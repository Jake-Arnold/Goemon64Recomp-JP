#include <cstdio>
#include <cassert>
#include <unordered_map>
#include <vector>
#include <array>
#include <filesystem>
#include <numeric>
#include <stdexcept>
#include <cinttypes>
#include <cstring>
#ifdef GOEMON_JP_SDL_AUDIO_STAGE15_ACTIVE
#include <chrono>
#include "../game/jp_generation_publisher_gate.hpp"
#endif
#ifdef GOEMON_JAPAN_NATIVE
#include "goemon_japan_build_id.h"
#if defined(__linux__)
#include "jp_session_logging.h"
#endif
#endif

#include "nfd.h"

#include "ultramodern/ultra64.h"
#include "ultramodern/ultramodern.hpp"
#define SDL_MAIN_HANDLED
#ifdef _WIN32
#include "SDL.h"
#else
#include "SDL2/SDL.h"
#include "SDL2/SDL_syswm.h"
// Undefine x11 macros that get included by SDL_syswm.h.
#undef None
#undef Status
#undef LockMask
#undef ControlMask
#undef Success
#undef Always
#endif

#include "recomp_ui.h"
#include "goemon_launcher.h"
#include "recomp_input.h"
#include "goemon_config.h"
#include "goemon_sound.h"
#include "goemon_render.h"
#include "goemon_support.h"
#include "goemon_game.h"
#include "recomp_data.h"
#include "ovl_patches.hpp"
#include "librecomp/game.hpp"
#include "librecomp/mods.hpp"
#include "librecomp/mods.hpp"
#include "librecomp/helpers.hpp"

#include "../../patches/graphics.h"
#include "../../patches/input.h"
#include "../../patches/sound.h"
#include "../../patches/misc_funcs.h"

// #include "mods/mm_recomp_dpad_builtin.h"

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <Windows.h>
#include <timeapi.h>
#include "SDL_syswm.h"
#endif

#include "../../lib/rt64/src/contrib/stb/stb_image.h"

const std::string version_string = GOEMON_LAUNCHER_VERSION;
#ifdef GOEMON_JAPAN_NATIVE
#ifdef GOEMON_JP_TIMER_STAGE1_ACTIVE
constexpr const char* jp_timer_build_mode = " [timer-stage1]";
#else
constexpr const char* jp_timer_build_mode = " [timer-off]";
#endif
#ifdef GOEMON_JP_THREAD_STAGE2_ACTIVE
constexpr const char* jp_thread_build_mode = " [threads-audit]";
#else
constexpr const char* jp_thread_build_mode = " [threads-off]";
#endif
#ifdef GOEMON_JP_GUEST_STAGE3_ACTIVE
constexpr const char* jp_guest_build_mode = " [guest-prep]";
#else
constexpr const char* jp_guest_build_mode = " [guest-prep-off]";
#endif
#ifdef GOEMON_JP_QUEUE_STAGE4_ACTIVE
constexpr const char* jp_queue_build_mode = " [queue-audit]";
#else
constexpr const char* jp_queue_build_mode = " [queue-off]";
#endif
#ifdef GOEMON_JP_JOIN_STAGE5_ACTIVE
constexpr const char* jp_join_build_mode = " [join-prep]";
#else
constexpr const char* jp_join_build_mode = " [join-prep-off]";
#endif
#ifdef GOEMON_JP_EVENTS_STAGE6_ACTIVE
constexpr const char* jp_events_build_mode = " [native-events-audit]";
#else
constexpr const char* jp_events_build_mode = " [native-events-off]";
#endif
#ifdef GOEMON_JP_BOOT_STAGE7_ACTIVE
constexpr const char* jp_boot_build_mode = " [boot-save-audio-audit]";
#else
constexpr const char* jp_boot_build_mode = " [boot-save-audio-off]";
#endif
#ifdef GOEMON_JP_GRAPHICS_STAGE8_ACTIVE
constexpr const char* jp_gfx_build_mode = " [graphics-queue-audit]";
#else
constexpr const char* jp_gfx_build_mode = " [graphics-queue-off]";
#endif
#ifdef GOEMON_JP_EXIT_STAGE9_ACTIVE
constexpr const char* jp_exit_build_mode = " [guest-exit-drain]";
#else
constexpr const char* jp_exit_build_mode = " [guest-exit-off]";
#endif
#ifdef GOEMON_JP_ACTION_STAGE10_ACTIVE
constexpr const char* jp_action_build_mode = " [action-exit-drain]";
#else
constexpr const char* jp_action_build_mode = " [action-exit-off]";
#endif
#ifdef GOEMON_JP_LIVE_STAGE11_ACTIVE
constexpr const char* jp_live_build_mode = " [live-preflight]";
#else
constexpr const char* jp_live_build_mode = " [live-preflight-off]";
#endif
#ifdef GOEMON_JP_BARRIER_STAGE12_ACTIVE
constexpr const char* jp_barrier_build_mode = " [exit-barrier-audit]";
#else
constexpr const char* jp_barrier_build_mode = " [exit-barrier-off]";
#endif
#ifdef GOEMON_JP_GENERATION_STAGE13_ACTIVE
constexpr const char* jp_generation_build_mode = " [generation-admission]";
#else
constexpr const char* jp_generation_build_mode = " [generation-admission-off]";
#endif
#ifdef GOEMON_JP_CONFIG_FENCE_STAGE14_ACTIVE
constexpr const char* jp_config_fence_build_mode = " [config-publisher-fence]";
#else
constexpr const char* jp_config_fence_build_mode = " [config-publisher-fence-off]";
#endif
#ifdef GOEMON_JP_SDL_AUDIO_STAGE15_ACTIVE
constexpr const char* jp_sdl_audio_build_mode = " [sdl-audio-exit]";
#else
constexpr const char* jp_sdl_audio_build_mode = " [sdl-audio-exit-off]";
#endif
#ifdef GOEMON_JP_SAVE_STAGE16_ACTIVE
constexpr const char* jp_save_final_build_mode = " [save-finalize]";
#else
constexpr const char* jp_save_final_build_mode = " [save-finalize-off]";
#endif
#ifdef GOEMON_JP_TIMER_PUBLISH_STAGE17_ACTIVE
constexpr const char* jp_timer_producer_build_mode = " [timer-publisher-fence]";
#else
constexpr const char* jp_timer_producer_build_mode = " [timer-publisher-fence-off]";
#endif
#ifdef GOEMON_JP_TIMER_EPOCH_STAGE18_ACTIVE
constexpr const char* jp_timer_epoch_build_mode = " [timer-epoch-clock]";
#else
constexpr const char* jp_timer_epoch_build_mode = " [timer-epoch-off]";
#endif
#endif

