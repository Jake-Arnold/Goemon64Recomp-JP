#include "goemon_launcher.h"
#include "goemon_config.h"
#include "goemon_rom_regions.hpp"

#define XXH_INLINE_ALL
#include "xxHash/xxhash.h"

#include <SDL.h>
#include "json/json.hpp"
#include "librecomp/game.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <limits>
#include <optional>
#include <fstream>
#include <random>
#include <vector>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <mutex>
#include <utility>

#ifdef _WIN32
#include <windows.h>
#else
#include <spawn.h>
#include <unistd.h>
#ifdef __APPLE__
#include <crt_externs.h>
#else
extern char** environ;
#endif
#endif

namespace goemon64::launcher {
namespace {
    std::mutex handoff_mutex;
    struct RegionRequest {
        std::filesystem::path executable;
        Action action = Action::None;
    };
    RegionRequest requested_region;
    std::filesystem::path active_profile;
    std::string active_rom_name;
    std::mutex title_mutex;
    std::string pending_title;

    constexpr auto rom_file(bool japan) {
        return japan ? u8"mnsg.jp.z64" : u8"mnsg.us.z64";
    }

    std::filesystem::path experiment_root(bool japan) {
        const auto base = get_app_folder_path(japan ? u8"Goemon64RecompiledJP" : u8"Goemon64Recompiled");
        return base.empty() ? std::filesystem::path{} :
            base / "experimental-roms" / (japan ? "jp" : "usa");
    }

    std::filesystem::path experiment_profile(const std::filesystem::path& root, std::string_view filename) {
        // Encode the basename as a safe folder key. Use only the basename so
        // moving the ROM between folders preserves the same save identity.
        return root / std::to_string(XXH3_64bits(filename.data(), filename.size()));
    }

    std::string read_rom_name(const std::filesystem::path& path) {
        if (path.empty()) {
            return {};
        }
        std::ifstream file(path, std::ios::binary | std::ios::ate);
        if (!file || file.tellg() <= 0 || file.tellg() > 1024) {
            return {};
        }
        std::string name(static_cast<std::size_t>(file.tellg()), '\0');
        if (!file.seekg(0).read(name.data(), name.size()) || name.find('\0') != std::string::npos) {
            return {};
        }
        return name;
    }

    std::string selected_experiment_name(const std::filesystem::path& root) {
        return root.empty() ? std::string{} : read_rom_name(root / "selected-rom");
    }

    constexpr std::array action_arguments{
        std::pair{Action::Play, "--launcher-play"},
        std::pair{Action::Controls, "--launcher-controls"},
        std::pair{Action::Settings, "--launcher-settings"},
        std::pair{Action::Mods, "--launcher-mods"},
    };

    constexpr auto rom_size = 16 * 1024 * 1024;

    bool read_rom(const std::filesystem::path& path, std::vector<char>& bytes, bool experimental = false) {
        std::ifstream file(path, std::ios::binary | std::ios::ate);
        const auto size = file.tellg();
        if (!file || size < 64 || size > 64 * 1024 * 1024 || (!experimental && size != rom_size)) {
            return false;
        }
        const auto length = static_cast<std::size_t>(size);
        bytes.resize((length + 3) & ~std::size_t{3});
        return static_cast<bool>(file.seekg(0).read(bytes.data(), length));
    }

    bool normalise_rom(std::vector<char>& bytes) {
        const std::string_view magic(bytes.data(), 4);
        std::size_t swap_width = 0;
        if (magic == "\x37\x80\x40\x12") {
            swap_width = 2;
        } else if (magic == "\x40\x12\x37\x80") {
            swap_width = 4;
        } else {
            return magic == "\x80\x37\x12\x40";
        }
        for (auto it = bytes.begin(); it != bytes.end(); it += swap_width) {
            std::reverse(it, it + swap_width);
        }
        return true;
    }

    bool replace_file(const std::filesystem::path& destination, std::string_view bytes) {
        if (destination.empty()) {
            return false;
        }
        const auto folder = destination.parent_path();
        std::error_code error;
        std::filesystem::create_directories(folder, error);
        if (error) {
            return false;
        }
        // Stage an import beside the destination so an incomplete write cannot
        // overwrite the previous ROM or selection.
        const auto staging = folder / (".rom-import-" + std::to_string(std::random_device{}()));
        if (!std::filesystem::create_directory(staging, error) || error) {
            return false;
        }
        const auto temp = staging / destination.filename();
        std::ofstream file(temp, std::ios::binary);
        file.write(bytes.data(), bytes.size());
        file.close();
        if (file) {
            std::filesystem::rename(temp, destination, error);
        }
        const bool success = file && !error;
        std::filesystem::remove(temp, error);
        std::filesystem::remove(staging, error);
        return success;
    }

