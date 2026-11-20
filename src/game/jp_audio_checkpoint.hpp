#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>
#include <vector>

namespace goemon_jp::savestate {

inline constexpr std::size_t audio_tail_samples = 8;

struct AudioCheckpoint {
    std::uint32_t sample_rate;
    std::uint32_t output_sample_rate;
    std::array<float, audio_tail_samples> interpolation_tail;
    std::vector<float> pending_output;
};

inline bool valid_audio_checkpoint(const AudioCheckpoint& audio) {
    constexpr auto max_rate = static_cast<std::uint32_t>(std::numeric_limits<int>::max());
    return audio.sample_rate != 0 && audio.sample_rate <= max_rate &&
        audio.output_sample_rate != 0 && audio.output_sample_rate <= max_rate &&
        audio.pending_output.size() % 2 == 0 &&
        audio.pending_output.size() <= std::numeric_limits<std::uint32_t>::max() / sizeof(float);
}

} // namespace goemon_jp::savestate

namespace ultramodern {

std::optional<goemon_jp::savestate::AudioCheckpoint> jp_snapshot_audio();
// Stage after verified generation exit, before initializing audio for restored guests.
bool jp_prepare_audio_restore(const goemon_jp::savestate::AudioCheckpoint& state);

} // namespace ultramodern

namespace recomp {

std::optional<goemon_jp::savestate::AudioCheckpoint> jp_capture_audio_output(std::uint32_t guest_rate);
bool jp_restore_audio_output(const goemon_jp::savestate::AudioCheckpoint& state);

} // namespace recomp