template<typename... Ts>
void exit_error(const char* str, Ts ...args) {
    // TODO pop up an error
    ((void)fprintf(stderr, str, args), ...);
    assert(false);

    ultramodern::error_handling::quick_exit(__FILE__, __LINE__, __FUNCTION__);
}

ultramodern::gfx_callbacks_t::gfx_data_t create_gfx() {
    SDL_SetHint(SDL_HINT_WINDOWS_DPI_AWARENESS, "permonitorv2");
    SDL_SetHint(SDL_HINT_GAMECONTROLLER_USE_BUTTON_LABELS, "0");
    SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI_PS4_RUMBLE, "1");
    SDL_SetHint(SDL_HINT_JOYSTICK_HIDAPI_PS5_RUMBLE, "1");
    SDL_SetHint(SDL_HINT_MOUSE_FOCUS_CLICKTHROUGH, "1");
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
#if defined(__linux__) && defined(GOEMON_JAPAN_NATIVE)
    // Use a stable desktop identity so GNOME can associate direct launches
    // of the Japanese executable with its installed application icon.
    SDL_setenv("SDL_VIDEO_X11_WMCLASS", "Goemon64RecompiledJP", 0);
    SDL_setenv("SDL_VIDEO_WAYLAND_WMCLASS", "Goemon64RecompiledJP", 0);
#endif

    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMECONTROLLER | SDL_INIT_JOYSTICK) > 0) {
        exit_error("Failed to initialize SDL2: %s\n", SDL_GetError());
    }

    fprintf(stdout, "SDL Video Driver: %s\n", SDL_GetCurrentVideoDriver());

    return {};
}

#if defined(__gnu_linux__)
#include "icon_bytes.h"

bool SetImageAsIcon(const char* filename, SDL_Window* window)
{
    // Read data
    int width, height, bytesPerPixel;
    void* data = stbi_load_from_memory(reinterpret_cast<const uint8_t*>(icon_bytes), sizeof(icon_bytes), &width, &height, &bytesPerPixel, 4);

    // Calculate pitch
    int pitch;
    pitch = width * 4;
    pitch = (pitch + 3) & ~3;

    // Setup relevance bitmask
    int Rmask, Gmask, Bmask, Amask;

#if SDL_BYTEORDER == SDL_LIL_ENDIAN
    Rmask = 0x000000FF;
    Gmask = 0x0000FF00;
    Bmask = 0x00FF0000;
    Amask = 0xFF000000;
#else
    Rmask = 0xFF000000;
    Gmask = 0x00FF0000;
    Bmask = 0x0000FF00;
    Amask = 0x000000FF;
#endif

    SDL_Surface* surface = nullptr;
    if (data != nullptr) {
        surface = SDL_CreateRGBSurfaceFrom(data, width, height, 32, pitch, Rmask, Gmask,
            Bmask, Amask);
    }

    if (surface == nullptr) {   
        if (data != nullptr) {
            stbi_image_free(data);
        }
        return false;
    } else {
        SDL_SetWindowIcon(window,surface);
        SDL_FreeSurface(surface);
        stbi_image_free(data);
        return true;
    }
}
#endif

SDL_Window* window;