    bool valid_stock_rom(const std::vector<char>& bytes, bool japan) {
        const auto& region = japan ? regions::japan_0 : regions::usa;
        return XXH3_64bits(bytes.data(), bytes.size()) == region.original_rom_xxh3_64;
    }

    std::filesystem::path region_folder(bool japan) {
        return get_app_folder_path(japan ? u8"Goemon64RecompiledJP" : u8"Goemon64Recompiled");
    }

    std::filesystem::path region_rom_path(bool japan) {
        const auto folder = region_folder(japan);
        return folder.empty() ? std::filesystem::path{} : folder / rom_file(japan);
    }

    std::filesystem::path region_executable(bool japan) {
        const std::unique_ptr<char, decltype(&SDL_free)> base(SDL_GetBasePath(), SDL_free);
        if (!base) {
            return {};
        }

        auto directory = std::filesystem::path(reinterpret_cast<const char8_t*>(base.get()));
        const char* name = japan ? "Goemon64RecompiledJP" : "Goemon64Recompiled";
#ifdef _WIN32
        auto path = directory / (std::string(name) + ".exe");
#else
        auto path = directory / name;
#endif
        std::error_code error;
        if (std::filesystem::is_regular_file(path, error)) {
            return path;
        }
#ifdef __APPLE__
        // SDL reports the resources directory when running in a bundle.
        if (directory.filename().empty()) {
            directory = directory.parent_path();
        }
        if (directory.filename() == "Resources") {
            const auto bundle_parent = directory.parent_path().parent_path().parent_path();
            path = bundle_parent / (std::string(name) + ".app") / "Contents" / "MacOS" / name;
            if (std::filesystem::is_regular_file(path, error)) {
                return path;
            }
        }
#endif
        return {};
    }
#ifdef GOEMON_JAPAN_NATIVE
    constexpr bool other_is_japan = false;
#else
    constexpr bool other_is_japan = true;
#endif

