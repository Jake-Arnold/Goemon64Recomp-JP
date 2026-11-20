#pragma once

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <mutex>
#include <optional>
#include <span>
#include <utility>
#include <vector>

namespace goemon_jp::savestate {

struct ExternalMessage {
    std::int32_t mq;
    std::int32_t mesg;
    bool jam;
    bool requeue_if_blocked;

    bool operator==(const ExternalMessage&) const = default;
};

// Includes messages being moved into guest RAM in the admission check.
template <typename Record>
class CheckpointQueue {
public:
    class Delivery {
        friend class CheckpointQueue;
        explicit Delivery(CheckpointQueue* queue) : queue_(queue) {}

    public:
        Delivery(const Delivery&) = delete;
        Delivery& operator=(const Delivery&) = delete;
        Delivery(Delivery&& other) noexcept : queue_(std::exchange(other.queue_, nullptr)) {}

        ~Delivery() {
            if (queue_) {
                std::lock_guard lock(queue_->mutex_);
                --queue_->active_deliveries_;
            }
        }

        explicit operator bool() const { return queue_ != nullptr; }

        // A batch retains the admission count while temporarily holding retry messages.
        bool try_dequeue(Record& message) {
            std::lock_guard lock(queue_->mutex_);
            return queue_->pop_locked(message);
        }

    private:
        CheckpointQueue* queue_;
    };

    void enqueue(Record message) {
        {
            std::lock_guard lock(mutex_);
            pending_.push_back(message);
        }
        work_.notify_one();
    }

    [[nodiscard]] Delivery begin_batch() {
        std::lock_guard lock(mutex_);
        ++active_deliveries_;
        return Delivery(this);
    }

    [[nodiscard]] Delivery try_dequeue(Record& message) {
        std::lock_guard lock(mutex_);
        return take_locked(message);
    }

    template <typename Rep, typename Period>
    [[nodiscard]] Delivery wait_dequeue_timed(Record& message, std::chrono::duration<Rep, Period> timeout) {
        std::unique_lock lock(mutex_);
        work_.wait_for(lock, timeout, [this] { return !pending_.empty(); });
        return take_locked(message);
    }

    void wait_for_work(std::chrono::milliseconds timeout) {
        std::unique_lock lock(mutex_);
        work_.wait_for(lock, timeout, [this] { return !pending_.empty(); });
    }

    [[nodiscard]] std::optional<std::vector<Record>> try_snapshot() const {
        std::lock_guard lock(mutex_);
        if (active_deliveries_ != 0) {
            return std::nullopt;
        }
        return std::vector<Record>(pending_.begin(), pending_.end());
    }

    // Only the generation owner may restore, with producers and guests stopped or parked.
    [[nodiscard]] bool restore_into_empty(std::span<const Record> messages) {
        std::deque<Record> restored(messages.begin(), messages.end());
        {
            std::lock_guard lock(mutex_);
            if (active_deliveries_ != 0 || !pending_.empty()) {
                return false;
            }
            pending_.swap(restored);
        }
        work_.notify_one();
        return true;
    }

    [[nodiscard]] std::optional<std::size_t> discard_after_join() {
        std::lock_guard lock(mutex_);
        if (active_deliveries_ != 0) {
            return std::nullopt;
        }
        const auto removed = pending_.size();
        pending_.clear();
        return removed;
    }

private:
    bool pop_locked(Record& message) {
        if (pending_.empty()) {
            return false;
        }
        message = pending_.front();
        pending_.pop_front();
        return true;
    }

    Delivery take_locked(Record& message) {
        if (!pop_locked(message)) {
            return Delivery(nullptr);
        }
        ++active_deliveries_;
        return Delivery(this);
    }

    mutable std::mutex mutex_;
    std::condition_variable work_;
    std::deque<Record> pending_;
    std::size_t active_deliveries_ = 0;
};

using ExternalMessageQueue = CheckpointQueue<ExternalMessage>;

} // namespace goemon_jp::savestate

namespace ultramodern {

// This is a queue-only snapshot. The caller coordinates the other runtime owners.
std::optional<std::vector<goemon_jp::savestate::ExternalMessage>> jp_snapshot_external_messages();
bool jp_restore_external_messages(std::span<const goemon_jp::savestate::ExternalMessage> messages);

} // namespace ultramodern