ultramodern::renderer::WindowHandle create_window(ultramodern::gfx_callbacks_t::gfx_data_t) {
#ifdef GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE
    // The surrounding recomp::start() never returned to main: retain the
    // exact SDL_Window pointer through the verified guest-generation loop.
    if (window != nullptr) {
        std::fprintf(stderr,
            "[Goemon JP integrated reset] SDL window reused=%p pid=unchanged.\n",
            static_cast<void*>(window));
        return ultramodern::renderer::WindowHandle{window};
    }
#endif
    uint32_t flags = SDL_WINDOW_RESIZABLE;

#if defined(__APPLE__)
    flags |= SDL_WINDOW_METAL;
#elif defined(RT64_SDL_WINDOW_VULKAN)
    flags |= SDL_WINDOW_VULKAN;
#endif

#ifdef GOEMON_JAPAN_NATIVE
    const std::string window_title = "Goemon 64: Recompiled JP v" + version_string +
        " (" + GOEMON_JP_BUILD_COMMIT + (GOEMON_JP_BUILD_DIRTY ? "-dirty" : "") + ")" + jp_timer_build_mode + jp_thread_build_mode + jp_guest_build_mode + jp_queue_build_mode + jp_join_build_mode + jp_events_build_mode + jp_boot_build_mode + jp_gfx_build_mode + jp_exit_build_mode + jp_action_build_mode + jp_live_build_mode + jp_barrier_build_mode + jp_generation_build_mode + jp_config_fence_build_mode + jp_sdl_audio_build_mode + jp_save_final_build_mode + jp_timer_producer_build_mode + jp_timer_epoch_build_mode;
#else
    const std::string window_title = "Goemon 64: Recompiled";
#endif
    const auto& rom_title = goemon64::launcher::experimental_window_title();
    const auto& title = rom_title.empty() ? window_title : rom_title;
    window = SDL_CreateWindow(title.c_str(), SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, 1600, 960, flags);
#if defined(__linux__)
    SetImageAsIcon("icons/512.png",window);
    if (ultramodern::renderer::get_graphics_config().wm_option == ultramodern::renderer::WindowMode::Fullscreen) { // TODO: Remove once RT64 gets native fullscreen support on Linux
        SDL_SetWindowFullscreen(window,SDL_WINDOW_FULLSCREEN_DESKTOP);
    } else {
        SDL_SetWindowFullscreen(window,0);
    }
#endif

    if (window == nullptr) {
        exit_error("Failed to create window: %s\n", SDL_GetError());
    }

    SDL_SysWMinfo wmInfo;
    SDL_VERSION(&wmInfo.version);
    SDL_GetWindowWMInfo(window, &wmInfo);

#if defined(_WIN32)
    return ultramodern::renderer::WindowHandle{ wmInfo.info.win.window, GetCurrentThreadId() };
#elif defined(__linux__) || defined(__ANDROID__)
    return ultramodern::renderer::WindowHandle{ window };
#elif defined(__APPLE__)
    SDL_MetalView view = SDL_Metal_CreateView(window);
    return ultramodern::renderer::WindowHandle{ wmInfo.info.cocoa.window,  SDL_Metal_GetLayer(view) };
#else
    static_assert(false && "Unimplemented");
#endif
}

void update_gfx(void*) {
    const auto title = goemon64::launcher::take_window_title();
    if (window && !title.empty()) {
        SDL_SetWindowTitle(window, title.c_str());
    }
    recomp::handle_events();
}

static SDL_AudioCVT audio_convert;
static SDL_AudioDeviceID audio_device = 0;
#ifdef GOEMON_JP_SDL_AUDIO_STAGE15_ACTIVE
// Host-only audio callback publication fence: separate from SDL lifecycle.
// Only normal app return may close it; F8 does not change this state.
static goemon_jp::reset::PublisherGate jp_sdl_audio_gate{1};
#endif
#ifdef GOEMON_JP_NATIVE_F6_PAUSE_ACTIVE
namespace recomp {
void jp_set_audio_paused(bool pause) {
    // Pause the existing SDL queue. Keep its bytes and device, so on
    // resume playback continues instead of losing an audio generation.
    if (audio_device != 0) {
        SDL_PauseAudioDevice(audio_device, pause ? 1 : 0);
    }
    // The window is deliberately NOT hidden/destroyed during guest pause.
    static std::string saved_title;
    if (window != nullptr) {
        if (pause) {
            if (saved_title.empty()) {
                saved_title = SDL_GetWindowTitle(window);
            }
            SDL_SetWindowTitle(window, (saved_title + " [PAUSED — F6]").c_str());
        } else if (!saved_title.empty()) {
            SDL_SetWindowTitle(window, saved_title.c_str());
            saved_title.clear();
        }
    }
}
} // namespace recomp
#endif

#ifdef GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE
namespace recomp {
bool jp_reset_audio_buffer_after_join() {
    // The previous guest, native event workers and SDL audio dispatch have
    // joined before this function runs. Keep the device and SDL window alive.
    const auto state = jp_sdl_audio_gate.snapshot();
    if (!state.open || state.in_flight != 0 || audio_device == 0) {
        return false;
    }
    SDL_PauseAudioDevice(audio_device, 1);
    SDL_ClearQueuedAudio(audio_device);
    const bool cleared = SDL_GetQueuedAudioSize(audio_device) == 0;
    SDL_PauseAudioDevice(audio_device, 0);
    std::fprintf(stderr,
        "[Goemon JP integrated reset] audio_reused=%u queue_cleared=%u.\n",
        static_cast<unsigned>(audio_device != 0),
        static_cast<unsigned>(cleared));
    return cleared;
}
} // namespace recomp
#endif

#ifdef GOEMON_JP_BOOT_STAGE7_ACTIVE
void jp_report_sdl_audio_queue() {
    // SDL owns the actual playback buffer; this read does not clear or
    // pause audio and does not prove that callbacks are quiescent.
    const unsigned queued = audio_device ? SDL_GetQueuedAudioSize(audio_device) : 0u;
    std::fprintf(stderr,
        "[Goemon JP SDL audio] device=%u queued_bytes=%u; playback still active.\n",
        static_cast<unsigned>(audio_device), queued);
}
#endif

// Samples per channel per second.
static uint32_t sample_rate = 48000;
static uint32_t output_sample_rate = 48000;
// Channel count.
constexpr uint32_t input_channels = 2;
static uint32_t output_channels = 2;

// Terminology: a frame is a collection of samples for each channel. e.g. 2 input samples is one input frame. This is unrelated to graphical frames.

// Number of frames to duplicate for fixing interpolation at the start and end of a chunk.
constexpr uint32_t duplicated_input_frames = 4;
// The number of output frames to skip for playback (to avoid playing duplicate inputs twice).
static uint32_t discarded_output_frames;

