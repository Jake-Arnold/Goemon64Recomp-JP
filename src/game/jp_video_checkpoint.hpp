#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <span>

#include "ultramodern/renderer_context.hpp"

namespace goemon_jp::savestate {

struct InterruptTarget {
    std::int32_t queue;
    std::int32_t message;
};

enum class VideoModeSource : std::uint8_t {
    None,
    Dummy,
    Guest,
};

struct VideoBufferCheckpoint {
    VideoModeSource mode_source;
    std::int32_t mode_address;
    std::int32_t framebuffer;
    InterruptTarget retrace;
    std::uint32_t flags;
    std::uint32_t control;
    std::int32_t retrace_count;
};

struct VideoCheckpoint {
    std::array<VideoBufferCheckpoint, 2> buffers;
    std::int32_t current_buffer;
    std::int32_t field;
    std::int32_t remaining_retraces;
    std::uint64_t total_retraces;
    ultramodern::renderer::ViRegs registers;
    ultramodern::renderer::ViRegs presented_registers;
    InterruptTarget sp, dp, ai, si;
};

} // namespace goemon_jp::savestate

namespace ultramodern {

// The snapshot owner also parks guests and joins any device-side RAM writers.
std::optional<goemon_jp::savestate::VideoCheckpoint> jp_snapshot_video();

// Rearm the event generation first, then stage registers against restored RAM.
bool jp_prepare_video_restore(const goemon_jp::savestate::VideoCheckpoint& state,
    std::uint64_t generation, std::span<std::uint8_t> restored_ram);

} // namespace ultramodern
