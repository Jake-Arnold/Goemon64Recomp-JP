#include <atomic>
#include <bit>
#include <cstdio>
#include <mutex>
#include <utility>

#include "ultramodern/ultramodern.hpp"
#include "recomp.h"
#include "recomp_input.h"
#include "goemon_config.h"
#include "recomp_ui.h"
#include "SDL.h"
#include "promptfont.h"
#include "GamepadMotion.hpp"
#ifdef GOEMON_JP_GENERATION_STAGE13_ACTIVE
#include "jp_generation_coordinator.hpp"
#endif

#ifdef GOEMON_JP_THREAD_STAGE2_ACTIVE
namespace ultramodern { void report_guest_thread_reset_blockers(); }
#endif
#ifdef GOEMON_JP_GUEST_STAGE3_ACTIVE
namespace ultramodern { void report_guest_shutdown_readiness(); }
#endif
#ifdef GOEMON_JP_QUEUE_STAGE4_ACTIVE
namespace ultramodern { void report_guest_message_queue_waits(); }
#endif
#ifdef GOEMON_JP_JOIN_STAGE5_ACTIVE
namespace ultramodern { void report_guest_join_barrier_readiness(); }
#endif
#ifdef GOEMON_JP_EVENTS_STAGE6_ACTIVE
namespace ultramodern { void jp_report_native_event_worker_blockers(); }
#endif
#ifdef GOEMON_JP_GRAPHICS_STAGE8_ACTIVE
namespace ultramodern { void jp_report_graphics_queue_settlement(); }
#endif
#ifdef GOEMON_JP_BOOT_STAGE7_ACTIVE
namespace recomp { void jp_report_native_boot_lifecycle(); }
namespace ultramodern {
    void jp_report_save_worker_lifecycle();
    void jp_report_audio_dispatch_lifecycle();
}
void jp_report_sdl_audio_queue();
#endif
#ifdef GOEMON_JP_LIVE_STAGE11_ACTIVE
namespace ultramodern { void jp_report_live_generation_preflight(); }
#endif
#ifdef GOEMON_JP_CONFIG_FENCE_STAGE14_ACTIVE
namespace ultramodern { void jp_report_config_action_producer_gate(); }
#endif
#ifdef GOEMON_JP_TIMER_PUBLISH_STAGE17_ACTIVE
namespace ultramodern { void jp_report_timer_producer_stage17(); }
#endif
#ifdef GOEMON_JP_TIMER_EPOCH_STAGE18_ACTIVE
namespace ultramodern { void jp_report_timer_epoch_stage18(); }
#endif
#ifdef GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE
#include "jp_guest_checkpoint_status.hpp"
#include "jp_external_message_queue.hpp"
namespace recomp { bool jp_request_integrated_reset(); }
#endif
#ifdef GOEMON_JP_NATIVE_F6_PAUSE_ACTIVE
#include "jp_pause_controller.hpp"
#include "jp_audio_checkpoint.hpp"
#include "jp_timer_checkpoint.hpp"
#include "jp_video_checkpoint.hpp"
namespace ultramodern {
    bool jp_set_guest_paused(bool);
    bool jp_guest_is_paused();
}
namespace recomp {
    void jp_set_audio_paused(bool);
    bool jp_request_integrated_reset();
}
#endif

constexpr float axis_threshold = 0.5f;

struct ControllerState {
    SDL_GameController* controller;
    std::array<float, 3> latest_accelerometer;
    GamepadMotion motion;
    uint32_t prev_gyro_timestamp;
    ControllerState() : controller{}, latest_accelerometer{}, motion{}, prev_gyro_timestamp{} {
        motion.Reset();
        motion.SetCalibrationMode(GamepadMotionHelpers::CalibrationMode::Stillness | GamepadMotionHelpers::CalibrationMode::SensorFusion);
    };
};

static struct {
    const Uint8* keys = nullptr;
    SDL_Keymod keymod = SDL_Keymod::KMOD_NONE;
    int numkeys = 0;
    std::atomic_int32_t mouse_wheel_pos = 0;
    std::mutex cur_controllers_mutex;
    std::vector<SDL_GameController*> cur_controllers{};
    std::unordered_map<SDL_JoystickID, ControllerState> controller_states;
    
    std::array<float, 2> rotation_delta{};
    std::array<float, 2> mouse_delta{};
    std::mutex pending_input_mutex;
    std::array<float, 2> pending_rotation_delta{};
    std::array<float, 2> pending_mouse_delta{};

    float cur_rumble;
    bool rumble_active;
} InputState;

static struct {
    std::list<std::filesystem::path> files_dropped;
} DropState;

std::atomic<recomp::InputDevice> scanning_device = recomp::InputDevice::COUNT;
std::atomic<recomp::InputField> scanned_input;

enum class InputType {
    None = 0, // Using zero for None ensures that default initialized InputFields are unbound.
    Keyboard,
    Mouse,
    ControllerDigital,
    ControllerAnalog // Axis input_id values are the SDL value + 1
};

void set_scanned_input(recomp::InputField value) {
    scanning_device.store(recomp::InputDevice::COUNT);
    scanned_input.store(value);
}

recomp::InputField recomp::get_scanned_input() {
    recomp::InputField ret = scanned_input.load();
    scanned_input.store({});
    return ret;
}

void recomp::start_scanning_input(recomp::InputDevice device) {
    scanned_input.store({});
    scanning_device.store(device);
}

void recomp::stop_scanning_input() {
    scanning_device.store(recomp::InputDevice::COUNT);
}

void queue_if_enabled(SDL_Event* event) {
    if (!recomp::all_input_disabled()) {
        recompui::queue_event(*event);
    }
}

static std::atomic_bool cursor_enabled = true;

void recompui::set_cursor_visible(bool visible) {
    cursor_enabled.store(visible);
}

bool should_override_keystate(SDL_Scancode key, SDL_Keymod mod) {
    // Override Enter when Alt is held.
    if (key == SDL_Scancode::SDL_SCANCODE_RETURN) {
        if (mod & SDL_Keymod::KMOD_ALT) {
            return true;
        }
    }

    return false;        
}

static constexpr int save_slot_count = 10;
static std::atomic_int save_slot{0};

int recomp::get_save_slot() {
    return save_slot.load() + 1;
}

static std::mutex save_state_status_mutex;
static std::string pending_save_state_status;

std::string recomp::take_save_state_status() {
    std::lock_guard lock(save_state_status_mutex);
    return std::exchange(pending_save_state_status, {});
}

