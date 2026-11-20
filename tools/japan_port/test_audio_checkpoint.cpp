#include <cassert>
#include <latch>
#include <thread>

#include "jp_audio_checkpoint.hpp"
#include "ultramodern/ultramodern.hpp"

namespace ultramodern {
bool jp_rearm_audio_after_verified_exit();
}

namespace {
using goemon_jp::savestate::AudioCheckpoint;

AudioCheckpoint output{48000, 48000, {}, {}};
std::latch callback_entered(1);
std::latch release_callback(1);
bool block_submission = false;
bool output_paused = false;

void set_frequency(std::uint32_t frequency) {
    output.sample_rate = frequency;
}

void submit(int16_t* data, std::size_t samples) {
    if (block_submission) {
        callback_entered.count_down();
        release_callback.wait();
    }
    output.pending_output.insert(output.pending_output.end(), data, data + samples);
}

std::size_t frames_remaining() {
    return output.pending_output.size() / 2;
}
}

namespace recomp {

std::optional<AudioCheckpoint> jp_capture_audio_output(std::uint32_t guest_rate) {
    if (!output_paused || output.sample_rate != guest_rate) {
        return std::nullopt;
    }
    return output;
}

bool jp_restore_audio_output(const AudioCheckpoint& state) {
    output_paused = true;
    output = state;
    return true;
}

} // namespace recomp

void test_audio_checkpoint() {
    constexpr auto buffer_address = static_cast<std::int32_t>(0x80000000u);
    alignas(std::int16_t) std::int16_t samples[1024]{};
    samples[0] = 17;
    samples[1] = 23;
    auto* rdram = reinterpret_cast<std::uint8_t*>(samples);

    ultramodern::set_audio_callbacks({submit, frames_remaining, set_frequency});
    ultramodern::init_audio();
    ultramodern::set_audio_frequency(22050);
    assert(!ultramodern::jp_snapshot_audio());
    output_paused = true;

    block_submission = true;
    std::thread producer([&] { ultramodern::queue_audio_buffer(rdram, buffer_address, sizeof(samples)); });
    callback_entered.wait();
    assert(!ultramodern::jp_snapshot_audio());
    release_callback.count_down();
    producer.join();
    block_submission = false;
    output.interpolation_tail[0] = 0.5f;

    const auto snapshot = ultramodern::jp_snapshot_audio();
    assert(snapshot && snapshot->sample_rate == 22050);
    assert(snapshot->pending_output.size() == 1024 && snapshot->pending_output[0] == 17);
    assert(snapshot->interpolation_tail[0] == 0.5f);
    const auto saved_length = ultramodern::get_remaining_audio_bytes();
    assert(saved_length > 0);
    assert(!ultramodern::jp_prepare_audio_restore(*snapshot));

    assert(ultramodern::jp_rearm_audio_after_verified_exit());
    assert(!ultramodern::jp_snapshot_audio());
    output = AudioCheckpoint{48000, 48000, {}, {99, 99}};
    auto malformed = *snapshot;
    malformed.sample_rate = 0;
    assert(!ultramodern::jp_prepare_audio_restore(malformed));
    assert(ultramodern::jp_prepare_audio_restore(*snapshot));
    assert(!ultramodern::jp_prepare_audio_restore(*snapshot));
    ultramodern::init_audio();
    assert(output_paused && output.sample_rate == 22050);
    assert(output.pending_output == snapshot->pending_output);
    assert(output.interpolation_tail == snapshot->interpolation_tail);
    assert(ultramodern::get_remaining_audio_bytes() == saved_length);
    ultramodern::queue_audio_buffer(rdram, buffer_address, 2 * sizeof(std::int16_t));
    assert(output.pending_output.size() == 1026 && output.pending_output.back() == 23);
}
