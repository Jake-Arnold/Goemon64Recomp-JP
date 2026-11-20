#include "recomp_ui.h"
#include "goemon_config.h"
#include "goemon_support.h"
#include "goemon_launcher.h"
#include "librecomp/game.hpp"
#include "librecomp/mods.hpp"
#include "ultramodern/ultramodern.hpp"
#include "RmlUi/Core.h"
#include <SDL.h>
#include <chrono>
#include <future>
#include <utility>
#include <filesystem>
#ifdef GOEMON_JAPAN_NATIVE
#include "goemon_japan_build_id.h"
#endif

static std::string version_string;
static std::string launcher_version = GOEMON_LAUNCHER_VERSION;
static std::string update_status;
static bool update_available = false;
static bool update_pending = false;
static std::future<goemon64::launcher::UpdateResult> update_job;
using goemon64::launcher::Action;
#ifdef GOEMON_JAPAN_NATIVE
static constexpr bool native_is_japan = true;
#else
static constexpr bool native_is_japan = false;
#endif
static bool selected_japan = native_is_japan;
static bool native_rom_valid = false;
static bool experimental_roms = goemon64::launcher::experimental_rom_mode();
static bool usa_available = false;
static bool japan_available = false;
static Action startup_action = Action::None;

Rml::DataModelHandle model_handle;
bool mm_rom_valid = false;

extern std::vector<recomp::GameEntry> supported_games;

static bool rom_valid_for_edition(bool japan) {
    if (experimental_roms) {
        return goemon64::launcher::experimental_rom_selected(japan);
    }
#ifdef GOEMON_UNIFIED_REGIONS
    return goemon64::launcher::region_rom_valid(japan);
#else
    return japan == native_is_japan ? native_rom_valid : goemon64::launcher::region_rom_valid(japan);
#endif
}

static void select_edition(bool japan) {
    selected_japan = japan;
    mm_rom_valid = rom_valid_for_edition(japan);
    model_handle.DirtyVariable("selected_japan");
    model_handle.DirtyVariable("mm_rom_valid");
    goemon64::launcher::queue_window_title(japan);
}

static void select_rom() {
    const bool japan = selected_japan;
    goemon64::open_file_dialog([japan](bool success, const std::filesystem::path& path) {
        if (!success) {
            return;
        }
        std::error_code size_error;
        const auto size = std::filesystem::file_size(path, size_error);
        if (size_error || size < 64) {
            recompui::message_box("Could not read a complete N64 ROM header.");
            return;
        }
        const auto error = experimental_roms
            ? goemon64::launcher::import_experimental_rom(path, japan)
#ifdef GOEMON_UNIFIED_REGIONS
            : goemon64::launcher::import_region_rom(path, japan);
#else
            : japan == native_is_japan
                ? recomp::select_rom(path, supported_games[0].game_id)
                : goemon64::launcher::import_region_rom(path, japan);
#endif
        using Error = recomp::RomValidationError;
        switch (error) {
            case Error::Good:
                if (!experimental_roms && japan == native_is_japan) {
                    native_rom_valid = true;
                }
                if (selected_japan == japan) {
                    mm_rom_valid = true;
                    model_handle.DirtyVariable("mm_rom_valid");
                    goemon64::launcher::queue_window_title(japan);
                }
                break;
            case Error::FailedToOpen:
                recompui::message_box("Failed to open ROM file.");
                break;
            case Error::NotARom:
                recompui::message_box("This is not a valid N64 ROM file.");
                break;
            case Error::IncorrectRom:
            case Error::IncorrectVersion:
            case Error::NotYet:
                recompui::message_box(japan
                    ? "Select the original Japanese ROM. Patched ROMs need a compatible build."
                    : "Select the original USA ROM. Patched ROMs need a compatible build.");
                break;
            case Error::OtherError:
                recompui::message_box("Could not save the selected ROM.");
                break;
        }
    });
}