#ifdef GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE
static void preview_save_state_action(bool save) {
    std::string status = save ? "Save unavailable" : "Load unavailable";
    if (save) {
        const auto coverage = ultramodern::jp_guest_checkpoint_coverage();
        const auto ready = coverage.not_started + coverage.message_checkpoints;
        status += " (" + std::to_string(ready) + "/" + std::to_string(coverage.threads) +
            " guest threads covered";
        if (ready == coverage.threads) {
            status += ", runtime snapshot incomplete";
        }
        status += ")";
        std::fprintf(stderr, "[Native save-state preview] slot=%d %s; waiting=%u unstarted=%u\n",
                     recomp::get_save_slot(), status.c_str(), coverage.message_checkpoints, coverage.not_started);
        const auto external = ultramodern::jp_snapshot_external_messages();
        const auto pending = external ? std::to_string(external->size()) : std::string("delivery in progress");
#ifdef GOEMON_JP_NATIVE_F6_PAUSE_ACTIVE
        const auto parked = goemon_jp::pause::controller().parked_workers();
        const auto dispatch_mask = goemon_jp::pause::native_dispatch_mask;
        const auto idle_flag = static_cast<std::uint32_t>(goemon_jp::pause::PauseWorker::IdleGuest);
        std::fprintf(stderr, "[Native save-state preview] dispatch_workers_parked=%d/%d idle_parked=%d external_queue=%s\n",
                     std::popcount(parked & dispatch_mask), std::popcount(dispatch_mask),
                     (parked & idle_flag) != 0, pending.c_str());
        if (const auto timer = ultramodern::jp_snapshot_timers()) {
            std::fprintf(stderr, "[Native save-state preview] timer_snapshot=ready active=%zu pending=%zu\n",
                         timer->active.size(), timer->pending.size());
        } else {
            std::fprintf(stderr, "[Native save-state preview] timer_snapshot=unavailable\n");
        }
        if (const auto video = ultramodern::jp_snapshot_video()) {
            std::fprintf(stderr, "[Native save-state preview] video_snapshot=ready origin=%08x retraces=%d\n",
                         video->presented_registers.VI_ORIGIN_REG, video->remaining_retraces);
        } else {
            std::fprintf(stderr, "[Native save-state preview] video_snapshot=unavailable\n");
        }
        if (const auto audio = ultramodern::jp_snapshot_audio()) {
            std::fprintf(stderr, "[Native save-state preview] audio_snapshot=ready rate=%u buffered_samples=%zu\n",
                         audio->sample_rate, audio->pending_output.size());
        } else {
            std::fprintf(stderr, "[Native save-state preview] audio_snapshot=unavailable\n");
        }
#else
        std::fprintf(stderr, "[Native save-state preview] external_queue=%s\n", pending.c_str());
#endif
    } else {
        status += " (native restore incomplete)";
        std::fprintf(stderr, "[Native save-state preview] slot=%d %s\n", recomp::get_save_slot(), status.c_str());
    }
    std::lock_guard lock(save_state_status_mutex);
    pending_save_state_status = std::move(status);
}
#endif

static bool handle_save_state_binding(recomp::InputDevice device, InputType type, int32_t button) {
    if (!ultramodern::is_game_started() || recomp::all_input_disabled() || recomp::game_input_disabled()) {
        return false;
    }

    constexpr std::pair<recomp::GameInput, int> actions[] = {
        { recomp::GameInput::PREVIOUS_SAVE_SLOT, -1 },
        { recomp::GameInput::NEXT_SAVE_SLOT, 1 },
#ifdef GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE
        { recomp::GameInput::SAVE_STATE, 0 },
        { recomp::GameInput::LOAD_STATE, 0 },
#endif
    };
    for (auto [action, direction] : actions) {
        for (size_t slot = 0; slot < recomp::bindings_per_input; ++slot) {
            const auto binding = recomp::get_input_binding(action, slot, device);
            if (binding == recomp::InputField{static_cast<uint32_t>(type), button}) {
#ifdef GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE
                if (direction == 0) {
                    preview_save_state_action(action == recomp::GameInput::SAVE_STATE);
                    return true;
                }
#endif
                int current = save_slot.load();
                while (!save_slot.compare_exchange_weak(
                    current, (current + direction + save_slot_count) % save_slot_count)) {}
                return true;
            }
        }
    }
    return false;
}

#ifdef GOEMON_JP_NATIVE_F6_PAUSE_ACTIVE
static bool handle_pause_binding(recomp::InputDevice device, InputType type, int32_t input) {
    if (scanning_device != recomp::InputDevice::COUNT) {
        return false;
    }

    const bool paused = ultramodern::jp_guest_is_paused();
    if (!paused && (!ultramodern::is_game_started() ||
                    recomp::all_input_disabled() || recompui::is_context_capturing_input())) {
        return false;
    }

    for (size_t slot = 0; slot < recomp::bindings_per_input; ++slot) {
        const auto& binding = recomp::get_input_binding(recomp::GameInput::PAUSE_GAME, slot, device);
        if (binding.input_type == static_cast<uint32_t>(type) && binding.input_id == input) {
            if (ultramodern::jp_set_guest_paused(!paused)) {
                recomp::jp_set_audio_paused(!paused);
            }
            return true;
        }
    }
    return false;
}
#endif

