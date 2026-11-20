#!/usr/bin/env python3
"""JP-only F6 cooperative pause/resume overlay for the integrated reset tree.

The SDL frontend remains responsive. This is an opt-in frame/event/guest-
scheduler pause, NOT a savestate and NOT an arbitrary native-stack freeze.
All source anchors are pinned and exact: fail at CMake configure on drift.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def once(src: str, old: str, new: str, label: str) -> str:
    count = src.count(old)
    if count != 1:
        raise ValueError(f"JP F6 pause {label}: expected exactly one source anchor, got {count}")
    return src.replace(old, new, 1)


PAUSE_TIMER_API = r"""// F6 changes the clock and the host-worker gate in a deliberate order:
// on pause freeze emulated time FIRST; on resume shift the timebase before
// releasing waiting guest/native workers. It never tears down RDRAM.
bool ultramodern::jp_set_guest_paused(bool pause) {
    const bool changed = jp_timer_epoch_clock.set_paused(pause);
    (void)goemon_jp::pause::controller().set_paused(pause);
    const auto snapshot = jp_timer_epoch_clock.snapshot();
    if (changed) {
        std::fprintf(stderr,
            "[Goemon JP pause] state=%s generation=%llu ticks=%llu "
            "guest_time=%llu pid=unchanged window=preserved; F6 toggles.\n",
            pause ? "PAUSED" : "RUNNING",
            static_cast<unsigned long long>(snapshot.generation),
            static_cast<unsigned long long>(snapshot.ticks),
            static_cast<unsigned long long>(snapshot.guest_time));
    }
    return changed;
}

bool ultramodern::jp_guest_is_paused() {
    return goemon_jp::pause::controller().is_paused();
}

"""


TIMER_SNAPSHOT = r"""
std::optional<goemon_jp::savestate::TimerCheckpoint> ultramodern::jp_snapshot_timers() {
    using goemon_jp::savestate::TimerCheckpoint;
    using goemon_jp::savestate::TimerCommandKind;
    std::optional<TimerCheckpoint> snapshot;
    (void)goemon_jp::pause::controller().inspect_parked(goemon_jp::pause::PauseWorker::Timer, [&] {
        const auto before = jp_timer_publisher_gate.snapshot();
        const auto clock = jp_timer_epoch_clock.saved_time();
        const auto actions = timer_context.action_queue.try_snapshot();
        const auto after = jp_timer_publisher_gate.snapshot();
        if (!clock || !actions || !after.open || before.generation != after.generation ||
                before.in_flight != 0 || after.in_flight != 0 || before.accepted != after.accepted) {
            return;
        }
        TimerCheckpoint captured{*clock, paused_active_timers, {}};
        captured.pending.reserve(actions->size());
        for (const auto& action : *actions) {
            if (const auto* add = std::get_if<AddTimerAction>(&action)) {
                captured.pending.push_back({TimerCommandKind::Add, add->timer});
            } else if (const auto* remove = std::get_if<RemoveTimerAction>(&action)) {
                captured.pending.push_back({TimerCommandKind::Remove, remove->timer});
            } else {
                return; // A shutdown action belongs to the old generation.
            }
        }
        snapshot = std::move(captured);
    });
    return snapshot;
}