constexpr uint32_t bytes_per_frame = input_channels * sizeof(float);

void queue_samples(int16_t* audio_data, size_t sample_count) {
#ifdef GOEMON_JP_SDL_AUDIO_STAGE15_ACTIVE
    // Own a counted lease across conversion AND SDL_QueueAudio.
    auto jp_audio_lease = jp_sdl_audio_gate.try_acquire();
    if (!jp_audio_lease) return;
#endif
    // Buffer for holding the output of swapping the audio channels. This is reused across
    // calls to reduce runtime allocations.
    static std::vector<float> swap_buffer;
    static std::array<float, duplicated_input_frames * input_channels> duplicated_sample_buffer;

    // Make sure the swap buffer is large enough to hold the audio data, including any extra space needed for resampling.
    size_t resampled_sample_count = sample_count + duplicated_input_frames * input_channels;
    size_t max_sample_count = std::max(resampled_sample_count, resampled_sample_count * audio_convert.len_mult);
    if (max_sample_count > swap_buffer.size()) {
        swap_buffer.resize(max_sample_count);
    }

    // Copy the duplicated frames from last chunk into this chunk
    for (size_t i = 0; i < duplicated_input_frames * input_channels; i++) {
        swap_buffer[i] = duplicated_sample_buffer[i];
    }

    // Convert the audio from 16-bit values to floats and swap the audio channels into the
    // swap buffer to correct for the address xor caused by endianness handling.
    float cur_main_volume = goemon64::get_main_volume() / 100.0f; // Get the current main volume, normalized to 0.0-1.0.
    for (size_t i = 0; i < sample_count; i += input_channels) {
        swap_buffer[i + 0 + duplicated_input_frames * input_channels] = audio_data[i + 1] * (0.5f / 32768.0f) * cur_main_volume;
        swap_buffer[i + 1 + duplicated_input_frames * input_channels] = audio_data[i + 0] * (0.5f / 32768.0f) * cur_main_volume;
    }

    // TODO handle cases where a chunk is smaller than the duplicated frame count.
    assert(sample_count > duplicated_input_frames * input_channels);

    // Copy the last converted samples into the duplicated sample buffer to reuse in resampling the next queued chunk.
    for (size_t i = 0; i < duplicated_input_frames * input_channels; i++) {
        duplicated_sample_buffer[i] = swap_buffer[i + sample_count];
    }

    audio_convert.buf = reinterpret_cast<Uint8*>(swap_buffer.data());
    audio_convert.len = (sample_count + duplicated_input_frames * input_channels) * sizeof(swap_buffer[0]);

    int ret = SDL_ConvertAudio(&audio_convert);

    if (ret < 0) {
        printf("Error using SDL audio converter: %s\n", SDL_GetError());
        throw std::runtime_error("Error using SDL audio converter");
    }

    uint64_t cur_queued_microseconds = uint64_t(SDL_GetQueuedAudioSize(audio_device)) / bytes_per_frame * 1000000 / sample_rate;
    uint32_t num_bytes_to_queue = audio_convert.len_cvt - output_channels * discarded_output_frames * sizeof(swap_buffer[0]);
    float* samples_to_queue = swap_buffer.data() + output_channels * discarded_output_frames / 2;

    // Prevent audio latency from building up by skipping samples in incoming audio when too many samples are already queued.
    // Skip samples based on how many microseconds of samples are queued already.
    uint32_t skip_factor = cur_queued_microseconds / 100000;
    if (skip_factor != 0) {
        uint32_t skip_ratio = 1 << skip_factor;
        num_bytes_to_queue /= skip_ratio;
        for (size_t i = 0; i < num_bytes_to_queue / (output_channels * sizeof(swap_buffer[0])); i++) {
            samples_to_queue[2 * i + 0] = samples_to_queue[2 * skip_ratio * i + 0];
            samples_to_queue[2 * i + 1] = samples_to_queue[2 * skip_ratio * i + 1];
        }
    }

    // Queue the swapped audio data.
    // Offset the data start by only half the discarded frame count as the other half of the discarded frames are at the end of the buffer.
    SDL_QueueAudio(audio_device, samples_to_queue, num_bytes_to_queue);
}

size_t get_frames_remaining() {
#ifdef GOEMON_JP_SDL_AUDIO_STAGE15_ACTIVE
    // SDL_GetQueuedAudioSize also needs to complete before device close.
    auto jp_audio_lease = jp_sdl_audio_gate.try_acquire();
    if (!jp_audio_lease) return 0;
#endif
    constexpr float buffer_offset_frames = 1.0f;
    // Get the number of remaining buffered audio bytes.
    uint64_t buffered_byte_count = SDL_GetQueuedAudioSize(audio_device);

    // Scale the byte count based on the ratio of sample rates and channel counts.
    buffered_byte_count = buffered_byte_count * 2 * sample_rate / output_sample_rate / output_channels;

    // Adjust the reported count to be some number of refreshes in the future, which helps ensure that
    // there are enough samples even if the audio thread experiences a small amount of lag. This prevents
    // audio popping on games that use the buffered audio byte count to determine how many samples
    // to generate.
    uint32_t frames_per_vi = (sample_rate / 60);
    if (buffered_byte_count > (buffer_offset_frames * bytes_per_frame * frames_per_vi)) {
        buffered_byte_count -= (buffer_offset_frames * bytes_per_frame * frames_per_vi);
    }
    else {
        buffered_byte_count = 0;
    }
    // Convert from byte count to sample count.
    return static_cast<uint32_t>(buffered_byte_count / bytes_per_frame);
}