bool sdl_event_filter(void* userdata, SDL_Event* event) {
    switch (event->type) {
    case SDL_EventType::SDL_KEYDOWN:
        {
            SDL_KeyboardEvent* keyevent = &event->key;
#ifdef GOEMON_JP_NATIVE_F6_PAUSE_ACTIVE
            // The guest stops polling inputs while paused. Handle the
            // configured key on the host SDL event pump instead.
            if (!keyevent->repeat &&
                handle_pause_binding(recomp::InputDevice::Keyboard, InputType::Keyboard,
                                     keyevent->keysym.scancode)) {
                return false; // Host hotkey is not a guest/UI key.
            }
            // A paused guest no longer polls F8 bindings. Let the same
            // independent SDL event pump request the verified F8 reset.
            if (!keyevent->repeat &&
                keyevent->keysym.scancode == SDL_SCANCODE_F8 &&
                ultramodern::jp_guest_is_paused()) {
                (void)ultramodern::jp_set_guest_paused(false);
                recomp::jp_set_audio_paused(false);
                (void)recomp::jp_request_integrated_reset();
                return false;
            }
#endif
            if (!keyevent->repeat &&
                handle_save_state_binding(recomp::InputDevice::Keyboard, InputType::Keyboard,
                                         keyevent->keysym.scancode)) {
                return false;
            }

            // Skip repeated events when not in the menu
            if (!recompui::is_context_capturing_input() &&
                event->key.repeat) {
                break;
            }

            if ((keyevent->keysym.scancode == SDL_Scancode::SDL_SCANCODE_RETURN && (keyevent->keysym.mod & SDL_Keymod::KMOD_ALT)) ||
                keyevent->keysym.scancode == SDL_Scancode::SDL_SCANCODE_F11
            ) {
                recompui::toggle_fullscreen();
            }
            if (scanning_device != recomp::InputDevice::COUNT) {
                if (keyevent->keysym.scancode == SDL_Scancode::SDL_SCANCODE_ESCAPE) {
                    recomp::cancel_scanning_input();
                } else if (scanning_device == recomp::InputDevice::Keyboard) {
                    set_scanned_input({(uint32_t)InputType::Keyboard, keyevent->keysym.scancode});
                }
            } else {
                if (!should_override_keystate(keyevent->keysym.scancode, static_cast<SDL_Keymod>(keyevent->keysym.mod))) {
                    queue_if_enabled(event);
                }
            }
        }
        break;
    case SDL_EventType::SDL_CONTROLLERDEVICEADDED:
        {
            SDL_ControllerDeviceEvent* controller_event = &event->cdevice;
            SDL_GameController* controller = SDL_GameControllerOpen(controller_event->which);
            printf("Controller added: %d\n", controller_event->which);
            if (controller != nullptr) {
                printf("  Instance ID: %d\n", SDL_JoystickInstanceID(SDL_GameControllerGetJoystick(controller)));
                ControllerState& state = InputState.controller_states[SDL_JoystickInstanceID(SDL_GameControllerGetJoystick(controller))];
                state.controller = controller;

                if (SDL_GameControllerHasSensor(controller, SDL_SensorType::SDL_SENSOR_GYRO) && SDL_GameControllerHasSensor(controller, SDL_SensorType::SDL_SENSOR_ACCEL)) {
                    SDL_GameControllerSetSensorEnabled(controller, SDL_SensorType::SDL_SENSOR_GYRO, SDL_TRUE);
                    SDL_GameControllerSetSensorEnabled(controller, SDL_SensorType::SDL_SENSOR_ACCEL, SDL_TRUE);
                }
            }
        }
        break;
    case SDL_EventType::SDL_CONTROLLERDEVICEREMOVED:
        {
            SDL_ControllerDeviceEvent* controller_event = &event->cdevice;
            printf("Controller removed: %d\n", controller_event->which);
            InputState.controller_states.erase(controller_event->which);
        }
        break;
    case SDL_EventType::SDL_QUIT: {
        if (!ultramodern::is_game_started()) {
            ultramodern::quit();
            return true;
        }

        goemon64::open_quit_game_prompt();
        recompui::activate_mouse();
        break;
    }
    case SDL_EventType::SDL_MOUSEWHEEL:
        {
            SDL_MouseWheelEvent* wheel_event = &event->wheel;    
            InputState.mouse_wheel_pos.fetch_add(wheel_event->y * (wheel_event->direction == SDL_MOUSEWHEEL_FLIPPED ? -1 : 1));
        }
        queue_if_enabled(event);
        break;
    case SDL_EventType::SDL_CONTROLLERBUTTONDOWN:
#ifdef GOEMON_JP_NATIVE_F6_PAUSE_ACTIVE
        if (handle_pause_binding(recomp::InputDevice::Controller, InputType::ControllerDigital,
                                 event->cbutton.button)) {
            return false;
        }
#endif
        if (handle_save_state_binding(recomp::InputDevice::Controller, InputType::ControllerDigital,
                                     event->cbutton.button)) {
            return false;
        }
        if (scanning_device != recomp::InputDevice::COUNT) {
            auto menuToggleBinding0 = recomp::get_input_binding(recomp::GameInput::TOGGLE_MENU, 0, recomp::InputDevice::Controller);
            auto menuToggleBinding1 = recomp::get_input_binding(recomp::GameInput::TOGGLE_MENU, 1, recomp::InputDevice::Controller);
            // note - magic number: 0 is InputType::None
            if ((menuToggleBinding0.input_type != 0 && event->cbutton.button == menuToggleBinding0.input_id) ||
                (menuToggleBinding1.input_type != 0 && event->cbutton.button == menuToggleBinding1.input_id)) {
                recomp::cancel_scanning_input();
            } else if (scanning_device == recomp::InputDevice::Controller) {
                SDL_ControllerButtonEvent* button_event = &event->cbutton;
                auto scanned_input_index = recomp::get_scanned_input_index();
                if ((scanned_input_index == static_cast<int>(recomp::GameInput::TOGGLE_MENU) ||
                     scanned_input_index == static_cast<int>(recomp::GameInput::ACCEPT_MENU) ||
                     scanned_input_index == static_cast<int>(recomp::GameInput::APPLY_MENU)) && (
                     button_event->button == SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_DPAD_UP ||
                     button_event->button == SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_DPAD_DOWN ||
                     button_event->button == SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_DPAD_LEFT ||
                     button_event->button == SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_DPAD_RIGHT)) {
                    break;
                }

                set_scanned_input({(uint32_t)InputType::ControllerDigital, button_event->button});
            }
        } else {
            queue_if_enabled(event);
        }
        break;
    case SDL_EventType::SDL_CONTROLLERAXISMOTION:
        if (scanning_device == recomp::InputDevice::Controller) {
            auto scanned_input_index = recomp::get_scanned_input_index();
#ifdef GOEMON_JP_NATIVE_F6_PAUSE_ACTIVE
            // A host pause binding must be a key or a controller button.
            if (scanned_input_index == static_cast<int>(recomp::GameInput::PAUSE_GAME)) {
                break;
            }
#endif
#ifdef GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE
            if (scanned_input_index == static_cast<int>(recomp::GameInput::SAVE_STATE) ||
                scanned_input_index == static_cast<int>(recomp::GameInput::LOAD_STATE)) {
                break;
            }
#endif
            if (scanned_input_index == static_cast<int>(recomp::GameInput::PREVIOUS_SAVE_SLOT) ||
                scanned_input_index == static_cast<int>(recomp::GameInput::NEXT_SAVE_SLOT) ||
                scanned_input_index == static_cast<int>(recomp::GameInput::TOGGLE_MENU) ||
                scanned_input_index == static_cast<int>(recomp::GameInput::ACCEPT_MENU) ||
                scanned_input_index == static_cast<int>(recomp::GameInput::APPLY_MENU)) {
                break;
            }

            SDL_ControllerAxisEvent* axis_event = &event->caxis;
            float axis_value = axis_event->value * (1/32768.0f);
            if (axis_value > axis_threshold) {
                SDL_Event set_stick_return_event;
                set_stick_return_event.type = SDL_USEREVENT;
                set_stick_return_event.user.code = axis_event->axis;
                set_stick_return_event.user.data1 = nullptr;
                set_stick_return_event.user.data2 = nullptr;
                recompui::queue_event(set_stick_return_event);
                
                set_scanned_input({(uint32_t)InputType::ControllerAnalog, axis_event->axis + 1});
            }
            else if (axis_value < -axis_threshold) {
                SDL_Event set_stick_return_event;
                set_stick_return_event.type = SDL_USEREVENT;
                set_stick_return_event.user.code = axis_event->axis;
                set_stick_return_event.user.data1 = nullptr;
                set_stick_return_event.user.data2 = nullptr;
                recompui::queue_event(set_stick_return_event);

                set_scanned_input({(uint32_t)InputType::ControllerAnalog, -axis_event->axis - 1});
            }
        } else {
            queue_if_enabled(event);
        }
        break;
    case SDL_EventType::SDL_CONTROLLERSENSORUPDATE:
        if (event->csensor.sensor == SDL_SensorType::SDL_SENSOR_ACCEL) {
            // Convert acceleration to g's.
            float x = event->csensor.data[0] / SDL_STANDARD_GRAVITY;
            float y = event->csensor.data[1] / SDL_STANDARD_GRAVITY;
            float z = event->csensor.data[2] / SDL_STANDARD_GRAVITY;
            ControllerState& state = InputState.controller_states[event->csensor.which];
            state.latest_accelerometer[0] = x;
            state.latest_accelerometer[1] = y;
            state.latest_accelerometer[2] = z;
        }
        else if (event->csensor.sensor == SDL_SensorType::SDL_SENSOR_GYRO) {
            // constexpr float gyro_threshold = 0.05f;
            // Convert rotational velocity to degrees per second.
            constexpr float rad_to_deg = 180.0f / M_PI;
            float x = event->csensor.data[0] * rad_to_deg;
            float y = event->csensor.data[1] * rad_to_deg;
            float z = event->csensor.data[2] * rad_to_deg;
            ControllerState& state = InputState.controller_states[event->csensor.which];
            uint64_t cur_timestamp = event->csensor.timestamp;
            uint32_t delta_ms = cur_timestamp - state.prev_gyro_timestamp;
            state.motion.ProcessMotion(x, y, z, state.latest_accelerometer[0], state.latest_accelerometer[1], state.latest_accelerometer[2], delta_ms * 0.001f);
            state.prev_gyro_timestamp = cur_timestamp;

            float rot_x = 0.0f;
            float rot_y = 0.0f;
            state.motion.GetPlayerSpaceGyro(rot_x, rot_y);

            {
                std::lock_guard lock{ InputState.pending_input_mutex };
                InputState.pending_rotation_delta[0] += rot_x;
                InputState.pending_rotation_delta[1] += rot_y;
            }
        }
        break;
    case SDL_EventType::SDL_MOUSEMOTION:
        if (!recomp::game_input_disabled()) {
            SDL_MouseMotionEvent* motion_event = &event->motion;
            std::lock_guard lock{ InputState.pending_input_mutex };
            InputState.pending_mouse_delta[0] += motion_event->xrel;
            InputState.pending_mouse_delta[1] += motion_event->yrel;
        }
        queue_if_enabled(event);
        break;
    case SDL_EventType::SDL_DROPBEGIN:
        DropState.files_dropped.clear();
        break;
    case SDL_EventType::SDL_DROPFILE:
        DropState.files_dropped.emplace_back(std::filesystem::path(std::u8string_view((const char8_t *)(event->drop.file))));
        SDL_free(event->drop.file);
        break;
    case SDL_EventType::SDL_DROPCOMPLETE:
        recompui::drop_files(DropState.files_dropped);
        break;
    case SDL_EventType::SDL_CONTROLLERBUTTONUP:
        // Always queue button up events to avoid missing them during binding.
        recompui::queue_event(*event);
        break;
    default:
        queue_if_enabled(event);
        break;
    }
    return false;
}