"""


def timer(src: str) -> str:
    if "jp_set_guest_paused(" in src:
        raise ValueError("Timer source already paused")
    src = once(src,
        '#include "jp_timer_epoch_clock.hpp"\n',
        '#include "jp_timer_epoch_clock.hpp"\n'
        '#include "jp_pause_controller.hpp"\n'
        '#include <algorithm>\n'
        'extern std::atomic_bool exited;\n'
        'namespace ultramodern {\n'
        '    bool jp_set_guest_paused(bool);\n'
        '    bool jp_guest_is_paused();\n'
        '}\n',
        "clock+pause controller includes")
    src = once(src,
        'void ultramodern::jp_report_timer_epoch_stage18() {\n',
        PAUSE_TIMER_API + TIMER_SNAPSHOT + 'void ultramodern::jp_report_timer_epoch_stage18() {\n',
        "host pause state and clock setter")
    src = once(src,
        'void timer_thread(RDRAM_ARG std::vector<int32_t> initial_timers) {\n',
        '// Accessed only under the pause controller lock while the timer is parked.\n'
        'static std::vector<int32_t> paused_active_timers;\n\n'
        'void timer_thread(RDRAM_ARG std::vector<int32_t> initial_timers) {\n',
        "publish the timer owner's active addresses alongside pause acknowledgement")
    src = once(src,
        '    while (!shutdown_requested) {\n'
        '        // Empty the action queue\n',
        '    while (!shutdown_requested) {\n'
        '        // Timer actions and messages remain pending during pause.\n'
        '        // Exit/reset first releases the gate so shutdown is still joined.\n'
        '        (void)goemon_jp::pause::controller().wait_with_snapshot(\n'
        '            exited, goemon_jp::pause::PauseWorker::Timer, [&] {\n'
        '                paused_active_timers.assign(active_timers.begin(), active_timers.end());\n'
        '            });\n'
        '        // Empty the action queue\n',
        "timer worker safe boundary")
    src = once(src,
        '        while (active_timers.empty() && !shutdown_requested) {\n'
        '            if (timer_context.action_queue.wait_dequeue_timed(cur_action, std::chrono::milliseconds{10})) {\n'
        '                process_timer_action(cur_action);\n'
        '            }\n'
        '        }\n'
        '        if (shutdown_requested) {\n'
        '            break;\n'
        '        }\n',
        '        if (active_timers.empty()) {\n'
        '            if (timer_context.action_queue.wait_dequeue_timed(cur_action, std::chrono::milliseconds{10})) {\n'
        '                process_timer_action(cur_action);\n'
        '            }\n'
        '            continue;\n'
        '        }\n',
        "allow an empty timer worker to acknowledge the pause")
    src = once(src,
        '        if (wait_duration.count() >= 0 && timer_context.action_queue.wait_dequeue_timed(cur_action, wait_duration)) {\n',
        '        const auto maximum_wait = std::chrono::duration_cast<decltype(wait_duration)>(std::chrono::milliseconds{10});\n'
        '        if (wait_duration.count() >= 0 && timer_context.action_queue.wait_dequeue_timed(\n'
        '                cur_action, std::min(wait_duration, maximum_wait))) {\n',
        "bound the timer wait without altering guest time")
    src = once(src,
        '        else {\n'
        '            // Waiting for the timer completed, so send the timer\'s message to its message queue\n',
        '        else {\n'
        '            // Reinsert a timer before parking, or after a bounded wait before its deadline.\n'
        '            if (goemon_jp::pause::controller().is_paused() || time_now() < cur_timer->timestamp) {\n'
        '                active_timers.insert(cur_timer_);\n'
        '                continue;\n'
        '            }\n'
        '            // Waiting for the timer completed, so send the timer\'s message to its message queue\n',
        "park only after returning a pending timer to the queue")
    return "// JP F6 cooperative pause: timer worker, guest clock and N64 time.\n" + src


VIDEO_SNAPSHOT = r"""
std::optional<goemon_jp::savestate::VideoCheckpoint> ultramodern::jp_snapshot_video() {
    using goemon_jp::pause::PauseWorker;
    using namespace goemon_jp::savestate;
    constexpr auto video_workers = static_cast<uint32_t>(PauseWorker::Vi) |
        static_cast<uint32_t>(PauseWorker::Graphics);
    std::optional<VideoCheckpoint> result;
    (void)goemon_jp::pause::controller().inspect_parked_workers(video_workers, [&] {
        std::lock_guard lock(events_context.message_mutex);
        auto& vi = events_context.vi;
        if (vi.get_next_state()->mode == nullptr) return;
        VideoCheckpoint snapshot{};
        for (size_t i = 0; i < snapshot.buffers.size(); ++i) {
            const auto& buffer = vi.states[i];
            const auto mode = buffer.mode_address != NULLPTR ? VideoModeSource::Guest :
                buffer.mode != nullptr ? VideoModeSource::Dummy : VideoModeSource::None;
            snapshot.buffers[i] = {mode, buffer.mode_address, buffer.framebuffer,
                {buffer.mq, buffer.msg}, buffer.state, buffer.control, buffer.retrace_count};
        }
        snapshot.current_buffer = vi.cur_state;
        snapshot.field = vi.field;
        snapshot.remaining_retraces = vi.remaining_retraces;
        snapshot.total_retraces = total_vis;
        snapshot.registers = vi.regs;
        snapshot.presented_registers = vi.update_screen_regs;
        snapshot.sp = {events_context.sp.mq, events_context.sp.msg};
        snapshot.dp = {events_context.dp.mq, events_context.dp.msg};
        snapshot.ai = {events_context.ai.mq, events_context.ai.msg};
        snapshot.si = {events_context.si.mq, events_context.si.msg};
        result = snapshot;
    });
    return result;
}