void update_audio_converter() {
    int ret = SDL_BuildAudioCVT(&audio_convert, AUDIO_F32, input_channels, sample_rate, AUDIO_F32, output_channels, output_sample_rate);

    if (ret < 0) {
        printf("Error creating SDL audio converter: %s\n", SDL_GetError());
        throw std::runtime_error("Error creating SDL audio converter");
    }

    // Calculate the number of samples to discard based on the sample rate ratio and the duplicate frame count.
    discarded_output_frames = duplicated_input_frames * output_sample_rate / sample_rate;
}

void set_frequency(uint32_t freq) {
#ifdef GOEMON_JP_SDL_AUDIO_STAGE15_ACTIVE
    // Converter settings must not mutate after the SDL audio close barrier.
    auto jp_audio_lease = jp_sdl_audio_gate.try_acquire();
    if (!jp_audio_lease) return;
#endif
    sample_rate = freq;

    update_audio_converter();
}

void reset_audio(uint32_t output_freq) {
    SDL_AudioSpec spec_desired{
        .freq = (int)output_freq,
        .format = AUDIO_F32,
        .channels = (Uint8)output_channels,
        .silence = 0, // calculated
        .samples = 0x100, // Fairly small sample count to reduce the latency of internal buffering
        .padding = 0, // unused
        .size = 0, // calculated
        .callback = nullptr,
        .userdata = nullptr
    };


    audio_device = SDL_OpenAudioDevice(nullptr, false, &spec_desired, nullptr, 0);
    if (audio_device == 0) {
        exit_error("SDL error opening audio device: %s\n", SDL_GetError());
    }
    SDL_PauseAudioDevice(audio_device, 0);

    output_sample_rate = output_freq;
    update_audio_converter();
}

// extern RspUcodeFunc njpgdspMain;
extern RspUcodeFunc aspMain;

RspUcodeFunc* get_rsp_microcode(const OSTask* task) {
    switch (task->t.type) {
    case M_AUDTASK:
        return aspMain;

    /*
    case M_NJPEGTASK:
        return njpgdspMain;
	*/

    default:
        fprintf(stderr, "Unknown task: %" PRIu32 "\n", task->t.type);
        return nullptr;
    }
}

extern "C" void recomp_entrypoint(uint8_t * rdram, recomp_context * ctx);
gpr get_entrypoint_address();
#ifdef GOEMON_UNIFIED_REGIONS
extern "C" void goemon_usa_recomp_entrypoint(uint8_t* rdram, recomp_context* ctx);
#endif

// Each executable owns one region-specific GameEntry. Never dispatch JP code
// through USA overlay tables, patch definitions or save configuration.
#ifdef GOEMON_JAPAN_NATIVE
std::vector<recomp::GameEntry> supported_games = {
    {
        .rom_hash = 0xD526A3DB80EB7924ULL,
        .internal_name = "\xB6\xDE\xDD\xCA\xDE\xDA \xBA\xDE\xB4\xD3\xDD",
        .game_id = u8"mnsg.jp",
        .mod_game_id = "mnsg.jp",
        .save_type = recomp::SaveType::AllowAll,
        .is_enabled = true,
        .decompression_routine = goemon64::decompress_mnsg_japan,
        .has_compressed_code = true,
        .entrypoint_address = get_entrypoint_address(),
        .entrypoint = recomp_entrypoint,
    }
#ifdef GOEMON_UNIFIED_REGIONS
    ,{
        .rom_hash = 0xDB1BC7EE0E6BEBA1ULL,
        .internal_name = "MYSTICAL NINJA",
        .game_id = u8"mnsg.us",
        .mod_game_id = "mnsg",
        .save_type = recomp::SaveType::AllowAll,
        .is_enabled = true,
        .decompression_routine = goemon64::decompress_mnsg,
        .has_compressed_code = true,
        .entrypoint_address = get_entrypoint_address(),
        .entrypoint = goemon_usa_recomp_entrypoint,
    }
#endif
};
#else
// array of supported GameEntry objects
std::vector<recomp::GameEntry> supported_games = {
	{
        .rom_hash = 0xDB1BC7EE0E6BEBA1ULL,
        .internal_name = "MYSTICAL NINJA",
        .game_id = u8"mnsg.us",
        .mod_game_id = "mnsg",
        .save_type = recomp::SaveType::AllowAll,
        .is_enabled = true,
        .decompression_routine = goemon64::decompress_mnsg,
        .has_compressed_code = true,
        .entrypoint_address = get_entrypoint_address(),
        .entrypoint = recomp_entrypoint,
	}
};
#endif // GOEMON_JAPAN_NATIVE


#ifdef GOEMON_UNIFIED_REGIONS
bool goemon64::launcher::start_unified_game(bool japan) {
    if (ultramodern::is_game_started()) {
        return false;
    }
    auto& game = supported_games[japan ? 0 : 1];
    auto rom_hash = game.rom_hash;
    if (experimental_rom_mode()) {
        if (!activate_experimental_rom(japan, rom_hash)) {
            return false;
        }
    } else if (!region_rom_valid(japan) || !select_unified_profile(japan)) {
        return false;
    }
    const auto profile = goemon64::get_app_folder_path();
    if (profile.empty()) {
        return false;
    }

    // No guest has started yet. Install one matching code/patch table before
    // publishing the game ID that lets the waiting guest boot.
    if (japan) {
        goemon64::register_overlays();
        goemon64::register_patches();
    } else {
        goemon64::register_usa_overlays();
        goemon64::register_usa_patches();
    }
    game.rom_hash = rom_hash;
    recomp::register_game(game);
    recomp::mods::close_mods();
    recomp::register_config_path(profile);
    recomp::mods::initialize_mods();
    recomp::mods::scan_mods();
    queue_window_title(japan);
    recomp::start_game(game.game_id);
    return true;
}
#endif