static void run_edition_action(Action action) {
    if (action == Action::None || (action == Action::Play && !mm_rom_valid)) {
        return;
    }
#ifdef GOEMON_UNIFIED_REGIONS
    if (action == Action::Play) {
        if (goemon64::launcher::start_unified_game(selected_japan)) {
            recompui::hide_all_contexts();
        } else {
            recompui::message_box("Could not open the selected ROM.");
        }
        return;
    }
    if (action == Action::Mods) {
        if (!goemon64::launcher::select_unified_profile(selected_japan)) {
            recompui::message_box("Could not open the selected edition's mod folder.");
            return;
        }
        recomp::mods::close_mods();
        recomp::register_config_path(goemon64::get_app_folder_path());
        recomp::mods::initialize_mods();
        recompui::update_mod_list();
    }
#else
    if (action == Action::Play && experimental_roms &&
        (selected_japan != native_is_japan || goemon64::launcher::experimental_profile_path().empty())) {
        if (goemon64::launcher::request_experimental_game(selected_japan)) {
            ultramodern::quit();
        } else {
            recompui::message_box("Could not start the selected edition.");
        }
        return;
    }
    if (selected_japan != native_is_japan) {
        if (goemon64::launcher::request_other_region(action)) {
            ultramodern::quit();
        } else {
            recompui::message_box("The other regional build was not found next to this executable.");
        }
        return;
    }
    if (action == Action::Play) {
        recomp::start_game(supported_games[0].game_id);
        recompui::hide_all_contexts();
        return;
    }
#endif
    const auto tab = action == Action::Controls ? recompui::ConfigTab::Controls
                   : action == Action::Mods ? recompui::ConfigTab::Mods
                   : recompui::ConfigTab::General;
    recompui::set_config_tab(tab);
    recompui::hide_all_contexts();
    recompui::show_context(recompui::get_config_context_id(), "");
}

void recompui::set_launcher_action(std::string_view argument) {
    startup_action = goemon64::launcher::action_from_argument(argument);
}

void recompui::update_launcher() {
    if (!recompui::is_context_shown(recompui::get_launcher_context_id())) {
        return;
    }
    run_edition_action(std::exchange(startup_action, Action::None));
    if (update_pending && update_job.wait_for(std::chrono::seconds(0)) == std::future_status::ready) {
        try {
            auto result = update_job.get();
            update_status = std::move(result.status);
            update_available = result.available;
        } catch (const std::exception&) {
            update_status = "Could not check for updates.";
        }
        update_pending = false;
        model_handle.DirtyVariable("update_status");
        model_handle.DirtyVariable("update_available");
        model_handle.DirtyVariable("update_pending");
    }
}

recompui::ContextId launcher_context;

recompui::ContextId recompui::get_launcher_context_id() {
	return launcher_context;
}

