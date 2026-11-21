#include <cmath>

#include "recomp.h"
#include "librecomp/overlays.hpp"
#include "goemon_config.h"
#include "recomp_input.h"
#include "recomp_ui.h"
#include "goemon_render.h"
#include "goemon_sound.h"
#include "librecomp/helpers.hpp"
#include "../patches/input.h"
#include "../patches/graphics.h"
#include "../patches/sound.h"
#include "ultramodern/ultramodern.hpp"
#include "ultramodern/config.hpp"
#ifdef GOEMON_UNIFIED_REGIONS
#include "usa_native_state_trace.hpp"
#endif

extern "C" void recomp_update_inputs(uint8_t* rdram, recomp_context* ctx) {
    recomp::poll_inputs();
}

extern "C" void recomp_puts(uint8_t* rdram, recomp_context* ctx) {
    PTR(char) cur_str = _arg<0, PTR(char)>(rdram, ctx);
    u32 length = _arg<1, u32>(rdram, ctx);

    for (u32 i = 0; i < length; i++) {
        fputc(MEM_B(i, (gpr)cur_str), stdout);
    }
}

extern "C" void recomp_exit(uint8_t* rdram, recomp_context* ctx) {
    ultramodern::quit();
}

extern "C" void recomp_get_gyro_deltas(uint8_t* rdram, recomp_context* ctx) {
    float* x_out = _arg<0, float*>(rdram, ctx);
    float* y_out = _arg<1, float*>(rdram, ctx);

    recomp::get_gyro_deltas(x_out, y_out);
}

extern "C" void recomp_get_mouse_deltas(uint8_t* rdram, recomp_context* ctx) {
    float* x_out = _arg<0, float*>(rdram, ctx);
    float* y_out = _arg<1, float*>(rdram, ctx);

    recomp::get_mouse_deltas(x_out, y_out);
}

extern "C" void recomp_powf(uint8_t* rdram, recomp_context* ctx) {
    float a = _arg<0, float>(rdram, ctx);
    float b = ctx->f14.fl; //_arg<1, float>(rdram, ctx);

    _return(ctx, std::pow(a, b));
}

extern "C" void recomp_get_target_framerate(uint8_t* rdram, recomp_context* ctx) {
    int frame_divisor = _arg<0, u32>(rdram, ctx);

    _return(ctx, ultramodern::get_target_framerate(60 / frame_divisor));
}

extern "C" void recomp_get_window_resolution(uint8_t* rdram, recomp_context* ctx) {
    int width, height;
    recompui::get_window_size(width, height);

    gpr width_out = _arg<0, PTR(u32)>(rdram, ctx);
    gpr height_out = _arg<1, PTR(u32)>(rdram, ctx);

    MEM_W(0, width_out) = (u32)width;
    MEM_W(0, height_out) = (u32)height;
}

extern "C" void goemon_jp_widen_camera_scissor(int32_t* upper, int32_t* lower) {
    if (ultramodern::renderer::get_graphics_config().ar_option !=
        ultramodern::renderer::AspectRatio::Expand) {
        return;
    }
    // The original JP camera writes (8, 16)–(312, 224) in quarter-pixel
    // coordinates. Preserve other scissor rectangles, including cutscenes.
    if (static_cast<uint32_t>(*upper) != 0xED020040u ||
        static_cast<uint32_t>(*lower) != 0x004E0380u) {
        return;
    }
    int width, height;
    recompui::get_window_size(width, height);
    if (height > 0 && 3LL * width > 4LL * height) {
        *upper = static_cast<int32_t>(0xED000000u);
        *lower = 0x005003C0;
    }
}

extern "C" void recomp_get_target_aspect_ratio(uint8_t* rdram, recomp_context* ctx) {
    ultramodern::renderer::GraphicsConfig graphics_config = ultramodern::renderer::get_graphics_config();
    float original = _arg<0, float>(rdram, ctx);
    int width, height;
    recompui::get_window_size(width, height);

    switch (graphics_config.ar_option) {
        case ultramodern::renderer::AspectRatio::Original:
        default:
            _return(ctx, original);
            return;
        case ultramodern::renderer::AspectRatio::Expand:
            _return(ctx, std::max(static_cast<float>(width) / height, original));
            return;
    }
}

extern "C" void recomp_get_targeting_mode(uint8_t* rdram, recomp_context* ctx) {
    _return(ctx, static_cast<int>(goemon64::get_targeting_mode()));
}

extern "C" void recomp_get_bgm_volume(uint8_t* rdram, recomp_context* ctx) {
    _return(ctx, goemon64::get_bgm_volume() / 100.0f);
}

extern "C" void recomp_get_se_volume(uint8_t* rdram, recomp_context* ctx) {
    _return(ctx, goemon64::get_se_volume() / 100.0f);
}

extern "C" void recomp_time_us(uint8_t* rdram, recomp_context* ctx) {
    _return(ctx, static_cast<u32>(std::chrono::duration_cast<std::chrono::microseconds>(ultramodern::time_since_start()).count()));
}