void recomp::handle_events() {
    SDL_Event cur_event;
    static bool started = false;
    static bool exited = false;
    while (SDL_PollEvent(&cur_event) && !exited) {
        exited = sdl_event_filter(nullptr, &cur_event);

        // Lock the cursor if all three conditions are true: mouse aiming is enabled, game input is not disabled, and the game has been started. 
        bool cursor_locked = (recomp::get_mouse_sensitivity() != 0) && !recomp::game_input_disabled() && ultramodern::is_game_started();

        // Hide the cursor based on its enable state, but override visibility to false if the cursor is locked.
        bool cursor_visible = cursor_enabled;
        if (cursor_locked) {
            cursor_visible = false;
        }

        SDL_ShowCursor(cursor_visible ? SDL_ENABLE : SDL_DISABLE);
        SDL_SetRelativeMouseMode(cursor_locked ? SDL_TRUE : SDL_FALSE);
    }

    if (!started && ultramodern::is_game_started()) {
        started = true;
        recompui::process_game_started();
    }
}

constexpr SDL_GameControllerButton SDL_CONTROLLER_BUTTON_SOUTH = SDL_CONTROLLER_BUTTON_A;
constexpr SDL_GameControllerButton SDL_CONTROLLER_BUTTON_EAST = SDL_CONTROLLER_BUTTON_B;
constexpr SDL_GameControllerButton SDL_CONTROLLER_BUTTON_WEST = SDL_CONTROLLER_BUTTON_X;
constexpr SDL_GameControllerButton SDL_CONTROLLER_BUTTON_NORTH = SDL_CONTROLLER_BUTTON_Y;

const recomp::DefaultN64Mappings recomp::default_n64_keyboard_mappings = {
    .a = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_SPACE}
    },
    .b = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_LSHIFT}
    },
    .l = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_E}
    },
    .r = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_R}
    },
    .z = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_Q}
    },
    .start = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_RETURN}
    },
    .c_left = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_LEFT}
    },
    .c_right = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_RIGHT}
    },
    .c_up = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_UP}
    },
    .c_down = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_DOWN}
    },
    .dpad_left = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_J}
    },
    .dpad_right = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_L}
    },
    .dpad_up = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_I}
    },
    .dpad_down = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_K}
    },
    .analog_left = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_A}
    },
    .analog_right = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_D}
    },
    .analog_up = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_W}
    },
    .analog_down = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_S}
    },
    .toggle_menu = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_ESCAPE}
    },
    .accept_menu = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_RETURN}
    },
    .apply_menu = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_F}
    },
#if defined(GOEMON_JAPAN_NATIVE) || defined(GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE)
    .reset_game = {
        {.input_type = (uint32_t)InputType::Keyboard, .input_id = SDL_SCANCODE_F8}
    },
#endif
#ifdef GOEMON_JP_NATIVE_F6_PAUSE_ACTIVE
    .pause_game = {
        {.input_type = static_cast<uint32_t>(InputType::Keyboard), .input_id = SDL_SCANCODE_F6}
    },
#endif
    .previous_save_slot = {
        {.input_type = static_cast<uint32_t>(InputType::Keyboard), .input_id = SDL_SCANCODE_F9}
    },
    .next_save_slot = {
        {.input_type = static_cast<uint32_t>(InputType::Keyboard), .input_id = SDL_SCANCODE_F10}
    },
