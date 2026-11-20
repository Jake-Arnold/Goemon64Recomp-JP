#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <string_view>

namespace recomp { enum class RomValidationError; }

namespace goemon64::launcher {
    inline constexpr char releases_url[] =
        "https://github.com/Jake-Arnold/Goemon64Recomp-JP/releases";

    struct UpdateResult {
        std::string status;
        bool available = false;
    };

    UpdateResult check_for_update();
    bool other_region_available();
    enum class Action { None, Play, Controls, Settings, Mods };
    Action action_from_argument(std::string_view argument);
    bool request_other_region(Action action);
    bool experimental_rom_mode();
    bool experimental_rom_selected(bool japan);
    recomp::RomValidationError import_experimental_rom(const std::filesystem::path& source, bool japan);
    bool request_experimental_game(bool japan);
    bool activate_experimental_rom(bool japan, std::uint64_t& rom_hash);
    const std::filesystem::path& experimental_profile_path();
    const std::string& experimental_window_title();
    void queue_window_title(bool japan);
    std::string take_window_title();
    bool region_rom_valid(bool japan);
    recomp::RomValidationError import_region_rom(const std::filesystem::path& source, bool japan);
#ifdef GOEMON_UNIFIED_REGIONS
    bool start_unified_game(bool japan);
    bool select_unified_profile(bool japan);
#endif
    // Called by main after the current runtime has shut down.
    bool launch_requested_region();
}
