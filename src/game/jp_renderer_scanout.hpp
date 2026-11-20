#pragma once

#include <algorithm>
#include <cstdint>
#include <optional>
#include <span>
#include <vector>

#include "ultramodern/renderer_context.hpp"

namespace goemon_jp::savestate {

struct RendererScanout {
    // Early presentation can use an older frame than the current VI registers.
    ultramodern::renderer::ViRegs vi;
    // VI's origin can point one or two rows into the framebuffer.
    std::uint32_t address;
    std::uint32_t width;
    std::uint32_t height;
    std::uint32_t bytes_per_pixel;
    std::vector<std::uint8_t> pixels;
};

// Use only against a fresh RAM image before restoring the VI/renderer owners.
inline bool restore_renderer_scanout(const RendererScanout& scanout, std::span<std::uint8_t> rdram) {
    const auto& vi = scanout.vi;
    const auto pixel_type = vi.VI_STATUS_REG & 3U;
    if (pixel_type != 2 && pixel_type != 3) {
        return false;
    }
    const auto pixel_bytes = 1U << (pixel_type - 1);
    const bool interlaced_odd = (vi.VI_STATUS_REG & 0x40U) && (vi.VI_V_CURRENT_LINE_REG & 1U);
    const auto row_offset = std::uint64_t{vi.VI_WIDTH_REG} * pixel_bytes * (interlaced_odd ? 2 : 1);
    const auto base = row_offset <= vi.VI_ORIGIN_REG ? vi.VI_ORIGIN_REG - row_offset : vi.VI_ORIGIN_REG;
    if (scanout.address != base || scanout.bytes_per_pixel != pixel_bytes ||
            scanout.width == 0 || scanout.width > vi.VI_WIDTH_REG || scanout.height == 0 ||
            scanout.address > rdram.size() || scanout.pixels.empty() ||
            scanout.pixels.size() > rdram.size() - scanout.address) {
        return false;
    }
    const auto pixels = scanout.pixels.size() / pixel_bytes;
    if (scanout.pixels.size() % pixel_bytes != 0 || pixels % scanout.width != 0 ||
            pixels / scanout.width != scanout.height) {
        return false;
    }
    std::copy(scanout.pixels.begin(), scanout.pixels.end(), rdram.begin() + scanout.address);
    return true;
}

} // namespace goemon_jp::savestate

namespace ultramodern {
// Available only once the native graphics dispatcher has acknowledged a pause.
std::optional<goemon_jp::savestate::RendererScanout> jp_snapshot_renderer_scanout();
}

namespace recomp {
std::optional<goemon_jp::savestate::RendererScanout> jp_capture_renderer_scanout(
    ultramodern::renderer::RendererContext& renderer);
}