// TODO: move somewhere else
namespace goemon64 {
    std::string get_game_thread_name(const OSThread* t) {
        std::string name = "[Game] ";

        switch (t->id) {
        case 0:
            switch (t->priority) {
            case 150:
                name += "PIMGR";
                break;

            case 254:
                name += "VIMGR";
                break;

            default:
                name += std::to_string(t->id);
                break;
            }
            break;

        case 1:
            name += "IDLE";
            break;

        case 3:
            switch (t->priority) {
            case 10:
                name += "MAIN";
                break;

            case 12:
                name += "AUDIO";
                break;

            default:
                name += std::to_string(t->id);
                break;
            }
            break;

        case 4:
            switch (t->priority) {
            case 9:
                name += "GAME";
                break;

            case 13:
                name += "SCHED";
                break;

            default:
                name += std::to_string(t->id);
                break;
            }
            break;

        default:
            name += std::to_string(t->id);
            break;
        }

        return name;
    }
}

#ifdef _WIN32

struct PreloadContext {
    HANDLE handle;
    HANDLE mapping_handle;
    SIZE_T size;
    PVOID view;
};

bool preload_executable(PreloadContext& context) {
    wchar_t module_name[MAX_PATH];
    GetModuleFileNameW(NULL, module_name, MAX_PATH);

    context.handle = CreateFileW(module_name, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (context.handle == INVALID_HANDLE_VALUE) {
        fprintf(stderr, "Failed to load executable into memory!");
        context = {};
        return false;
    }

    LARGE_INTEGER module_size;
    if (!GetFileSizeEx(context.handle, &module_size)) {
        fprintf(stderr, "Failed to get size of executable!");
        CloseHandle(context.handle);
        context = {};
        return false;
    }

    context.size = module_size.QuadPart;

    context.mapping_handle = CreateFileMappingW(context.handle, nullptr, PAGE_READONLY, 0, 0, nullptr);
    if (context.mapping_handle == nullptr) {
        fprintf(stderr, "Failed to create file mapping of executable!");
        CloseHandle(context.handle);
        context = {};
        return EXIT_FAILURE;
    }

    context.view = MapViewOfFile(context.mapping_handle, FILE_MAP_READ, 0, 0, 0);
    if (context.view == nullptr) {
        fprintf(stderr, "Failed to map view of of executable!");
        CloseHandle(context.mapping_handle);
        CloseHandle(context.handle);
        context = {};
        return false;
    }

    DWORD pid = GetCurrentProcessId();
    HANDLE process_handle = OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, FALSE, pid);
    if (process_handle == nullptr) {
        fprintf(stderr, "Failed to open own process!");
        CloseHandle(context.mapping_handle);
        CloseHandle(context.handle);
        context = {};
        return false;
    }

    SIZE_T minimum_set_size, maximum_set_size;
    if (!GetProcessWorkingSetSize(process_handle, &minimum_set_size, &maximum_set_size)) {
        fprintf(stderr, "Failed to get working set size!");
        CloseHandle(context.mapping_handle);
        CloseHandle(context.handle);
        context = {};
        return false;
    }

    if (!SetProcessWorkingSetSize(process_handle, minimum_set_size + context.size, maximum_set_size + context.size)) {
        fprintf(stderr, "Failed to set working set size!");
        CloseHandle(context.mapping_handle);
        CloseHandle(context.handle);
        context = {};
        return false;
    }

    if (VirtualLock(context.view, context.size) == 0) {
        fprintf(stderr, "Failed to lock view of executable! (Error: %08lx)\n", GetLastError());
        CloseHandle(context.mapping_handle);
        CloseHandle(context.handle);
        context = {};
        return false;
    }

    return true;
}

void release_preload(PreloadContext& context) {
    VirtualUnlock(context.view, context.size);
    CloseHandle(context.mapping_handle);
    CloseHandle(context.handle);
    context = {};
}

#else

struct PreloadContext {

};

// TODO implement on other platforms
bool preload_executable(PreloadContext& context) {
    return false;
}

void release_preload(PreloadContext& context) {
}

#endif

void enable_texture_pack(recomp::mods::ModContext& context, const recomp::mods::ModHandle& mod) {
    goemon64::renderer::enable_texture_pack(context, mod);
}

void disable_texture_pack(recomp::mods::ModContext&, const recomp::mods::ModHandle& mod) {
    goemon64::renderer::disable_texture_pack(mod);
}

void reorder_texture_pack(recomp::mods::ModContext&) {
    goemon64::renderer::trigger_texture_pack_update();
}

#define REGISTER_FUNC(name) recomp::overlays::register_base_export(#name, name)