#ifdef GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE
    .save_state = {
        {.input_type = static_cast<uint32_t>(InputType::Keyboard), .input_id = SDL_SCANCODE_F5}
    },
    .load_state = {
        {.input_type = static_cast<uint32_t>(InputType::Keyboard), .input_id = SDL_SCANCODE_F7}
    },
#endif
};

const recomp::DefaultN64Mappings recomp::default_n64_controller_mappings = {
    .a = {
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_SOUTH},
    },
    .b = {
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_WEST},
    },
    .l = {
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_LEFTSHOULDER},
    },
    .r = {
        {.input_type = (uint32_t)InputType::ControllerAnalog, .input_id = SDL_CONTROLLER_AXIS_TRIGGERRIGHT + 1},
    },
    .z = {
        {.input_type = (uint32_t)InputType::ControllerAnalog, .input_id = SDL_CONTROLLER_AXIS_TRIGGERLEFT + 1},
    },
    .start = {
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_START},
    },
    .c_left = {
        {.input_type = (uint32_t)InputType::ControllerAnalog, .input_id = -(SDL_CONTROLLER_AXIS_RIGHTX + 1)},
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_NORTH},
    },
    .c_right = {
        {.input_type = (uint32_t)InputType::ControllerAnalog, .input_id = SDL_CONTROLLER_AXIS_RIGHTX + 1},
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_EAST},
    },
    .c_up = {
        {.input_type = (uint32_t)InputType::ControllerAnalog, .input_id = -(SDL_CONTROLLER_AXIS_RIGHTY + 1)},
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_RIGHTSTICK},
    },
    .c_down = {
        {.input_type = (uint32_t)InputType::ControllerAnalog, .input_id = SDL_CONTROLLER_AXIS_RIGHTY + 1},
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_RIGHTSHOULDER},
    },
    .dpad_left = {
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_DPAD_LEFT},
    },
    .dpad_right = {
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_DPAD_RIGHT},
    },
    .dpad_up = {
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_DPAD_UP},
    },
    .dpad_down = {
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_DPAD_DOWN},
    },
    .analog_left = {
        {.input_type = (uint32_t)InputType::ControllerAnalog, .input_id = -(SDL_CONTROLLER_AXIS_LEFTX + 1)},
    },
    .analog_right = {
        {.input_type = (uint32_t)InputType::ControllerAnalog, .input_id = SDL_CONTROLLER_AXIS_LEFTX + 1},
    },
    .analog_up = {
        {.input_type = (uint32_t)InputType::ControllerAnalog, .input_id = -(SDL_CONTROLLER_AXIS_LEFTY + 1)},
    },
    .analog_down = {
        {.input_type = (uint32_t)InputType::ControllerAnalog, .input_id = SDL_CONTROLLER_AXIS_LEFTY + 1},
    },
    .toggle_menu = {
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_BACK},
    },
    .accept_menu = {
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_SOUTH},
    },
    .apply_menu = {
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_WEST},
        {.input_type = (uint32_t)InputType::ControllerDigital, .input_id = SDL_CONTROLLER_BUTTON_START}
    },
#if defined(GOEMON_JAPAN_NATIVE) || defined(GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE)
    .reset_game = {},
#endif
#ifdef GOEMON_JP_NATIVE_F6_PAUSE_ACTIVE
    .pause_game = {},
#endif
    .previous_save_slot = {},
    .next_save_slot = {},
#ifdef GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE
    .save_state = {},
    .load_state = {},
#endif
};

void recomp::poll_inputs() {
    InputState.keys = SDL_GetKeyboardState(&InputState.numkeys);
    InputState.keymod = SDL_GetModState();

    {
        std::lock_guard lock{ InputState.cur_controllers_mutex };
        InputState.cur_controllers.clear();

        for (const auto& [id, state] : InputState.controller_states) {
            (void)id; // Avoid unused variable warning.
            SDL_GameController* controller = state.controller;
            if (controller != nullptr) {
                InputState.cur_controllers.push_back(controller);
            }
        }
    }

    // Read the deltas while resetting them to zero.
    {
        std::lock_guard lock{ InputState.pending_input_mutex };
        
        InputState.rotation_delta = InputState.pending_rotation_delta;
        InputState.pending_rotation_delta = { 0.0f, 0.0f };

        InputState.mouse_delta = InputState.pending_mouse_delta;
        InputState.pending_mouse_delta = { 0.0f, 0.0f };
    }
    
#if defined(GOEMON_JAPAN_NATIVE) || defined(GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE)
    // Input plumbing only: the pinned runtime cannot safely reboot its
    // guest OSThreads and RDRAM yet. Do not exit or restart the process.
    // Track button state while capturing input so a stale press cannot
    // trigger the request on returning to gameplay.
    bool reset_held = false;
    for (size_t slot = 0; slot < recomp::bindings_per_input; ++slot) {
        reset_held |= recomp::get_input_digital(
            recomp::get_input_binding(recomp::GameInput::RESET_GAME, slot, recomp::InputDevice::Keyboard));
        reset_held |= recomp::get_input_digital(
            recomp::get_input_binding(recomp::GameInput::RESET_GAME, slot, recomp::InputDevice::Controller));
    }
    static bool reset_was_held = true;
    if (reset_held && !reset_was_held && ultramodern::is_game_started() &&
        !recomp::game_input_disabled() && !recomp::all_input_disabled()) {
#ifdef GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE
#ifdef GOEMON_JP_NATIVE_F6_PAUSE_ACTIVE
        // F8 during F6-pause must release guest/native workers before
        // entering the existing joined shutdown and RDRAM replacement.
        if (ultramodern::jp_guest_is_paused()) {
            (void)ultramodern::jp_set_guest_paused(false);
            recomp::jp_set_audio_paused(false);
        }
#endif
        // Experimental: request a verified per-generation teardown and
        // re-entry. Input never touches worker queues or RDRAM itself.
        if (recomp::jp_request_integrated_reset()) {
            reset_was_held = reset_held;
            return;
        }
#else
        fprintf(stderr,
                "[Goemon JP reset] Request received; native reset backend not installed. "
                "Game continues without modification.\n");
#ifdef GOEMON_JP_THREAD_STAGE2_ACTIVE
        ultramodern::report_guest_thread_reset_blockers();
#endif
#ifdef GOEMON_JP_GUEST_STAGE3_ACTIVE
        // Diagnostic only; NEVER arm guest shutdown from input.
        ultramodern::report_guest_shutdown_readiness();
#endif
#ifdef GOEMON_JP_QUEUE_STAGE4_ACTIVE
        ultramodern::report_guest_message_queue_waits();
#endif
#ifdef GOEMON_JP_JOIN_STAGE5_ACTIVE
        // A readiness report only. No guest stop request or thread join from F8.
        ultramodern::report_guest_join_barrier_readiness();
#endif
#ifdef GOEMON_JP_EVENTS_STAGE6_ACTIVE
        // Audit native host workers; never interrupt live VI/GFX/RSP from F8.
        ultramodern::jp_report_native_event_worker_blockers();
#endif
#ifdef GOEMON_JP_GRAPHICS_STAGE8_ACTIVE
        // Snapshot only: no queue consumption, renderer interruption or reset.
        ultramodern::jp_report_graphics_queue_settlement();
#endif
#ifdef GOEMON_JP_BOOT_STAGE7_ACTIVE
        // Read-only audit: never request shutdown, save flush or audio pause.
        recomp::jp_report_native_boot_lifecycle();
        ultramodern::jp_report_save_worker_lifecycle();
        ultramodern::jp_report_audio_dispatch_lifecycle();
        jp_report_sdl_audio_queue();
#endif
#ifdef GOEMON_JP_LIVE_STAGE11_ACTIVE
        // Live read-only snapshot. NO stop flag, queue drain, reset or exit.
        ultramodern::jp_report_live_generation_preflight();
#endif
#ifdef GOEMON_JP_GENERATION_STAGE13_ACTIVE
        // Admission-only: record and reject this F8 request. No pending
        // request is retained and no generation state is changed.
        const auto jp_request = goemon_jp::reset::coordinator().record_unarmed_request();
        std::fprintf(stderr,
            "[Goemon JP generation stage13] generation=%llu request=%llu "
            "phase=%u admitted=0 backend=0 verified_fences=0x000 "
            "required_fences=0x%03X reason=backend-not-installed; "
            "no guest stop, no RDRAM reset.\n",
            static_cast<unsigned long long>(jp_request.generation),
            static_cast<unsigned long long>(jp_request.ticket),
            static_cast<unsigned>(jp_request.phase),
            static_cast<unsigned>(goemon_jp::reset::kFutureRequiredFenceMask));
#endif
#ifdef GOEMON_JP_CONFIG_FENCE_STAGE14_ACTIVE
        // Read-only. Producer gate may close only at normal PC exit.
        ultramodern::jp_report_config_action_producer_gate();
#endif
#ifdef GOEMON_JP_TIMER_PUBLISH_STAGE17_ACTIVE
        // Read-only live observation. Do not close timer producer admission.
        ultramodern::jp_report_timer_producer_stage17();
#endif
#ifdef GOEMON_JP_TIMER_EPOCH_STAGE18_ACTIVE
        // Read-only synchronized timebase: generation remains 1.
        ultramodern::jp_report_timer_epoch_stage18();
#endif
#endif // GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE
    }
    reset_was_held = reset_held;
#endif
}