"""


def events(src: str) -> str:
    if "JP F6 cooperative pause" in src:
        raise ValueError("Events source already paused")
    src = once(src,
        '#include "ultramodern/ultramodern.hpp"\n',
        '#include "ultramodern/ultramodern.hpp"\n'
        '#include "jp_pause_controller.hpp"\n',
        "native VI/gfx pause include")
    src = once(src,
        '    while (!exited) {\n'
        '        if (events_context.vi.waiting_for_restored_guest) {',
        '    while (!exited) {\n'
        '        if (!goemon_jp::pause::controller().wait_if_paused(exited, goemon_jp::pause::PauseWorker::Vi)) break;\n'
        '        if (events_context.vi.waiting_for_restored_guest) {',
        "VI frame boundary")
    src = once(src,
        '        ultramodern::sleep_until(next);\n'
        '        auto time_now = ultramodern::time_since_start();\n',
        '        ultramodern::sleep_until(next);\n'
        '        if (!goemon_jp::pause::controller().wait_if_paused(exited, goemon_jp::pause::PauseWorker::Vi)) break;\n'
        '        auto time_now = ultramodern::time_since_start();\n',
        "VI frame dispatch after wall-clock sleep")
    src = once(src,
        '        events_context.sp_task_queue.wait_dequeue(task);\n',
        '        (void)goemon_jp::pause::controller().wait_if_paused(exited, goemon_jp::pause::PauseWorker::Rsp);\n'
        '        if (!events_context.sp_task_queue.wait_dequeue_timed(task, std::chrono::milliseconds{10})) continue;\n',
        "SP task execution checkpoint")
    src = once(src,
        '    while (!exited) {\n'
        '        // Try to pull an action from the queue\n',
        '    while (!exited) {\n'
        '        if (!goemon_jp::pause::controller().wait_if_paused(exited, goemon_jp::pause::PauseWorker::Graphics)) break;\n'
        '        // Try to pull an action from the queue\n',
        "graphics action loop boundary")
    src = once(src,
        "uint64_t total_vis = 0;\n",
        "uint64_t total_vis = 0;\n" + VIDEO_SNAPSHOT,
        "copy the paused VI and presented frame registers together")
    return "// JP F6 cooperative pause: preserve last VI/frame/RSP and renderer.\n" + src


def messages(src: str) -> str:
    if "JP F6 cooperative pause" in src:
        raise ValueError("N64 message source already paused")
    src = once(src,
        '#include "ultramodern/ultramodern.hpp"\n',
        '#include "ultramodern/ultramodern.hpp"\n'
        '#include "jp_pause_controller.hpp"\n'
        'extern std::atomic_bool exited;\n',
        "external N64 message pause include")
    src = once(src,
        'void dequeue_external_messages(RDRAM_ARG1) {\n',
        'void dequeue_external_messages(RDRAM_ARG1) {\n'
        '    (void)goemon_jp::pause::controller().wait_if_paused(exited);\n',
        "external message batch checkpoint")
    # Stage4 changed the unbounded wait into a 10ms interruptible
    # guest-shutdown loop. Preserve that shutdown checkpoint.
    src = once(src,
        'void ultramodern::wait_for_external_message(RDRAM_ARG1) {\n',
        'void ultramodern::wait_for_external_message(RDRAM_ARG1) {\n'
        '    (void)goemon_jp::pause::controller().wait_if_paused(exited);\n',
        "guest message wait entry checkpoint")
    src = once(src,
        '        ultramodern::guest_shutdown_checkpoint();\n'
        '        if (!do_send(PASS_RDRAM to_send.mq, to_send.mesg, to_send.jam, false) && to_send.requeue_if_blocked) {\n'
        '            external_messages.enqueue(to_send);\n'
        '        }\n'
        '        return;\n',
        '        ultramodern::guest_shutdown_checkpoint();\n'
        '        (void)goemon_jp::pause::controller().wait_if_paused(exited);\n'
        '        if (!do_send(PASS_RDRAM to_send.mq, to_send.mesg, to_send.jam, false) && to_send.requeue_if_blocked) {\n'
        '            external_messages.enqueue(to_send);\n'
        '        }\n'
        '        return;\n',
        "external message wake checkpoint")
    timed_wait = '    if (auto delivery = external_messages.wait_dequeue_timed(to_send, std::chrono::milliseconds{millis}); delivery) {\n'
    src = once(src,
        timed_wait + '        ultramodern::guest_shutdown_checkpoint();\n',
        timed_wait + '        ultramodern::guest_shutdown_checkpoint();\n'
        '        (void)goemon_jp::pause::controller().wait_if_paused(exited);\n',
        "timed message wake checkpoint")
    src = once(src,
        '[[noreturn]] void ultramodern::jp_idle_message_loop(uint8_t* rdram) {\n'
        '    for (;;) {\n',
        '[[noreturn]] void ultramodern::jp_idle_message_loop(uint8_t* rdram) {\n'
        '    for (;;) {\n'
        '        {\n'
        '            const MessageContinuationScope continuation(rdram, {MessageCall::IdleWait, 0, 0});\n'
        '            (void)goemon_jp::pause::controller().wait_if_paused(exited, goemon_jp::pause::PauseWorker::IdleGuest);\n'
        '        }\n',
        "pause IDLE at the boundary before it consumes a message")
    src = once(src,
        '            external_messages.wait_for_work(std::chrono::milliseconds{10});\n',
        '            external_messages.wait_for_work(std::chrono::milliseconds{10});\n'
        '            (void)goemon_jp::pause::controller().wait_if_paused(exited, goemon_jp::pause::PauseWorker::IdleGuest);\n',
        "acknowledge a pause that lands while IDLE is waiting")
    return "// JP F6 cooperative pause: external message delivery to guest memory.\n" + src


def threads(src: str) -> str:
    if "JP F6 cooperative pause" in src:
        raise ValueError("OSThread source already paused")
    src = once(src,
        '#include "ultramodern/threads.hpp"\n',
        '#include "ultramodern/threads.hpp"\n'
        '#include "jp_pause_controller.hpp"\n'
        'extern std::atomic_bool exited;\n',
        "OSThread pause includes and exit flag")
    src = once(src,
        '    ultramodern::guest_shutdown_checkpoint();\n'
        '    // If this thread\'s context was replaced',
        '    ultramodern::guest_shutdown_checkpoint();\n'
        '    // Once an OSThread wakes, stop before resuming guest C code.\n'
        '    (void)goemon_jp::pause::controller().wait_if_paused(exited);\n'
        '    // If this thread\'s context was replaced',
        "OSThread context resume checkpoint")
    return "// JP F6 cooperative pause: OSThread dispatch/resume checkpoint.\n" + src


def recomp(src: str) -> str:
    if "JP F6 cooperative pause" in src:
        raise ValueError("Recomp lifecycle already paused")
    src = once(src,
        'namespace recomp { bool jp_request_integrated_reset(); }\n',
        'namespace recomp { bool jp_request_integrated_reset(); }\n'
        'namespace ultramodern { bool jp_set_guest_paused(bool); }\n',
        "recomp generation owner pause API")
    src = once(src,
        '    // End only the current guest-runner loop. Do NOT call\n',
        '    // F8 must release workers parked by F6 BEFORE entering joins.\n'
        '    (void)ultramodern::jp_set_guest_paused(false);\n'
        '    // End only the current guest-runner loop. Do NOT call\n',
        "F8 reset while paused releases guest workers")
    src = once(src,
        'void ultramodern::quit() {\n'
        '    exited.store(true);\n',
        'void ultramodern::quit() {\n'
        '    // Window close while F6-paused must not strand worker joins.\n'
        '    (void)ultramodern::jp_set_guest_paused(false);\n'
        '    exited.store(true);\n',
        "normal quit releases paused workers")
    return "// JP F6 cooperative pause: F8 reset and normal quit release the gate.\n" + src


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generated-dir", type=Path, required=True)
    args = parser.parse_args()
    d = args.generated_dir
    overlays = [
        ("timer_integrated.cpp", "timer_pause.cpp", timer),
        ("events_integrated.cpp", "events_pause.cpp", events),
        ("mesgqueue_integrated.cpp", "mesgqueue_pause.cpp", messages),
        ("threads_reset.cpp", "threads_pause.cpp", threads),
        ("recomp_integrated.cpp", "recomp_pause.cpp", recomp),
    ]
    for old_name, new_name, transform in overlays:
        original = (d / old_name).read_text(encoding="utf-8")
        modified = transform(original)
        target = d / new_name
        temp = target.with_suffix(".tmp")
        temp.write_text(modified, encoding="utf-8")
        temp.replace(target)
        print(f"[JP F6 pause] {old_name} -> {new_name}")


if __name__ == "__main__":
    main()
