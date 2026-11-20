// ROM-free host verification of the F6 clock freeze and worker checkpoints.
#define GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE
#include "../../src/game/jp_timer_epoch_clock.hpp"
#include "../../src/game/jp_pause_controller.hpp"

#include <atomic>
#include <cassert>
#include <chrono>
#include <cstdio>
#include <thread>

using goemon_jp::reset::TimerEpochClock;
using goemon_jp::pause::controller;
using namespace std::chrono_literals;

int main() {
    TimerEpochClock clock{};
    std::this_thread::sleep_for(3ms);
    const auto before = clock.ticks_now();
    assert(before > 0);

    assert(clock.set_paused(true));
    const auto frozen = clock.snapshot();
    const auto frozen_time = clock.guest_time();
    std::this_thread::sleep_for(30ms);
    assert(clock.ticks_now() == frozen.ticks);
    assert(clock.guest_time() == frozen_time);
    assert(clock.snapshot().guest_time == frozen.guest_time);
    assert(clock.since_start() >= 0ns);
    assert(!clock.set_paused(true));  // repeated key does not move epoch

    assert(clock.set_paused(false));
    assert(!clock.set_paused(false));
    const auto resumed = clock.ticks_now();
    // The 30ms pause must NOT count toward the N64 timer.
    assert(resumed >= frozen.ticks);
    assert(resumed - frozen.ticks < 46875ull * 20ull);
    std::this_thread::sleep_for(4ms);
    assert(clock.ticks_now() > resumed);

    // A host worker must truly park while paused and then resume. The UI
    // event loop is intentionally not one of these parked workers.
    std::atomic_bool exiting{false};
    std::atomic_bool started{false};
    std::atomic_bool finished{false};
    assert(controller().set_paused(true));
    std::thread worker([&] {
        started.store(true, std::memory_order_release);
        const bool active = controller().wait_if_paused(exiting, goemon_jp::pause::PauseWorker::Vi);
        finished.store(active, std::memory_order_release);
    });
    while (!started.load(std::memory_order_acquire)) std::this_thread::yield();
    std::this_thread::sleep_for(15ms);
    assert(!finished.load(std::memory_order_acquire));
    assert(controller().is_paused());
    assert(controller().parked_workers() ==
        static_cast<std::uint32_t>(goemon_jp::pause::PauseWorker::Vi));
    assert(controller().set_paused(false));
    assert(controller().parked_workers() == 0);
    worker.join();
    assert(finished.load(std::memory_order_acquire));
    assert(controller().transitions() == 2);

    // Even if a caller forgets to release the gate, process exit wakes
    // worker checkpoints via a bounded condition-variable wait.
    started.store(false);
    finished.store(false);
    assert(controller().set_paused(true));
    std::thread exiting_worker([&] {
        started.store(true, std::memory_order_release);
        const bool active = controller().wait_if_paused(exiting);
        finished.store(!active, std::memory_order_release);
    });
    while (!started.load(std::memory_order_acquire)) std::this_thread::yield();
    exiting.store(true, std::memory_order_release);
    exiting_worker.join();
    assert(finished.load(std::memory_order_acquire));
    assert(controller().set_paused(false));

    // Timer epoch rearm resets paused flags after the complete stop proof.
    assert(clock.set_paused(true));
    const TimerEpochClock::StopProof all_joined{true,true,true,true};
    assert(clock.rebase_after_verified_exit(1, all_joined));
    assert(clock.snapshot().generation == 2);
    assert(!clock.is_paused());

    std::puts("JP F6 pause/resume host PASS: clock_frozen=1 no_catchup=1 worker_parked=1 exit_wakes=1 epoch_rearm=1");
}
