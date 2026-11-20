#pragma once

#include <mutex>

#include "hle/rt64_application.h"
#include "../game/jp_renderer_scanout.hpp"

namespace goemon_jp::savestate {

// The graphics dispatcher must be parked so no new RT64 work can be submitted.
// Try, rather than wait, on both RT64 owners so F5 never stalls the SDL pump.
inline std::optional<RendererScanout> capture_rt64_scanout(RT64::Application& app) {
    if (!app.emulatorConfig.framebuffer.renderToRAM) {
        return std::nullopt;
    }
    auto& workload = *app.workloadQueue;
    auto& present = *app.presentQueue;
    std::unique_lock work_lock(workload.threadMutex, std::try_to_lock);
    std::unique_lock present_lock(present.threadMutex, std::try_to_lock);
    if (!work_lock || !present_lock) {
        return std::nullopt;
    }

    {
        std::scoped_lock ids(workload.workloadIdMutex, present.presentIdMutex);
        if (workload.workloadId < app.state->workloadId ||
                present.presentId != app.state->presentId || present.presentId == 0) {
            return std::nullopt;
        }
    }

    const auto& frame = present.presents[present.previousWriteCursor()];
    const auto& vi = frame.screenVI;
    if (frame.presentId != app.state->presentId || frame.debuggerFramebuffer.view || frame.paused ||
            !vi.visible()) {
        return std::nullopt;
    }
    const auto size = vi.fbSize();
    const auto format = vi.fbSiz();
    if (size.x == 0 || size.y == 0 || (format != G_IM_SIZ_16b && format != G_IM_SIZ_32b)) {
        return std::nullopt;
    }
    const auto pixel_bytes = 1U << (format - 1);
    const auto address = vi.fbAddress();
    constexpr std::size_t native_ram_size = std::size_t{RT64::RDRAMSize} + 1;
    if (address >= native_ram_size || frame.storage.size() > native_ram_size - address ||
            frame.storage.size() % pixel_bytes != 0 ||
            frame.storage.size() / pixel_bytes / size.x != size.y ||
            frame.storage.size() / pixel_bytes % size.x != 0) {
        return std::nullopt;
    }

    const ultramodern::renderer::ViRegs presented{
        .VI_STATUS_REG = vi.status.word,
        .VI_ORIGIN_REG = vi.origin,
        .VI_WIDTH_REG = vi.width,
        .VI_INTR_REG = vi.intr,
        .VI_V_CURRENT_LINE_REG = vi.vCurrentLine,
        .VI_TIMING_REG = vi.burst.word,
        .VI_V_SYNC_REG = vi.vSync,
        .VI_H_SYNC_REG = vi.hSync.word,
        .VI_LEAP_REG = vi.leap.word,
        .VI_H_START_REG = vi.hRegion.word,
        .VI_V_START_REG = vi.vRegion.word,
        .VI_V_BURST_REG = vi.vBurst.word,
        .VI_X_SCALE_REG = vi.xTransform.word,
        .VI_Y_SCALE_REG = vi.yTransform.word,
    };
    return RendererScanout{presented, address, size.x, size.y, pixel_bytes, frame.storage};
}

} // namespace goemon_jp::savestate