class LauncherMenu : public recompui::MenuController {
public:
    LauncherMenu() {
        native_rom_valid = recomp::is_rom_valid(supported_games[0].game_id);
#ifdef GOEMON_UNIFIED_REGIONS
        mm_rom_valid = rom_valid_for_edition(selected_japan);
        goemon64::launcher::queue_window_title(selected_japan);
        usa_available = true;
        japan_available = true;
#else
        mm_rom_valid = experimental_roms && goemon64::launcher::experimental_profile_path().empty()
            ? goemon64::launcher::experimental_rom_selected(native_is_japan)
            : native_rom_valid;
        const bool other_available = goemon64::launcher::other_region_available();
        usa_available = !native_is_japan || other_available;
        japan_available = native_is_japan || other_available;
#endif
    }
    ~LauncherMenu() override {

    }
    void load_document() override {
        launcher_context = recompui::create_context(goemon64::get_asset_path("launcher.rml"));
    }
    void register_events(recompui::UiEventListenerInstancer& listener) override {
        recompui::register_event(listener, "select_rom",
            [](const std::string& param, Rml::Event& event) {
                select_rom();
            }
        );
        recompui::register_event(listener, "start_game",
            [](const std::string&, Rml::Event&) { run_edition_action(Action::Play); }
        );
        recompui::register_event(listener, "select_usa",
            [](const std::string&, Rml::Event&) { if (usa_available) select_edition(false); }
        );
        recompui::register_event(listener, "select_japan",
            [](const std::string&, Rml::Event&) { if (japan_available) select_edition(true); }
        );
        recompui::register_event(listener, "check_updates",
            [](const std::string&, Rml::Event&) {
                if (update_pending) {
                    return;
                }
                update_available = false;
                try {
                    update_job = std::async(std::launch::async, goemon64::launcher::check_for_update);
                    update_pending = true;
                    update_status = "Checking for updates…";
                } catch (const std::exception&) {
                    update_status = "Could not start the update check.";
                }
                model_handle.DirtyVariable("update_status");
                model_handle.DirtyVariable("update_available");
                model_handle.DirtyVariable("update_pending");
            }
        );
        recompui::register_event(listener, "open_releases",
            [](const std::string&, Rml::Event&) {
                if (SDL_OpenURL(goemon64::launcher::releases_url) < 0) {
                    recompui::message_box("Could not open the releases page in your browser.");
                }
            }
        );
        recompui::register_event(listener, "open_controls",
            [](const std::string&, Rml::Event&) { run_edition_action(Action::Controls); }
        );
        recompui::register_event(listener, "open_settings",
            [](const std::string&, Rml::Event&) { run_edition_action(Action::Settings); }
        );
        recompui::register_event(listener, "open_mods",
            [](const std::string&, Rml::Event&) { run_edition_action(Action::Mods); }
        );
        recompui::register_event(listener, "exit_game",
            [](const std::string& param, Rml::Event& event) {
                ultramodern::quit();
            }
        );
    }
    void make_bindings(Rml::Context* context) override {
        Rml::DataModelConstructor constructor = context->CreateDataModel("launcher_model");

        constructor.Bind("mm_rom_valid", &mm_rom_valid);
        constructor.Bind("selected_japan", &selected_japan);
        constructor.Bind("experimental_roms", &experimental_roms);
        constructor.Bind("usa_available", &usa_available);
        constructor.Bind("japan_available", &japan_available);
        constructor.Bind("update_status", &update_status);
        constructor.Bind("update_available", &update_available);
        constructor.Bind("update_pending", &update_pending);

        version_string = recomp::get_project_version().to_string();
#ifdef GOEMON_JAPAN_NATIVE
        version_string += " (" + std::string(GOEMON_JP_BUILD_COMMIT) +
            (GOEMON_JP_BUILD_DIRTY ? "-dirty" : "") + ")";
#ifdef GOEMON_JP_TIMER_STAGE1_ACTIVE
        version_string += " [timer-stage1]";
#else
        version_string += " [timer-off]";
#endif
#ifdef GOEMON_JP_THREAD_STAGE2_ACTIVE
        version_string += " [threads-audit]";
#else
        version_string += " [threads-off]";
#endif
#ifdef GOEMON_JP_GUEST_STAGE3_ACTIVE
        version_string += " [guest-prep]";
#else
        version_string += " [guest-prep-off]";
#endif
#ifdef GOEMON_JP_QUEUE_STAGE4_ACTIVE
        version_string += " [queue-audit]";
#else
        version_string += " [queue-off]";
#endif
#ifdef GOEMON_JP_JOIN_STAGE5_ACTIVE
        version_string += " [join-prep]";
#else
        version_string += " [join-prep-off]";
#endif
#ifdef GOEMON_JP_EVENTS_STAGE6_ACTIVE
        version_string += " [native-events-audit]";
#else
        version_string += " [native-events-off]";
#endif
#ifdef GOEMON_JP_BOOT_STAGE7_ACTIVE
        version_string += " [boot-save-audio-audit]";
#else
        version_string += " [boot-save-audio-off]";
#endif
#ifdef GOEMON_JP_GRAPHICS_STAGE8_ACTIVE
        version_string += " [graphics-queue-audit]";
#else
        version_string += " [graphics-queue-off]";
#endif
#ifdef GOEMON_JP_EXIT_STAGE9_ACTIVE
        version_string += " [guest-exit-drain]";
#else
        version_string += " [guest-exit-off]";
#endif
#ifdef GOEMON_JP_ACTION_STAGE10_ACTIVE
        version_string += " [action-exit-drain]";
#else
        version_string += " [action-exit-off]";
#endif
#ifdef GOEMON_JP_LIVE_STAGE11_ACTIVE
        version_string += " [live-preflight]";
#else
        version_string += " [live-preflight-off]";
#endif
#ifdef GOEMON_JP_BARRIER_STAGE12_ACTIVE
        version_string += " [exit-barrier-audit]";
#else
        version_string += " [exit-barrier-off]";
#endif
#ifdef GOEMON_JP_GENERATION_STAGE13_ACTIVE
        version_string += " [generation-admission]";
#else
        version_string += " [generation-admission-off]";
#endif
#ifdef GOEMON_JP_CONFIG_FENCE_STAGE14_ACTIVE
        version_string += " [config-publisher-fence]";
#else
        version_string += " [config-publisher-fence-off]";
#endif
#ifdef GOEMON_JP_SDL_AUDIO_STAGE15_ACTIVE
        version_string += " [sdl-audio-exit]";
#else
        version_string += " [sdl-audio-exit-off]";
#endif
#ifdef GOEMON_JP_SAVE_STAGE16_ACTIVE
        version_string += " [save-finalize]";
#else
        version_string += " [save-finalize-off]";
#endif
#ifdef GOEMON_JP_TIMER_PUBLISH_STAGE17_ACTIVE
        version_string += " [timer-publisher-fence]";
#else
        version_string += " [timer-publisher-fence-off]";
#endif
#ifdef GOEMON_JP_TIMER_EPOCH_STAGE18_ACTIVE
        version_string += " [timer-epoch-clock]";
#else
        version_string += " [timer-epoch-off]";
#endif
#endif
        constructor.Bind("version_number", &version_string);
        constructor.Bind("launcher_version", &launcher_version);

        model_handle = constructor.GetModelHandle();
    }
};

std::unique_ptr<recompui::MenuController> recompui::create_launcher_menu() {
    return std::make_unique<LauncherMenu>();
}