void recomp::set_rumble(int controller_num, bool on) {
    if (controller_num == 0) {
        InputState.rumble_active = on;
    }
}

ultramodern::input::connected_device_info_t recomp::get_connected_device_info(int controller_num) {
    switch (controller_num) {
        case 0:
            return ultramodern::input::connected_device_info_t {
                .connected_device = ultramodern::input::Device::Controller,
                .connected_pak = ultramodern::input::Pak::RumblePak,
            };
    }

    return ultramodern::input::connected_device_info_t {
        .connected_device = ultramodern::input::Device::None,
        .connected_pak = ultramodern::input::Pak::None,
    };
}

static float smoothstep(float from, float to, float amount) {
    amount = (amount * amount) * (3.0f - 2.0f * amount);
    return std::lerp(from, to, amount);
}

// Update rumble to attempt to mimic the way n64 rumble ramps up and falls off
void recomp::update_rumble() {
    // Note: values are not accurate! just approximations based on feel
    if (InputState.rumble_active) {
        InputState.cur_rumble += 0.17f;
        if (InputState.cur_rumble > 1) InputState.cur_rumble = 1;
    } else {
        InputState.cur_rumble *= 0.92f;
        InputState.cur_rumble -= 0.01f;
        if (InputState.cur_rumble < 0) InputState.cur_rumble = 0;
    }
    float smooth_rumble = smoothstep(0, 1, InputState.cur_rumble);

    uint16_t rumble_strength = smooth_rumble * (recomp::get_rumble_strength() * 0xFFFF / 100);
    uint32_t duration = 1000000; // Dummy duration value that lasts long enough to matter as the game will reset rumble on its own.
    {
        std::lock_guard lock{ InputState.cur_controllers_mutex };
        for (const auto& controller : InputState.cur_controllers) {
            SDL_GameControllerRumble(controller, 0, rumble_strength, duration);
        }
    }
}

bool controller_button_state(int32_t input_id) {
    if (input_id >= 0 && input_id < SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_MAX) {
        SDL_GameControllerButton button = (SDL_GameControllerButton)input_id;
        bool ret = false;
        {
            std::lock_guard lock{ InputState.cur_controllers_mutex };
            for (const auto& controller : InputState.cur_controllers) {
                ret |= SDL_GameControllerGetButton(controller, button);
            }
        }

        return ret;
    }
    return false;
}

static std::atomic_bool right_analog_suppressed = false;

float controller_axis_state(int32_t input_id, bool allow_suppression) {
    if (abs(input_id) - 1 < SDL_GameControllerAxis::SDL_CONTROLLER_AXIS_MAX) {
        SDL_GameControllerAxis axis = (SDL_GameControllerAxis)(abs(input_id) - 1);
        bool negative_range = input_id < 0;
        float ret = 0.0f;

        {
            std::lock_guard lock{ InputState.cur_controllers_mutex };
            for (const auto& controller : InputState.cur_controllers) {
                float cur_val = SDL_GameControllerGetAxis(controller, axis) * (1/32768.0f);
                if (negative_range) {
                    cur_val = -cur_val;
                }

                // Check if this input is a right analog axis and suppress it accordingly.
                if (allow_suppression && right_analog_suppressed.load() &&
                    (axis == SDL_GameControllerAxis::SDL_CONTROLLER_AXIS_RIGHTX || axis == SDL_GameControllerAxis::SDL_CONTROLLER_AXIS_RIGHTY)) {
                    cur_val = 0;
                }
                ret += std::clamp(cur_val, 0.0f, 1.0f);
            }
        }

        return std::clamp(ret, 0.0f, 1.0f);
    }
    return false;
}

float recomp::get_input_analog(const recomp::InputField& field) {
    switch ((InputType)field.input_type) {
    case InputType::Keyboard:
        if (InputState.keys && field.input_id >= 0 && field.input_id < InputState.numkeys) {
            if (should_override_keystate(static_cast<SDL_Scancode>(field.input_id), InputState.keymod)) {
                return 0.0f;
            }
            return InputState.keys[field.input_id] ? 1.0f : 0.0f;
        }
        return 0.0f;
    case InputType::ControllerDigital:
        return controller_button_state(field.input_id) ? 1.0f : 0.0f;
    case InputType::ControllerAnalog:
        return controller_axis_state(field.input_id, true);
    case InputType::Mouse:
        // TODO mouse support
        return 0.0f;
    case InputType::None:
        return false;
    }
}