int main(int argc, char** argv) {
    if (argc >= 2 && std::strcmp(argv[1], "--version") == 0) {
#ifdef GOEMON_JAPAN_NATIVE
        printf("Goemon 64: Recompiled JP v%s (%s%s)%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s%s\n",
               version_string.c_str(), GOEMON_JP_BUILD_COMMIT,
               GOEMON_JP_BUILD_DIRTY ? "-dirty" : "", jp_timer_build_mode,
               jp_thread_build_mode, jp_guest_build_mode, jp_queue_build_mode,
               jp_join_build_mode, jp_events_build_mode, jp_boot_build_mode,
               jp_gfx_build_mode, jp_exit_build_mode, jp_action_build_mode,
               jp_live_build_mode, jp_barrier_build_mode, jp_generation_build_mode, jp_config_fence_build_mode, jp_sdl_audio_build_mode, jp_save_final_build_mode, jp_timer_producer_build_mode, jp_timer_epoch_build_mode);
#else
        printf("Goemon 64: Recompiled v%s\n", version_string.c_str());
#endif
        return EXIT_SUCCESS;
    }
#if defined(__linux__) && defined(GOEMON_JAPAN_NATIVE)
    // Capture both standalone desktop and CLI sessions before preload, SDL,
    // native startup or guest worker creation. --version stays side-effect free.
    if (!goemon_jp::logging::start_session(version_string.c_str(), GOEMON_JP_BUILD_COMMIT)) {
        std::fprintf(stderr,
                     "[Goemon JP session log] WARNING: could not open persistent log; "
                     "terminal logging continues.\n");
    }
#endif
    recomp::Version project_version{};
    if (!recomp::Version::from_string(version_string, project_version)) {
        ultramodern::error_handling::message_box(("Invalid version string: " + version_string).c_str());
        return EXIT_FAILURE;
    }

    // Map this executable into memory and lock it, which should keep it in physical memory. This ensures
    // that there are no stutters from the OS having to load new pages of the executable whenever a new code page is run.
    PreloadContext preload_context;
    bool preloaded = preload_executable(preload_context);

    if (!preloaded) {
        fprintf(stderr, "Failed to preload executable!\n");
    }

#ifdef _WIN32
    // Set up high resolution timing period.
    timeBeginPeriod(1);

    // Process arguments.
    for (int i = 1; i < argc; i++)
    {
        if (strcmp(argv[i], "--show-console") == 0)
        {
            if (GetConsoleWindow() == nullptr)
            {
                AllocConsole();
                freopen("CONIN$", "r", stdin);
                freopen("CONOUT$", "w", stderr);
                freopen("CONOUT$", "w", stdout);
            }

            break;
        }
    }

    // Set up console output to accept UTF-8 on windows
    SetConsoleOutputCP(CP_UTF8);

    // Initialize native file dialogs.
    NFD_Init();

    // Change to a font that supports Japanese characters
    CONSOLE_FONT_INFOEX cfi;
    cfi.cbSize = sizeof cfi;
    cfi.nFont = 0;
    cfi.dwFontSize.X = 0;
    cfi.dwFontSize.Y = 16;
    cfi.FontFamily = FF_DONTCARE;
    cfi.FontWeight = FW_NORMAL;
    wcscpy_s(cfi.FaceName, L"NSimSun");
    SetCurrentConsoleFontEx(GetStdHandle(STD_OUTPUT_HANDLE), FALSE, &cfi);
#endif

#ifdef _WIN32
    // Force wasapi on Windows, as there seems to be some issue with sample queueing with directsound currently.
    SDL_setenv("SDL_AUDIODRIVER", "wasapi", true);
#endif

#if defined(__linux__) && defined(RECOMP_FLATPAK)
    // When using Flatpak, applications tend to launch from the home directory by default.
    // Mods might use the current working directory to store the data, so we switch it to a directory
    // with persistent data storage and write permissions under Flatpak to ensure it works.
    std::error_code ec;
    std::filesystem::current_path("/var/data", ec);
#endif

    // Initialize SDL audio and set the output frequency.
    SDL_InitSubSystem(SDL_INIT_AUDIO);
    reset_audio(48000);

    // Source controller mappings file
    std::u8string controller_db_path = (goemon64::get_program_path() / "recompcontrollerdb.txt").u8string();
    if (SDL_GameControllerAddMappingsFromFile(reinterpret_cast<const char *>(controller_db_path.c_str())) < 0) {
        fprintf(stderr, "Failed to load controller mappings: %s\n", SDL_GetError());
    }

    if (argc == 2) {
#ifndef GOEMON_UNIFIED_REGIONS
        if (goemon64::launcher::experimental_rom_mode() &&
            goemon64::launcher::action_from_argument(argv[1]) == goemon64::launcher::Action::Play) {
#ifdef GOEMON_JAPAN_NATIVE
            constexpr bool native_is_japan = true;
#else
            constexpr bool native_is_japan = false;
#endif
            if (!goemon64::launcher::activate_experimental_rom(native_is_japan, supported_games[0].rom_hash)) {
                goemon64::show_error_message_box("Could not start ROM", "Import an experimental ROM first.");
                return EXIT_FAILURE;
            }
        }
#endif
        recompui::set_launcher_action(argv[1]);
    }
    recomp::register_config_path(goemon64::get_app_folder_path());

    // Register supported games and patches
    for (const auto& game : supported_games) {
        recomp::register_game(game);
    }

    // recomp::mods::register_embedded_mod("mm_recomp_dpad_builtin", { (const uint8_t*)(mm_recomp_dpad_builtin), std::size(mm_recomp_dpad_builtin)});

    REGISTER_FUNC(recomp_get_window_resolution);
    REGISTER_FUNC(recomp_get_target_aspect_ratio);
    REGISTER_FUNC(recomp_get_target_framerate);
    // REGISTER_FUNC(recomp_get_autosave_enabled);
    REGISTER_FUNC(recomp_get_analog_cam_enabled);
    REGISTER_FUNC(recomp_get_camera_inputs);
    REGISTER_FUNC(recomp_get_targeting_mode);
    REGISTER_FUNC(recomp_get_bgm_volume);
    REGISTER_FUNC(recomp_get_se_volume);
    REGISTER_FUNC(recomp_get_gyro_deltas);
    REGISTER_FUNC(recomp_get_mouse_deltas);
    REGISTER_FUNC(recomp_get_inverted_axes);
    REGISTER_FUNC(recomp_get_analog_inverted_axes);
    recompui::register_ui_exports();
    recomputil::register_data_api_exports();

#ifndef GOEMON_UNIFIED_REGIONS
    goemon64::register_overlays();
    goemon64::register_patches();
#endif
    recomputil::init_extended_actor_data();
    goemon64::load_config();

    recomp::rsp::callbacks_t rsp_callbacks{
        .get_rsp_microcode = get_rsp_microcode,
    };

    ultramodern::renderer::callbacks_t renderer_callbacks{
        .create_render_context = goemon64::renderer::create_render_context,
    };

    ultramodern::gfx_callbacks_t gfx_callbacks{
        .create_gfx = create_gfx,
        .create_window = create_window,
        .update_gfx = update_gfx,
    };

    ultramodern::audio_callbacks_t audio_callbacks{
        .queue_samples = queue_samples,
        .get_frames_remaining = get_frames_remaining,
        .set_frequency = set_frequency,
    };

    ultramodern::input::callbacks_t input_callbacks{
        .poll_input = recomp::poll_inputs,
        .get_input = recomp::get_n64_input,
        .set_rumble = recomp::set_rumble,
        .get_connected_device_info = recomp::get_connected_device_info,
    };

    ultramodern::events::callbacks_t thread_callbacks{
        .vi_callback = recomp::update_rumble,
        .gfx_init_callback = recompui::update_supported_options,
    };

    ultramodern::error_handling::callbacks_t error_handling_callbacks{
        .message_box = recompui::message_box,
    };

    ultramodern::threads::callbacks_t threads_callbacks{
        .get_game_thread_name = goemon64::get_game_thread_name,
    };

    // Register the texture pack content type with rt64.json as its content file.
    recomp::mods::ModContentType texture_pack_content_type{
        .content_filename = "rt64.json",
        .allow_runtime_toggle = true,
        .on_enabled = enable_texture_pack,
        .on_disabled = disable_texture_pack,
        .on_reordered = reorder_texture_pack,
    };
    auto texture_pack_content_type_id = recomp::mods::register_mod_content_type(texture_pack_content_type);

    // Register the .rtz texture pack file format with the previous content type as its only allowed content type.
    recomp::mods::register_mod_container_type("rtz", std::vector{ texture_pack_content_type_id }, false);

    recomp::start(
        project_version,
        {},
        rsp_callbacks,
        renderer_callbacks,
        audio_callbacks,
        input_callbacks,
        gfx_callbacks,
        thread_callbacks,
        error_handling_callbacks,
        threads_callbacks
    );

#ifdef GOEMON_JP_SDL_AUDIO_STAGE15_ACTIVE
    // Stage15: ONLY after recomp::start returns from normal PC app exit.
    // Guest/native joins are performed internally before this point.
    // This fence prevents late audio callback access to a closed SDL device.
    const bool jp_audio_leases_drained =
        jp_sdl_audio_gate.close_for_pc_exit(std::chrono::milliseconds{1000});
    const auto jp_audio_exit_state = jp_sdl_audio_gate.snapshot();
    unsigned jp_audio_before = 0;
    unsigned jp_audio_after = 0;
    bool jp_audio_device_closed = false;
    if (jp_audio_leases_drained && audio_device != 0) {
        jp_audio_before = SDL_GetQueuedAudioSize(audio_device);
        SDL_PauseAudioDevice(audio_device, 1);
        SDL_ClearQueuedAudio(audio_device);
        jp_audio_after = SDL_GetQueuedAudioSize(audio_device);
        SDL_CloseAudioDevice(audio_device);
        audio_device = 0;
        jp_audio_device_closed = true;
    }
    const bool jp_audio_exit_confirmed =
        jp_audio_leases_drained && jp_audio_device_closed &&
        jp_audio_after == 0;
    std::fprintf(stderr,
        "[Goemon JP SDL audio exit stage15] permit_drained=%u "
        "accepted=%llu rejected=%llu inflight=%llu queued_before=%u "
        "queued_after=%u device_closed=%u confirmed=%u; "
        "PC exit only, no live reset or generation rearm.\n",
        static_cast<unsigned>(jp_audio_leases_drained),
        static_cast<unsigned long long>(jp_audio_exit_state.accepted),
        static_cast<unsigned long long>(jp_audio_exit_state.rejected),
        static_cast<unsigned long long>(jp_audio_exit_state.in_flight),
        jp_audio_before, jp_audio_after,
        static_cast<unsigned>(jp_audio_device_closed),
        static_cast<unsigned>(jp_audio_exit_confirmed));
#endif

    NFD_Quit();

    if (preloaded) {
        release_preload(preload_context);
    }

#ifdef _WIN32
    // End high resolution timing period.
    timeEndPeriod(1);
#endif

#if defined(__linux__) && defined(GOEMON_JAPAN_NATIVE)
    goemon_jp::logging::finish_session();
#endif
    if (!goemon64::launcher::launch_requested_region()) {
        goemon64::show_error_message_box("Launch failed", "Could not start the other regional build.");
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