    bool can_launch(const std::filesystem::path& path) {
        if (path.empty()) {
            return false;
        }
#ifdef _WIN32
        return true;
#else
        return access(path.c_str(), X_OK) == 0;
#endif
    }
}

bool other_region_available() {
    return can_launch(region_executable(other_is_japan));
}

Action action_from_argument(std::string_view argument) {
    for (const auto& [action, flag] : action_arguments) {
        if (argument == flag) {
            return action;
        }
    }
    return Action::None;
}

bool region_rom_valid(bool japan) {
    std::vector<char> bytes;
    return read_rom(region_rom_path(japan), bytes) && valid_stock_rom(bytes, japan);
}

#ifdef GOEMON_UNIFIED_REGIONS
bool select_unified_profile(bool japan) {
    auto profile = region_folder(japan);
    if (profile.empty()) {
        return false;
    }
    if (experimental_rom_mode()) {
        profile /= "experimental-roms";
        profile /= "launcher";
        profile /= japan ? "jp" : "usa";
    }
    active_profile = std::move(profile);
    active_rom_name.clear();
    return true;
}
#endif

recomp::RomValidationError import_region_rom(const std::filesystem::path& source, bool japan) {
    using Error = recomp::RomValidationError;
    std::vector<char> bytes;
    if (!read_rom(source, bytes)) {
        return Error::IncorrectVersion;
    }
    if (!normalise_rom(bytes)) {
        return Error::NotARom;
    }
    if (!valid_stock_rom(bytes, japan)) {
        return Error::IncorrectVersion;
    }
    const auto destination = region_rom_path(japan);
    if (!replace_file(destination, {bytes.data(), bytes.size()})) {
        return Error::OtherError;
    }
    const auto name = source.filename().u8string();
    replace_file(destination.parent_path() / (japan ? "jp-rom-name" : "usa-rom-name"),
                 {reinterpret_cast<const char*>(name.data()), name.size()});
    return Error::Good;
}

bool experimental_rom_mode() {
    const char* flag = std::getenv("GOEMON_EXPERIMENTAL_ANY_ROM");
    return flag && std::string_view(flag) == "1";
}

bool experimental_rom_selected(bool japan) {
    const auto root = experiment_root(japan);
    const auto name = selected_experiment_name(root);
    std::error_code error;
    return !name.empty() && std::filesystem::is_regular_file(
        experiment_profile(root, name) / rom_file(japan), error);
}

recomp::RomValidationError import_experimental_rom(const std::filesystem::path& source, bool japan) {
    using Error = recomp::RomValidationError;
    if (!experimental_rom_mode()) {
        return Error::OtherError;
    }
    std::vector<char> bytes;
    if (!read_rom(source, bytes, true)) {
        return Error::FailedToOpen;
    }
    if (!normalise_rom(bytes)) {
        return Error::NotARom;
    }
    const auto utf8_name = source.filename().u8string();
    const std::string name(utf8_name.begin(), utf8_name.end());
    const auto root = experiment_root(japan);
    if (root.empty() || name.empty() || name.size() > 1024) {
        return Error::OtherError;
    }
    const auto profile = experiment_profile(root, name);
    if (!replace_file(profile / rom_file(japan), {bytes.data(), bytes.size()}) ||
        !replace_file(root / "selected-rom", name)) {
        return Error::OtherError;
    }
    return Error::Good;
}

bool activate_experimental_rom(bool japan, std::uint64_t& rom_hash) {
    if (!experimental_rom_mode()) {
        return false;
    }
    const auto root = experiment_root(japan);
    const auto name = selected_experiment_name(root);
    if (name.empty()) {
        return false;
    }
    const auto profile = experiment_profile(root, name);
    std::vector<char> bytes;
    if (!read_rom(profile / rom_file(japan), bytes, true) ||
        std::string_view(bytes.data(), 4) != "\x80\x37\x12\x40") {
        return false;
    }
    // Match the imported file rather than the stock revision. This leaves
    // the normal runtime's integrity check in place for the file it opens.
    rom_hash = XXH3_64bits(bytes.data(), bytes.size());
    active_profile = profile;
    active_rom_name = name;
    return true;
}

const std::filesystem::path& experimental_profile_path() {
    return active_profile;
}

const std::string& experimental_window_title() {
    return active_rom_name;
}

void queue_window_title(bool japan) {
    std::string title = japan ? "Goemon 64: Recompiled — Japan" : "Goemon 64: Recompiled — USA";
    std::string name;
    if (experimental_rom_mode()) {
        name = selected_experiment_name(experiment_root(japan));
    } else {
        const auto rom = region_rom_path(japan);
        std::error_code error;
        if (std::filesystem::is_regular_file(rom, error)) {
            name = read_rom_name(rom.parent_path() / (japan ? "jp-rom-name" : "usa-rom-name"));
            if (name.empty()) {
                name = rom.filename().string();
            }
        }
    }
    if (!name.empty()) {
        title += " — " + name;
    }
    std::lock_guard lock(title_mutex);
    pending_title = std::move(title);
}

std::string take_window_title() {
    std::lock_guard lock(title_mutex);
    return std::exchange(pending_title, {});
}

bool request_experimental_game(bool japan) {
    auto path = region_executable(japan);
    if (!experimental_rom_mode() || !can_launch(path)) {
        return false;
    }
    std::lock_guard lock(handoff_mutex);
    requested_region = {std::move(path), Action::Play};
    return true;
}

bool request_other_region(Action action) {
    auto path = region_executable(other_is_japan);
    if (!can_launch(path)) {
        return false;
    }
    std::lock_guard lock(handoff_mutex);
    requested_region = {std::move(path), action};
    return true;
}

bool launch_requested_region() {
    RegionRequest request;
    {
        std::lock_guard lock(handoff_mutex);
        request = std::exchange(requested_region, {});
    }
    const auto& path = request.executable;
    if (path.empty()) {
        return true;
    }
    std::string argument;
    for (const auto& [action, flag] : action_arguments) {
        if (request.action == action) {
            argument = flag;
            break;
        }
    }
#ifdef _WIN32
    auto command = L"\"" + path.wstring() + L"\" " + std::wstring(argument.begin(), argument.end());
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(path.c_str(), command.data(), nullptr, nullptr, FALSE, 0,
                        nullptr, nullptr, &startup, &process)) {
        return false;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return true;
#else
    auto program = path.native();
    char* argv[] = {program.data(), argument.data(), nullptr};
    pid_t pid{};
#ifdef __APPLE__
    auto* environment = *_NSGetEnviron();
#else
    auto* environment = environ;
#endif
    // The next edition must survive the current window and process exiting.
    posix_spawnattr_t attributes;
    if (posix_spawnattr_init(&attributes) != 0) {
        return false;
    }
    const bool started = posix_spawnattr_setpgroup(&attributes, 0) == 0 &&
        posix_spawnattr_setflags(&attributes, POSIX_SPAWN_SETPGROUP) == 0 &&
        posix_spawn(&pid, program.c_str(), nullptr, &attributes, argv, environment) == 0;
    posix_spawnattr_destroy(&attributes);
    return started;
#endif
}

UpdateResult check_for_update() {
    // The command is fixed. ROM paths, version strings and server data are never
    // passed to the shell. --disable ignores user curl configuration.
    constexpr char options[] =
        " --disable --silent --max-time 6 --max-filesize 524288 --proto =https"
        " --user-agent Goemon64Recomp-JP --header \"Accept: application/vnd.github+json\""
        " --write-out \"\\n%{http_code}\""
        " https://api.github.com/repos/Jake-Arnold/Goemon64Recomp-JP/releases?per_page=20";
#ifdef _WIN32
    const auto command = std::string("\"%SystemRoot%\\System32\\curl.exe\"") + options + " 2>NUL";
    auto* pipe = _popen(command.c_str(), "rb");
    const auto close_pipe = _pclose;
#else
    const auto command = std::string("/usr/bin/curl") + options + " 2>/dev/null";
    auto* pipe = popen(command.c_str(), "r");
    const auto close_pipe = pclose;
#endif
    if (!pipe) {
        return {"Could not start the update check."};
    }
    constexpr std::size_t max_response = 524288 + 4;
    std::array<char, 4096> buffer{};
    std::string response;
    bool overflow = false;
    while (const auto count = std::fread(buffer.data(), 1, buffer.size(), pipe)) {
        if (count > max_response - response.size()) {
            overflow = true;
        } else if (!overflow) {
            response.append(buffer.data(), count);
        }
    }
    const bool read_failed = std::ferror(pipe);
    if (close_pipe(pipe) != 0 || overflow || read_failed) {
        return {"Could not check for updates. Try again later."};
    }

    if (!response.ends_with("\n200")) {
        return {"GitHub could not complete the update check."};
    }
    response.resize(response.size() - 4);
    const auto releases = nlohmann::json::parse(response, nullptr, false);
    if (!releases.is_array()) {
        return {"GitHub returned an invalid release response."};
    }

    struct ReleaseVersion {
        recomp::Version version;
        int stage; // Stable sorts after any test build with the same version.
    };
    const auto parse_release = [](std::string tag) -> std::optional<ReleaseVersion> {
        if (tag.starts_with('v')) {
            tag.erase(0, 1);
        }
        recomp::Version parsed;
        if (!recomp::Version::from_string(tag, parsed)) {
            return std::nullopt;
        }
        if (parsed.suffix.empty()) {
            return ReleaseVersion{parsed, std::numeric_limits<int>::max()};
        }
        if (parsed.suffix == "-dev") {
            return ReleaseVersion{parsed, 0};
        }
        constexpr std::string_view prefix = "-test.";
        if (!parsed.suffix.starts_with(prefix)) {
            return std::nullopt;
        }
        const auto count = std::string_view(parsed.suffix).substr(prefix.size());
        int stage = 0;
        const auto result = std::from_chars(count.data(), count.data() + count.size(), stage);
        if (result.ec != std::errc{} || result.ptr != count.data() + count.size() ||
            stage <= 0 || stage == std::numeric_limits<int>::max()) {
            return std::nullopt;
        }
        return ReleaseVersion{parsed, stage};
    };
    const auto newer = [](const ReleaseVersion& a, const ReleaseVersion& b) {
        const auto comparison = a.version <=> b.version;
        return comparison > 0 || (comparison == 0 && a.stage > b.stage);
    };

    const auto installed = parse_release(GOEMON_LAUNCHER_VERSION);
    if (!installed) {
        return {"The installed version could not be compared."};
    }
    std::optional<ReleaseVersion> latest;
    for (const auto& release : releases) {
        if (!release.is_object() || !release.contains("draft") ||
            !release["draft"].is_boolean() || release["draft"].get<bool>() ||
            !release.contains("tag_name") || !release["tag_name"].is_string()) {
            continue;
        }
        const auto candidate = parse_release(release["tag_name"].get<std::string>());
        if (candidate && (!latest || newer(*candidate, *latest))) {
            latest = candidate;
        }
    }
    if (!latest) {
        return {"No compatible release has been published yet."};
    }
    if (newer(*latest, *installed)) {
        return {"Version " + latest->version.to_string() + " is available.", true};
    }
    return {"You have the latest release or newer."};
}

}