float recomp::get_input_analog(const std::span<const recomp::InputField> fields) {
    float ret = 0.0f;
    for (const auto& field : fields) {
        ret += get_input_analog(field);
    }
    return std::clamp(ret, 0.0f, 1.0f);
}

bool recomp::get_input_digital(const recomp::InputField& field) {
    switch ((InputType)field.input_type) {
    case InputType::Keyboard:
        if (InputState.keys && field.input_id >= 0 && field.input_id < InputState.numkeys) {
            if (should_override_keystate(static_cast<SDL_Scancode>(field.input_id), InputState.keymod)) {
                return false;
            }
            return InputState.keys[field.input_id] != 0;
        }
        return false;
    case InputType::ControllerDigital:
        return controller_button_state(field.input_id);
    case InputType::ControllerAnalog:
        // TODO adjustable threshold
        return controller_axis_state(field.input_id, true) >= axis_threshold;
    case InputType::Mouse:
        // TODO mouse support
        return false;
    case InputType::None:
        return false;
    }
}

bool recomp::get_input_digital(const std::span<const recomp::InputField> fields) {
    bool ret = 0;
    for (const auto& field : fields) {
        ret |= get_input_digital(field);
    }
    return ret;
}

void recomp::get_gyro_deltas(float* x, float* y) {
    std::array<float, 2> cur_rotation_delta = InputState.rotation_delta;
    float sensitivity = (float)recomp::get_gyro_sensitivity() / 100.0f;
    *x = cur_rotation_delta[0] * sensitivity;
    *y = cur_rotation_delta[1] * sensitivity;
}

void recomp::get_mouse_deltas(float* x, float* y) {
    std::array<float, 2> cur_mouse_delta = InputState.mouse_delta;
    float sensitivity = (float)recomp::get_mouse_sensitivity() / 100.0f;
    *x = cur_mouse_delta[0] * sensitivity;
    *y = cur_mouse_delta[1] * sensitivity;
}

void recomp::apply_joystick_deadzone(float x_in, float y_in, float* x_out, float* y_out) {
    float joystick_deadzone = (float)recomp::get_joystick_deadzone() / 100.0f;

    if(fabsf(x_in) < joystick_deadzone) {
        x_in = 0.0f;
    }
    else {
        if(x_in > 0.0f) {
            x_in -= joystick_deadzone;
        } 
        else {
            x_in += joystick_deadzone;
        }

        x_in /= (1.0f - joystick_deadzone);
    }

    if(fabsf(y_in) < joystick_deadzone) {
        y_in = 0.0f;
    }
    else {
        if(y_in > 0.0f) {
            y_in -= joystick_deadzone;
        } 
        else {
            y_in += joystick_deadzone;
        }

        y_in /= (1.0f - joystick_deadzone);
    }

    *x_out = x_in;
    *y_out = y_in;
}

void recomp::get_right_analog(float* x, float* y) {
    float x_val =
        controller_axis_state((SDL_GameControllerAxis::SDL_CONTROLLER_AXIS_RIGHTX + 1), false) -
        controller_axis_state(-(SDL_GameControllerAxis::SDL_CONTROLLER_AXIS_RIGHTX + 1), false);
    float y_val =
        controller_axis_state((SDL_GameControllerAxis::SDL_CONTROLLER_AXIS_RIGHTY + 1), false) -
        controller_axis_state(-(SDL_GameControllerAxis::SDL_CONTROLLER_AXIS_RIGHTY + 1), false);
    recomp::apply_joystick_deadzone(x_val, y_val, x, y);
}

void recomp::set_right_analog_suppressed(bool suppressed) {
    right_analog_suppressed.store(suppressed);
}

bool recomp::game_input_disabled() {
    // Disable input if any menu that blocks input is open.
    return recompui::is_context_capturing_input();
}

bool recomp::all_input_disabled() {
    // Disable all input if an input is being polled.
    return scanning_device != recomp::InputDevice::COUNT;
}

std::string controller_button_to_string(SDL_GameControllerButton button) {
    switch (button) {
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_A:
        return PF_GAMEPAD_A;
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_B:
        return PF_GAMEPAD_B;
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_X:
        return PF_GAMEPAD_X;
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_Y:
        return PF_GAMEPAD_Y;
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_BACK:
        return PF_XBOX_VIEW;
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_GUIDE:
        return PF_GAMEPAD_HOME;
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_START:
        return PF_XBOX_MENU;
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_LEFTSTICK:
        return PF_ANALOG_L_CLICK;
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_RIGHTSTICK:
        return PF_ANALOG_R_CLICK;
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_LEFTSHOULDER:
        return PF_XBOX_LEFT_SHOULDER;
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_RIGHTSHOULDER:
        return PF_XBOX_RIGHT_SHOULDER;
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_DPAD_UP:
        return PF_DPAD_UP;
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_DPAD_DOWN:
        return PF_DPAD_DOWN;
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_DPAD_LEFT:
        return PF_DPAD_LEFT;
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_DPAD_RIGHT:
        return PF_DPAD_RIGHT;
    // case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_MISC1:
    //     return "";
    // case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_PADDLE1:
    //     return "";
    // case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_PADDLE2:
    //     return "";
    // case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_PADDLE3:
    //     return "";
    // case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_PADDLE4:
    //     return "";
    case SDL_GameControllerButton::SDL_CONTROLLER_BUTTON_TOUCHPAD:
        return PF_SONY_TOUCHPAD;
    default:
        return "Button " + std::to_string(button);
    }
}