extern "C" void recomp_get_autosave_enabled(uint8_t* rdram, recomp_context* ctx) {
    _return(ctx, static_cast<s32>(goemon64::get_autosave_mode() == goemon64::AutosaveMode::On));
}

extern "C" void recomp_load_overlays(uint8_t * rdram, recomp_context * ctx) {
    u32 rom = _arg<0, u32>(rdram, ctx);
    PTR(void) ram = _arg<1, PTR(void)>(rdram, ctx);
    u32 size = _arg<2, u32>(rdram, ctx);

#ifdef GOEMON_UNIFIED_REGIONS
    // Registration request only; original MIPS copy/decompression/relocation
    // happens later in patched func_80001C00_2800.
    goemon64::usa_native_trace::observe_overlay_registration_request(
        rdram, rom, static_cast<std::uint32_t>(ram), size);
#endif
    load_overlays(rom, ram, size);
}

// Called from the patched USA MIPS loader after copy/decompress,
// overlay_apply_relocations and tail zero-fill return. It does not
// perform an independent content verification.
extern "C" void recomp_file_loader_postprocess(uint8_t* rdram, recomp_context* ctx) {
#ifdef GOEMON_UNIFIED_REGIONS
    goemon64::usa_native_trace::observe_file_loader_postprocess(
        rdram,
        _arg<0, u32>(rdram, ctx),
        _arg<1, u32>(rdram, ctx),
        static_cast<std::uint32_t>(_arg<2, PTR(void)>(rdram, ctx)),
        _arg<3, u32>(rdram, ctx));
#else
    (void)rdram;
    (void)ctx;
#endif
}

extern "C" void recomp_unload_overlays(uint8_t *rdram, recomp_context *ctx) {
    PTR(void) vram_addr = _arg<0, PTR(void)>(rdram, ctx);
    u32 size = _arg<1, u32>(rdram, ctx);

    unload_overlays(vram_addr, size);
}

extern "C" void recomp_high_precision_fb_enabled(uint8_t * rdram, recomp_context * ctx) {
    _return(ctx, static_cast<s32>(goemon64::renderer::RT64HighPrecisionFBEnabled()));
}

extern "C" void recomp_get_resolution_scale(uint8_t* rdram, recomp_context* ctx) {
    _return(ctx, ultramodern::get_resolution_scale());
}

extern "C" void recomp_get_inverted_axes(uint8_t* rdram, recomp_context* ctx) {
    s32* x_out = _arg<0, s32*>(rdram, ctx);
    s32* y_out = _arg<1, s32*>(rdram, ctx);

    goemon64::CameraInvertMode mode = goemon64::get_camera_invert_mode();

    *x_out = (mode == goemon64::CameraInvertMode::InvertX || mode == goemon64::CameraInvertMode::InvertBoth);
    *y_out = (mode == goemon64::CameraInvertMode::InvertY || mode == goemon64::CameraInvertMode::InvertBoth);
}

extern "C" void recomp_get_analog_inverted_axes(uint8_t* rdram, recomp_context* ctx) {
    s32* x_out = _arg<0, s32*>(rdram, ctx);
    s32* y_out = _arg<1, s32*>(rdram, ctx);

    goemon64::CameraInvertMode mode = goemon64::get_analog_camera_invert_mode();

    *x_out = (mode == goemon64::CameraInvertMode::InvertX || mode == goemon64::CameraInvertMode::InvertBoth);
    *y_out = (mode == goemon64::CameraInvertMode::InvertY || mode == goemon64::CameraInvertMode::InvertBoth);
}

extern "C" void recomp_get_analog_cam_enabled(uint8_t* rdram, recomp_context* ctx) {
    _return<s32>(ctx, goemon64::get_analog_cam_mode() == goemon64::AnalogCamMode::On);
}

extern "C" void recomp_get_camera_inputs(uint8_t* rdram, recomp_context* ctx) {
    float* x_out = _arg<0, float*>(rdram, ctx);
    float* y_out = _arg<1, float*>(rdram, ctx);

    // TODO expose this in the menu
    constexpr float radial_deadzone = 0.05f;

    float x, y;

    recomp::get_right_analog(&x, &y);

    float magnitude = sqrtf(x * x + y * y);

    if (magnitude < radial_deadzone) {
        *x_out = 0.0f;
        *y_out = 0.0f;
    }
    else {
        float x_normalized = x / magnitude;
        float y_normalized = y / magnitude;

        *x_out = x_normalized * ((magnitude - radial_deadzone) / (1 - radial_deadzone));
        *y_out = y_normalized * ((magnitude - radial_deadzone) / (1 - radial_deadzone));
    }
}

extern "C" void recomp_set_right_analog_suppressed(uint8_t* rdram, recomp_context* ctx) {
    s32 suppressed = _arg<0, s32>(rdram, ctx);

    recomp::set_right_analog_suppressed(suppressed);
}
