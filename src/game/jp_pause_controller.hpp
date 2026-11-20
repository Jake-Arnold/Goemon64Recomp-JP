#pragma once

// JP-native cooperative pause gate.
//
// Stops dispatch at scheduler, VI, graphics, RSP, timer and message-queue
// boundaries. The SDL event loop remains responsive; the current guest
// generation and RDRAM remain allocated. This is not a savestate/snapshot,
// and a CPU-bound guest function with no runtime call is not preempted.
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <mutex>

namespace goemon_jp::pause {

enum class PauseWorker : std::uint32_t {
    Untracked = 0,
    Vi = 1U << 0,
    Rsp = 1U << 1,
    Graphics = 1U << 2,
    Timer = 1U << 3,
    IdleGuest = 1U << 4,
};

inline constexpr std::uint32_t native_dispatch_mask =
    static_cast<std::uint32_t>(PauseWorker::Vi) |
    static_cast<std::uint32_t>(PauseWorker::Rsp) |
    static_cast<std::uint32_t>(PauseWorker::Graphics) |
    static_cast<std::uint32_t>(PauseWorker::Timer);

class Controller final {
public:
    Controller() = default;
    Controller(const Controller&) = delete;
    Controller& operator=(const Controller&) = delete;

    [[nodiscard]] bool is_paused() const noexcept {
        return paused_.load(std::memory_order_acquire);
    }

    [[nodiscard]] std::uint64_t transitions() const noexcept {
        return transitions_.load(std::memory_order_acquire);
    }

    // Called by the SDL event thread when F6 toggles. Never destroys guest
    // state, cancels native threads or replaces the SDL window.
    [[nodiscard]] bool set_paused(bool pause) noexcept {
        bool changed;
        {
            std::lock_guard<std::mutex> guard(mutex_);
            changed = paused_.load(std::memory_order_relaxed) != pause;
            if (changed) {
                parked_workers_ = 0;
                paused_.store(pause, std::memory_order_release);
                transitions_.fetch_add(1, std::memory_order_acq_rel);
            }
        }
        if (!pause) {
            cv_.notify_all();
        }
        return changed;
    }

    // Dispatch acknowledgements cover these loops, including any work they
    // took from their queues. Worker-owned queues and the renderer still
    // require their own snapshot support.
    [[nodiscard]] std::uint32_t parked_workers() const noexcept {
        std::lock_guard lock(mutex_);
        return parked_workers_;
    }

    // A host worker calls this only at a safe runtime boundary, not while
    // holding the game runtime's message/graphics locks. Wake periodically
    // so normal PC exit cannot be stranded if a caller omitted resume.
    [[nodiscard]] bool wait_if_paused(
        const std::atomic_bool& process_exiting,
        PauseWorker worker = PauseWorker::Untracked) noexcept {
        return wait_with_snapshot(process_exiting, worker, [] {});
    }

    // Capture worker-owned state before publishing its acknowledgement, once
    // per pause. The callback and the inspector share the controller lock.
    template <typename Capture>
    [[nodiscard]] bool wait_with_snapshot(
        const std::atomic_bool& process_exiting, PauseWorker worker, Capture capture) {
        if (!is_paused()) {
            return !process_exiting.load(std::memory_order_acquire);
        }
        std::unique_lock<std::mutex> lock(mutex_);
        const auto flag = static_cast<std::uint32_t>(worker);
        while (paused_.load(std::memory_order_acquire) &&
               !process_exiting.load(std::memory_order_acquire)) {
            if (flag != 0 && (parked_workers_ & flag) == 0) {
                capture();
                parked_workers_ |= flag;
            }
            cv_.wait_for(lock, std::chrono::milliseconds{10});
        }
        parked_workers_ &= ~flag;
        return !process_exiting.load(std::memory_order_acquire);
    }

    template <typename Inspect>
    [[nodiscard]] bool inspect_parked(PauseWorker worker, Inspect inspect) const {
        std::lock_guard lock(mutex_);
        if ((parked_workers_ & static_cast<std::uint32_t>(worker)) == 0) {
            return false;
        }
        inspect();
        return true;
    }

private:
    std::atomic_bool paused_{false};
    std::atomic_uint64_t transitions_{0};
    mutable std::mutex mutex_;
    std::condition_variable cv_;
    std::uint32_t parked_workers_ = 0;
};

// One C++17 inline variable shared by the statically linked JP runtime and
// frontend translation units; never create one instance per worker.
inline Controller global_controller{};
[[nodiscard]] inline Controller& controller() noexcept {
    return global_controller;
}

} // namespace goemon_jp::pause
