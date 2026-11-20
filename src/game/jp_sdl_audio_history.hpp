#pragma once

#include <limits>
#include <optional>
#include <span>
#include <vector>

#include "SDL_audio.h"

namespace goemon_jp::savestate {

// SDL consumes a prefix of the playback buffer. Keep a copy of what was
// queued so the unread suffix can be recovered at a paused device boundary.
// The owner serializes device operations and access to this history.
class SdlAudioHistory {
public:
    bool enqueue(SDL_AudioDeviceID device, std::span<const float> samples) {
        if (samples.size_bytes() > std::numeric_limits<Uint32>::max()) {
            return false;
        }
        if (samples.empty()) {
            return true;
        }
        const auto pending_bytes = SDL_GetQueuedAudioSize(device);
        if (pending_bytes == 0) {
            clear_history();
        } else if (valid_ && contains(pending_bytes)) {
            queued_.erase(queued_.begin(), queued_.end() - pending_bytes / sizeof(float));
        } else {
            valid_ = false;
            queued_.clear();
        }

        const auto old_size = queued_.size();
        if (valid_) {
            queued_.insert(queued_.end(), samples.begin(), samples.end());
        }
        if (SDL_QueueAudio(device, samples.data(), static_cast<Uint32>(samples.size_bytes())) != 0) {
            queued_.resize(old_size);
            return false;
        }
        return true;
    }

    std::optional<std::vector<float>> snapshot(SDL_AudioDeviceID device) const {
        if (SDL_GetAudioDeviceStatus(device) != SDL_AUDIO_PAUSED) {
            return std::nullopt;
        }
        const auto pending_bytes = SDL_GetQueuedAudioSize(device);
        if (pending_bytes == 0) {
            return std::vector<float>{};
        }
        if (!valid_ || !contains(pending_bytes)) {
            return std::nullopt;
        }
        return std::vector<float>(queued_.end() - pending_bytes / sizeof(float), queued_.end());
    }

    bool clear(SDL_AudioDeviceID device) {
        if (SDL_GetAudioDeviceStatus(device) != SDL_AUDIO_PAUSED) {
            return false;
        }
        SDL_ClearQueuedAudio(device);
        clear_history();
        return SDL_GetQueuedAudioSize(device) == 0;
    }

    bool restore(SDL_AudioDeviceID device, std::span<const float> samples) {
        return clear(device) && (samples.empty() || enqueue(device, samples));
    }

private:
    bool contains(std::size_t bytes) const {
        return bytes % sizeof(float) == 0 && bytes / sizeof(float) <= queued_.size();
    }

    void clear_history() {
        queued_.clear();
        valid_ = true;
    }

    std::vector<float> queued_;
    bool valid_ = true;
};

} // namespace goemon_jp::savestate