std::unordered_map<SDL_Scancode, std::string> scancode_codepoints {
    {SDL_SCANCODE_LEFT, PF_KEYBOARD_LEFT},
    // NOTE: UP and RIGHT are swapped with promptfont.
    {SDL_SCANCODE_UP, PF_KEYBOARD_RIGHT},
    {SDL_SCANCODE_RIGHT, PF_KEYBOARD_UP},
    {SDL_SCANCODE_DOWN, PF_KEYBOARD_DOWN},
    {SDL_SCANCODE_A, PF_KEYBOARD_A},
    {SDL_SCANCODE_B, PF_KEYBOARD_B},
    {SDL_SCANCODE_C, PF_KEYBOARD_C},
    {SDL_SCANCODE_D, PF_KEYBOARD_D},
    {SDL_SCANCODE_E, PF_KEYBOARD_E},
    {SDL_SCANCODE_F, PF_KEYBOARD_F},
    {SDL_SCANCODE_G, PF_KEYBOARD_G},
    {SDL_SCANCODE_H, PF_KEYBOARD_H},
    {SDL_SCANCODE_I, PF_KEYBOARD_I},
    {SDL_SCANCODE_J, PF_KEYBOARD_J},
    {SDL_SCANCODE_K, PF_KEYBOARD_K},
    {SDL_SCANCODE_L, PF_KEYBOARD_L},
    {SDL_SCANCODE_M, PF_KEYBOARD_M},
    {SDL_SCANCODE_N, PF_KEYBOARD_N},
    {SDL_SCANCODE_O, PF_KEYBOARD_O},
    {SDL_SCANCODE_P, PF_KEYBOARD_P},
    {SDL_SCANCODE_Q, PF_KEYBOARD_Q},
    {SDL_SCANCODE_R, PF_KEYBOARD_R},
    {SDL_SCANCODE_S, PF_KEYBOARD_S},
    {SDL_SCANCODE_T, PF_KEYBOARD_T},
    {SDL_SCANCODE_U, PF_KEYBOARD_U},
    {SDL_SCANCODE_V, PF_KEYBOARD_V},
    {SDL_SCANCODE_W, PF_KEYBOARD_W},
    {SDL_SCANCODE_X, PF_KEYBOARD_X},
    {SDL_SCANCODE_Y, PF_KEYBOARD_Y},
    {SDL_SCANCODE_Z, PF_KEYBOARD_Z},
    {SDL_SCANCODE_0, PF_KEYBOARD_0},
    {SDL_SCANCODE_1, PF_KEYBOARD_1},
    {SDL_SCANCODE_2, PF_KEYBOARD_2},
    {SDL_SCANCODE_3, PF_KEYBOARD_3},
    {SDL_SCANCODE_4, PF_KEYBOARD_4},
    {SDL_SCANCODE_5, PF_KEYBOARD_5},
    {SDL_SCANCODE_6, PF_KEYBOARD_6},
    {SDL_SCANCODE_7, PF_KEYBOARD_7},
    {SDL_SCANCODE_8, PF_KEYBOARD_8},
    {SDL_SCANCODE_9, PF_KEYBOARD_9},
    {SDL_SCANCODE_ESCAPE, PF_KEYBOARD_ESCAPE},
    {SDL_SCANCODE_F1, PF_KEYBOARD_F1},
    {SDL_SCANCODE_F2, PF_KEYBOARD_F2},
    {SDL_SCANCODE_F3, PF_KEYBOARD_F3},
    {SDL_SCANCODE_F4, PF_KEYBOARD_F4},
    {SDL_SCANCODE_F5, PF_KEYBOARD_F5},
    {SDL_SCANCODE_F6, PF_KEYBOARD_F6},
    {SDL_SCANCODE_F7, PF_KEYBOARD_F7},
    {SDL_SCANCODE_F8, PF_KEYBOARD_F8},
    {SDL_SCANCODE_F9, PF_KEYBOARD_F9},
    {SDL_SCANCODE_F10, PF_KEYBOARD_F10},
    {SDL_SCANCODE_F11, PF_KEYBOARD_F11},
    {SDL_SCANCODE_F12, PF_KEYBOARD_F12},
    {SDL_SCANCODE_PRINTSCREEN, PF_KEYBOARD_PRINT_SCREEN},
    {SDL_SCANCODE_SCROLLLOCK, PF_KEYBOARD_SCROLL_LOCK},
    {SDL_SCANCODE_PAUSE, PF_KEYBOARD_PAUSE},
    {SDL_SCANCODE_INSERT, PF_KEYBOARD_INSERT},
    {SDL_SCANCODE_HOME, PF_KEYBOARD_HOME},
    {SDL_SCANCODE_PAGEUP, PF_KEYBOARD_PAGE_UP},
    {SDL_SCANCODE_DELETE, PF_KEYBOARD_DELETE},
    {SDL_SCANCODE_END, PF_KEYBOARD_END},
    {SDL_SCANCODE_PAGEDOWN, PF_KEYBOARD_PAGE_DOWN},
    {SDL_SCANCODE_SPACE, PF_KEYBOARD_SPACE},
    {SDL_SCANCODE_BACKSPACE, PF_KEYBOARD_BACKSPACE},
    {SDL_SCANCODE_TAB, PF_KEYBOARD_TAB},
    {SDL_SCANCODE_RETURN, PF_KEYBOARD_ENTER},
    {SDL_SCANCODE_CAPSLOCK, PF_KEYBOARD_CAPS},
    {SDL_SCANCODE_NUMLOCKCLEAR, PF_KEYBOARD_NUM_LOCK},
    {SDL_SCANCODE_LSHIFT, "L" PF_KEYBOARD_SHIFT},
    {SDL_SCANCODE_RSHIFT, "R" PF_KEYBOARD_SHIFT},
};

std::string keyboard_input_to_string(SDL_Scancode key) {
    if (scancode_codepoints.find(key) != scancode_codepoints.end()) {
        return scancode_codepoints[key];
    }
    return std::to_string(key);
}

std::string controller_axis_to_string(int axis) {
    bool positive = axis > 0;
    SDL_GameControllerAxis actual_axis = SDL_GameControllerAxis(abs(axis) - 1);
    switch (actual_axis) {
    case SDL_GameControllerAxis::SDL_CONTROLLER_AXIS_LEFTX:
        return positive ? "\u21C0" : "\u21BC";
    case SDL_GameControllerAxis::SDL_CONTROLLER_AXIS_LEFTY:
        return positive ? "\u21C2" : "\u21BE";
    case SDL_GameControllerAxis::SDL_CONTROLLER_AXIS_RIGHTX:
        return positive ? "\u21C1" : "\u21BD";
    case SDL_GameControllerAxis::SDL_CONTROLLER_AXIS_RIGHTY:
        return positive ? "\u21C3" : "\u21BF";
    case SDL_GameControllerAxis::SDL_CONTROLLER_AXIS_TRIGGERLEFT:
        return positive ? "\u2196" : "\u21DC";
    case SDL_GameControllerAxis::SDL_CONTROLLER_AXIS_TRIGGERRIGHT:
        return positive ? "\u2197" : "\u21DD";
    default:
        return "Axis " + std::to_string(actual_axis) + (positive ? '+' : '-');
    }
}

std::string recomp::InputField::to_string() const {
    switch ((InputType)input_type) {
        case InputType::None:
            return "";
        case InputType::ControllerDigital:
            return controller_button_to_string((SDL_GameControllerButton)input_id);
        case InputType::ControllerAnalog:
            return controller_axis_to_string(input_id);
        case InputType::Keyboard:
            return keyboard_input_to_string((SDL_Scancode)input_id);
        default:
            return std::to_string(input_type) + "," + std::to_string(input_id);
    }
}
