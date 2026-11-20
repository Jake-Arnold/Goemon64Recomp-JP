#!/usr/bin/env python3
"""Experimental JP F8 in-process reset attempt.

This is ONE integrated owner-driven generation transition. It composes the
proven Stage9-18 generated runtime overlays, does not touch pinned submodules
or ROMs, and rejects a new generation unless the previous one passed ALL
existing joined/queue/save/timer checks. Native app remains in recomp::start()
with its SDL_Window reused. There is no execv or process replacement.

The first Ubuntu run is mandatory: the source patch alone is NOT proof that
all RT64/VI/SDL/guest static state can be reinitialized. Default OFF.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def once(src: str, old: str, new: str, label: str) -> str:
    n = src.count(old)
    if n != 1:
        raise ValueError(f"Experimental reset {label}: expected one anchor, found {n}")
    return src.replace(old, new, 1)


EXTERNAL_QUEUE_FENCE = r"""// A stale external OSMesgQueue pointer is an RDRAM use-after-free after
// generation replacement. Close ALL enqueue callers, drain after joins,
// then reopen with a new generation before any new game boot.
static goemon_jp::reset::PublisherGate jp_external_message_gate{1};

bool ultramodern::jp_flush_external_messages_after_join(std::uint64_t generation) {
    if (!jp_external_message_gate.close_for_pc_exit(
            std::chrono::milliseconds{500})) {
        return false;
    }
    QueuedMessage old;
    std::uint64_t removed = 0;
    while (external_messages.try_dequeue(old)) {
        ++removed;
    }
    // Keep producer admission CLOSED until the old RDRAM is actually
    // unmapped and all other generation-local subsystems have rearmed.
    if (external_messages.size_approx() != 0) {
        return false;
    }
    std::fprintf(stderr,
        "[Goemon JP integrated reset] external_queue_drained=1 removed=%llu "
        "generation=%llu; no stale OSMesgQueue pointers.\n",
        static_cast<unsigned long long>(removed),
        static_cast<unsigned long long>(generation));
    return true;
}

// Separate publish rearm occurs LAST, on the owner thread, AFTER the
// verified old RDRAM unmap and ALL other subsystem rearm checks.
bool ultramodern::jp_rearm_external_messages_after_verified_exit(
        std::uint64_t old_generation) {
    if (external_messages.size_approx() != 0) {
        return false;
    }
    return jp_external_message_gate.rearm_after_verified_exit(
        old_generation, true);
}

"""


MESSAGE_CONTINUATION_SCOPE = r"""using goemon_jp::savestate::MessageCall;
using goemon_jp::savestate::MessageContinuation;

// Publish at a runtime boundary that can be resumed from a saved record.
class MessageContinuationScope {
    uint8_t* rdram;

public:
    MessageContinuationScope(uint8_t* rdram, MessageContinuation state) : rdram(rdram) {
        ultramodern::jp_set_message_continuation(rdram, state);
    }

    MessageContinuationScope(const MessageContinuationScope&) = delete;
    MessageContinuationScope& operator=(const MessageContinuationScope&) = delete;

    ~MessageContinuationScope() {
        ultramodern::jp_set_message_continuation(rdram, std::nullopt);
    }
};

static s32 finish_message_call(RDRAM_ARG bool success) {
    const s32 result = success ? 0 : -1;
    const MessageContinuationScope continuation(rdram, {MessageCall::Return, 0, result});
    ultramodern::check_running_queue(PASS_RDRAM1);
    return result;
}

"""

IDLE_MESSAGE_LOOP = r"""
[[noreturn]] void ultramodern::jp_idle_message_loop(uint8_t* rdram) {
    for (;;) {
        ultramodern::guest_shutdown_checkpoint();
        QueuedMessage message;
        {
            std::unique_lock lock(idle_wakeup_mutex);
            if (!external_messages.try_dequeue(message)) {
                const MessageContinuationScope continuation(rdram, {MessageCall::IdleWait, 0, 0});
                idle_wakeup.wait_for(lock, std::chrono::milliseconds{10});
                continue;
            }
        }
        if (!do_send(rdram, message.mq, message.mesg, message.jam, false) && message.requeue_if_blocked) {
            external_messages.enqueue(message);
        }
        ultramodern::check_running_queue(rdram);
    }
}
"""


RESUME_MESSAGE_CALL = r"""
bool ultramodern::jp_resume_message_call(
        uint8_t* rdram, const MessageContinuation& state, int32_t& result) {
    const auto self = ultramodern::this_thread();
    if (self == NULLPTR || TO_PTR(OSThread, self)->queue != NULLPTR ||
            ultramodern::guest_shutdown_requested_for_future_reset()) {
        return false;
    }
    switch (state.call) {
        case MessageCall::IdleWait:
            if (state.queue != 0 || state.argument != 0) {
                return false;
            }
            jp_idle_message_loop(rdram);
        case MessageCall::Receive:
            result = finish_message_call(rdram, do_recv(rdram, state.queue, state.argument, true));
            return true;
        case MessageCall::Send:
        case MessageCall::Jam:
            result = finish_message_call(rdram,
                do_send(rdram, state.queue, state.argument, state.call == MessageCall::Jam, true));
            return true;
        case MessageCall::Return:
            if (state.argument != 0 && state.argument != -1) {
                return false;
            }
            result = state.argument;
            return true;
    }
    return false;
}
"""


def messages(src: str) -> str:
    if "jp_external_message_gate" in src:
        raise ValueError("External message producer already gated")
    src = once(src,
        '#include "ultramodern/ultramodern.hpp"\n',
        '#include "ultramodern/ultramodern.hpp"\n'
        '#include "jp_generation_publisher_gate.hpp"\n'
        '#include "jp_message_continuation.hpp"\n'
        '#include <condition_variable>\n'
        '#include <mutex>\n'
        'namespace ultramodern { bool guest_shutdown_requested_for_future_reset(); }\n'
        '#include <cstdio>\n',
        "external message gate include")
    src = once(src,
        "static moodycamel::BlockingConcurrentQueue<QueuedMessage> external_messages {};\n",
        "static moodycamel::BlockingConcurrentQueue<QueuedMessage> external_messages {};\n"
        "static std::mutex idle_wakeup_mutex;\n"
        "static std::condition_variable idle_wakeup;\n"
        "namespace ultramodern { bool jp_flush_external_messages_after_join(std::uint64_t); }\n"
        "namespace ultramodern { bool jp_rearm_external_messages_after_verified_exit(std::uint64_t); }\n"
        + EXTERNAL_QUEUE_FENCE,
        "external queue close, drain and rearm")
    src = once(src,
        "void ultramodern::enqueue_external_message(PTR(OSMesgQueue) mq, OSMesg msg, bool jam, bool requeue_if_blocked) {\n",
        "void ultramodern::enqueue_external_message(PTR(OSMesgQueue) mq, OSMesg msg, bool jam, bool requeue_if_blocked) {\n"
        "    auto jp_lease = jp_external_message_gate.try_acquire();\n"
        "    if (!jp_lease) return;\n",
        "count actual external message enqueue producer")
    src = once(src, "bool do_send(RDRAM_ARG PTR(OSMesgQueue) mq_, OSMesg msg, bool jam, bool block);\n",
        MESSAGE_CONTINUATION_SCOPE +
        "bool do_send(RDRAM_ARG PTR(OSMesgQueue) mq_, OSMesg msg, bool jam, bool block);\n",
        "insert message continuation scope before the runtime handlers")
    src = once(src,
        "            JpQueueWaitScope blocked(jp_active_senders);\n",
        "            JpQueueWaitScope blocked(jp_active_senders);\n"
        "            const MessageContinuationScope continuation(rdram,\n"
        "                {jam ? MessageCall::Jam : MessageCall::Send, mq_, msg});\n",
        "record a blocked send or jam before switching threads")
    src = once(src,
        "            JpQueueWaitScope blocked(jp_active_receivers);\n",
        "            JpQueueWaitScope blocked(jp_active_receivers);\n"
        "            const MessageContinuationScope continuation(rdram,\n"
        "                {MessageCall::Receive, mq_, msg_});\n",
        "record a blocked receive before switching threads")
    for result_name, count in (("sent", 2), ("received", 1)):
        ending = (
            "    // Check the queue to see if this thread should swap execution to another.\n"
            "    ultramodern::check_running_queue(PASS_RDRAM1);\n\n"
            f"    return {result_name} ? 0 : -1;\n"
        )
        if src.count(ending) != count:
            raise ValueError(f"Unexpected return sites for {result_name} message operations")
        src = src.replace(ending, f"    return finish_message_call(PASS_RDRAM {result_name});\n")
    src = once(src,
        "    external_messages.enqueue({mq, msg, jam, requeue_if_blocked});\n",
        "    {\n"
        "        std::lock_guard lock(idle_wakeup_mutex);\n"
        "        if (!external_messages.enqueue({mq, msg, jam, requeue_if_blocked})) return;\n"
        "    }\n"
        "    idle_wakeup.notify_one();\n",
        "wake IDLE for a published external message without removing it")
    src += IDLE_MESSAGE_LOOP + RESUME_MESSAGE_CALL
    return "// Experimental reset external message queue generation fence.\n" + src


SCHEDULER_RESET = r"""// A native pointer to an old OSThread can outlive joined guests.
// Allocating zeroed RDRAM does NOT reset this process-static queue head.
// The reset owner invokes this only after all old guest/native joins.
namespace ultramodern {
bool jp_clear_native_running_queue_after_verified_exit(bool joined);
}

bool ultramodern::jp_clear_native_running_queue_after_verified_exit(
        bool joined) {
    if (!joined) {
        return false;
    }
    const bool stale = running_queue_impl != NULLPTR;
    running_queue_impl = NULLPTR;
    std::fprintf(stderr,
        "[Goemon JP integrated reset] native_running_queue_cleared=1 "
        "stale_head=%u; old-generation scheduler references released.\n",
        static_cast<unsigned>(stale));
    return true;
}

"""


def scheduler(src: str) -> str:
    if "jp_clear_native_running_queue_after_verified_exit" in src:
        raise ValueError("Native scheduler queue already instrumented")
    src = once(src,
        "#include <cassert>\n",
        "#include <cassert>\n#include <cstdio>\n",
        "scheduler reset diagnostic include")
    src = once(src,
        "static PTR(OSThread) running_queue_impl = NULLPTR;\n",
        "static PTR(OSThread) running_queue_impl = NULLPTR;\n" +
        SCHEDULER_RESET,
        "clear persistent scheduler head after all guest/native joins")
    return "// Experimental integrated reset persistent native scheduler queue.\n" + src


def overlays(src: str) -> str:
    if "clear_region_patches" in src:
        raise ValueError("Regional overlay reset already installed")
    src = once(src,
        "static std::unordered_map<std::string, recomp_func_t*> base_exports{};\n",
        "static std::unordered_map<std::string, recomp_func_t*> base_exports{};\n"
        "static std::unordered_map<std::string, recomp_func_t*> patch_exports{};\n",
        "keep native exports separate from edition-specific patches")
    src = once(src,
        "        base_exports.emplace(cur_export->name, it->second);\n",
        "        patch_exports.emplace(cur_export->name, it->second);\n",
        "register only patch exports in the edition's table")
    src = once(src,
        "    auto it = base_exports.find(export_name);\n"
        "    if (it == base_exports.end()) {\n"
        "        return nullptr;\n"
        "    }\n"
        "    return it->second;\n",
        "    if (const auto it = base_exports.find(export_name); it != base_exports.end()) {\n"
        "        return it->second;\n"
        "    }\n"
        "    const auto patch = patch_exports.find(export_name);\n"
        "    return patch == patch_exports.end() ? nullptr : patch->second;\n",
        "look up process exports and then regional patch exports")
    src = once(src,
        "void recomp::overlays::register_overlays(",
        """// The unified launcher calls this only before publishing the new
// game ID, after any previous generation's teardown has completed.
namespace recomp::overlays { void clear_region_patches(); }
void recomp::overlays::clear_region_patches() {
    patch_code_sections = nullptr;
    num_patch_code_sections = 0;
    patch_data.clear();
    patch_code_sections_by_rom.clear();
    patch_exports.clear();
    base_events.clear();
    manual_patch_symbols_by_vram.clear();
}

void recomp::overlays::register_overlays(""",
        "clear the old region before installing the selected patch set")
    src = once(src,
        "void recomp::overlays::init_overlays() {\n"
        "    func_map.clear();\n",
        "void recomp::overlays::init_overlays() {\n"
        "    func_map.clear();\n"
        "    loaded_sections.clear();\n"
        "    code_sections_by_rom.clear();\n"
        "    std::free(section_addresses);\n",
        "discard dynamic overlays and address storage from the prior generation")
    return "// Experimental integrated reset overlay and patch lifecycle.\n" + src


GUEST_THREAD_BINDINGS = r"""// Keep native thread handles outside of the guest's OSThread record.
struct GuestThreadBinding {
    UltraThreadContext* context;
    goemon_jp::savestate::GuestThreadStart initial;
    bool entered = false;
    std::optional<goemon_jp::savestate::MessageContinuation> message;
    const goemon_jp_guest_checkpoint* message_cpu = nullptr;
};

static std::unordered_map<const OSThread*, GuestThreadBinding> jp_guest_thread_bindings;
static thread_local UltraThreadContext* jp_current_host_context = nullptr;

static bool is_bound_guest_thread(const OSThread* thread, const UltraThreadContext* context) {
    std::lock_guard lock(jp_guest_context_mutex);
    const auto binding = jp_guest_thread_bindings.find(thread);
    return binding != jp_guest_thread_bindings.end() && binding->second.context == context;
}

static bool mark_guest_thread_entered(const OSThread* thread, const UltraThreadContext* context) {
    std::lock_guard lock(jp_guest_context_mutex);
    const auto binding = jp_guest_thread_bindings.find(thread);
    if (binding == jp_guest_thread_bindings.end() || binding->second.context != context) {
        return false;
    }
    binding->second.entered = true;
    return true;
}

goemon_jp::savestate::GuestCheckpointCoverage ultramodern::jp_guest_checkpoint_coverage() {
    std::lock_guard lock(jp_guest_context_mutex);
    goemon_jp::savestate::GuestCheckpointCoverage coverage{};
    for (const auto& [address, binding] : jp_guest_thread_bindings) {
        ++coverage.threads;
        coverage.not_started += !binding.entered;
        coverage.message_checkpoints += binding.message_cpu != nullptr && binding.message.has_value();
    }
    return coverage;
}

std::optional<goemon_jp::savestate::GuestThreadStart> ultramodern::jp_get_unstarted_guest_thread(int32_t address) {
    std::lock_guard lock(jp_guest_context_mutex);
    for (const auto& [key, binding] : jp_guest_thread_bindings) {
        if (binding.initial.address == address && !binding.entered) {
            return binding.initial;
        }
    }
    return std::nullopt;
}

static bool unbind_guest_thread(const OSThread* thread, const UltraThreadContext* context) {
    std::lock_guard lock(jp_guest_context_mutex);
    const auto binding = jp_guest_thread_bindings.find(thread);
    if (binding == jp_guest_thread_bindings.end() || binding->second.context != context) {
        return false;
    }
    jp_guest_thread_bindings.erase(binding);
    return true;
}

static UltraThreadContext* current_guest_context(const OSThread* thread) {
    std::lock_guard lock(jp_guest_context_mutex);
    return jp_guest_thread_bindings.at(thread).context;
}

void ultramodern::jp_set_message_continuation(
        uint8_t* rdram, std::optional<goemon_jp::savestate::MessageContinuation> message) {
    if (thread_self == NULLPTR) {
        return;
    }
    std::lock_guard lock(jp_guest_context_mutex);
    const auto binding = jp_guest_thread_bindings.find(TO_PTR(OSThread, thread_self));
    // A retired thread may unwind after a new thread takes its guest address.
    if (binding != jp_guest_thread_bindings.end() && binding->second.context == jp_current_host_context) {
        binding->second.message = message;
    }
}

std::optional<goemon_jp::savestate::MessageContinuation> ultramodern::jp_get_message_continuation(
        uint8_t* rdram, int32_t guest_thread) {
    if (guest_thread == NULLPTR) {
        return std::nullopt;
    }
    std::lock_guard lock(jp_guest_context_mutex);
    const auto binding = jp_guest_thread_bindings.find(TO_PTR(OSThread, guest_thread));
    return binding == jp_guest_thread_bindings.end() ? std::nullopt : binding->second.message;
}

void ultramodern::jp_set_message_cpu_checkpoint(uint8_t* rdram, const goemon_jp_guest_checkpoint* cpu) {
    if (thread_self == NULLPTR) {
        return;
    }
    std::lock_guard lock(jp_guest_context_mutex);
    const auto binding = jp_guest_thread_bindings.find(TO_PTR(OSThread, thread_self));
    if (binding != jp_guest_thread_bindings.end() && binding->second.context == jp_current_host_context) {
        binding->second.message_cpu = cpu;
    }
}

std::optional<goemon_jp_guest_checkpoint> ultramodern::jp_get_message_checkpoint(
        uint8_t* rdram, int32_t guest_thread) {
    if (guest_thread == NULLPTR) {
        return std::nullopt;
    }
    std::lock_guard lock(jp_guest_context_mutex);
    const auto it = jp_guest_thread_bindings.find(TO_PTR(OSThread, guest_thread));
    if (it == jp_guest_thread_bindings.end() || !it->second.message || it->second.message_cpu == nullptr) {
        return std::nullopt;
    }
    goemon_jp_guest_checkpoint checkpoint = *it->second.message_cpu;
    const auto& message = *it->second.message;
    checkpoint.message = {static_cast<uint8_t>(message.call), message.queue, message.argument};
    return checkpoint;
}

"""


GUEST_REARM = r"""// After normal shutdown and a complete worker join, the reset owner may
// reopen OSThread creation. Never clear the stop flag while any old guest
// context remains live or its cleaner has not joined.
bool ultramodern::jp_rearm_guest_after_verified_exit() {
    std::lock_guard<std::mutex> lock(jp_guest_context_mutex);
    if (!jp_guest_stop_requested.load(std::memory_order_acquire) ||
        jp_guest_creations_in_flight.load(std::memory_order_acquire) != 0 ||
        !jp_guest_contexts.empty() || !jp_guest_thread_bindings.empty() ||
        jp_guest_threads_created.load(std::memory_order_acquire) !=
            jp_guest_threads_joined.load(std::memory_order_acquire)) {
        return false;
    }
    jp_guest_stop_requested.store(false, std::memory_order_release);
    return true;
}

"""


def threads(src: str) -> str:
    if "jp_rearm_guest_after_verified_exit" in src:
        raise ValueError("Guest rearm already installed")
    src = once(src,
        '#include "ultramodern/ultramodern.hpp"\n',
        '#include "ultramodern/ultramodern.hpp"\n'
        '#include "jp_message_continuation.hpp"\n'
        '#include "jp_guest_checkpoint_status.hpp"\n'
        'namespace ultramodern { bool jp_rearm_guest_after_verified_exit(); }\n',
        "guest rearm declaration before out-of-namespace definition")
    src = once(src,
        "#include <unordered_set>\n",
        "#include <unordered_set>\n#include <unordered_map>\n",
        "guest thread binding include")
    registry_anchor = "static std::unordered_set<UltraThreadContext*> jp_guest_contexts;\n"
    src = once(src, registry_anchor, registry_anchor + "\n" + GUEST_THREAD_BINDINGS,
        "guest thread binding registry")
    src = once(src,
        "TO_PTR(OSThread, ultramodern::this_thread())->context != thread_context",
        "!is_bound_guest_thread(TO_PTR(OSThread, ultramodern::this_thread()), thread_context)",
        "check binding after scheduler wake")
    src = once(src,
        "    t->context->running.signal();\n",
        "    std::lock_guard lock(jp_guest_context_mutex);\n"
        "    jp_guest_thread_bindings.at(t).context->running.signal();\n",
        "signal a bound host thread")
    src = once(src,
        '    debug_printf("[Scheduling] Resuming execution of thread %d\\n", to_run->id);\n'
        "    to_run->context->running.signal();\n",
        "    resume_thread(to_run);\n",
        "signal the next scheduled thread")
    for schedule in ("run_next_thread(PASS_RDRAM1);", "resume_thread(t);"):
        src = once(src,
            "    UltraThreadContext* cur_context = TO_PTR(OSThread, thread_self)->context;\n"
            f"    {schedule}\n",
            "    UltraThreadContext* cur_context = current_guest_context(TO_PTR(OSThread, thread_self));\n"
            f"    {schedule}\n",
            "look up the running thread before yielding")
    src = once(src,
        "        self->context == thread_context) {\n",
        "        mark_guest_thread_entered(self, thread_context)) {\n",
        "check binding before guest entry")
    src = once(src,
        "    if (self->context == thread_context) {\n"
        "        self->context = nullptr;\n",
        "    if (unbind_guest_thread(self, thread_context)) {\n",
        "remove only the exiting host thread's binding")
    src = once(src,
        "        jp_guest_contexts.insert(context);\n"
        "    }\n"
        "    t->context = context;\n"
        "    context->host_thread = std::thread{_thread_func, PASS_RDRAM t_, entrypoint, arg, t->context};\n",
        "        jp_guest_contexts.insert(context);\n"
        "        jp_guest_thread_bindings.insert_or_assign(t, GuestThreadBinding{\n"
        "            context, {t_, entrypoint, t->sp, arg}, false, std::nullopt, nullptr});\n"
        "    }\n"
        "    // Preserve the pinned runtime's OSThread layout in guest RAM.\n"
        "    t->context = nullptr;\n"
        "    context->host_thread = std::thread{_thread_func, PASS_RDRAM t_, entrypoint, arg, context};\n",
        "bind new host threads outside guest RAM")
    src = once(src,
        "    UltraThreadContext* cur_context = t->context;\n"
        "    if (cur_context != nullptr) {\n"
        "        // Mark the target thread as destroyed and resume it. When it starts it'll check this and terminate itself instead of resuming.\n"
        "        t->context = nullptr;\n"
        "        cur_context->running.signal();\n"
        "    }\n",
        "    std::lock_guard lock(jp_guest_context_mutex);\n"
        "    const auto binding = jp_guest_thread_bindings.find(t);\n"
        "    if (binding != jp_guest_thread_bindings.end()) {\n"
        "        UltraThreadContext* context = binding->second.context;\n"
        "        jp_guest_thread_bindings.erase(binding);\n"
        "        context->running.signal();\n"
        "    }\n",
        "unbind and wake a destroyed thread")
    src = once(src,
        "    thread_self = self_;\n",
        "    thread_self = self_;\n"
        "    jp_current_host_context = thread_context;\n",
        "identify the native thread owning a continuation scope")
    src = once(src,
        "void ultramodern::request_guest_shutdown_for_future_reset() {\n",
        GUEST_REARM + "void ultramodern::request_guest_shutdown_for_future_reset() {\n",
        "guest join-based rearm owner")
    return "// Experimental integrated reset OSThread rearm.\n" + src


TIMER_REARM = r"""// Called only after the old guest, timer worker, publishers, and other
// native workers have fully joined. The clock and publication gate move to
// the same next generation or the whole restart fails closed.
bool ultramodern::jp_rearm_timer_after_verified_exit(std::uint64_t old_generation) {
    if (!ultramodern::jp_timer_exit_joined_stage17()) {
        return false;
    }
    const goemon_jp::reset::TimerEpochClock::StopProof proofs{
        true, true, true, true
    };
    if (!jp_timer_epoch_clock.rebase_after_verified_exit(old_generation, proofs)) {
        return false;
    }
    if (!jp_timer_publisher_gate.rearm_after_verified_exit(old_generation, true)) {
        return false;
    }
    jp_timer_exit_gate_drained.store(false, std::memory_order_release);
    jp_timer_exit_worker_joined.store(false, std::memory_order_release);
    return true;
}

"""


def timer(src: str) -> str:
    if "jp_rearm_timer_after_verified_exit" in src:
        raise ValueError("Timer rearm already installed")
    src = once(src,
        "    void jp_report_timer_epoch_stage18();\n",
        "    void jp_report_timer_epoch_stage18();\n"
        "    bool jp_rearm_timer_after_verified_exit(std::uint64_t);\n",
        "timer restart declaration")
    src = once(src,
        "void ultramodern::jp_report_timer_epoch_stage18() {\n",
        TIMER_REARM + "void ultramodern::jp_report_timer_epoch_stage18() {\n",
        "timer rearm implementation")
    return "// Experimental integrated reset timer clock, queue and gate rearm.\n" + src


EVENT_REARM = r"""// Only invoked after ALL guest/native event threads have joined and the
// Stage10 action queue is empty. No RT64 worker can still read these values.
bool ultramodern::jp_rearm_events_after_verified_exit(std::uint64_t old_generation) {
    if (!ultramodern::jp_event_workers_joined_for_future_reset() ||
        !ultramodern::jp_graphics_queue_settled_for_future_reset() ||
        events_context.action_queue.size_approx() != 0 ||
        events_context.sp_task_queue.size_approx() != 0) {
        return false;
    }
    {
        std::lock_guard<std::mutex> lock(events_context.message_mutex);
        events_context.vi.cur_state = 0;
        events_context.vi.field = 0;
        events_context.vi.states[0] = ViState{};
        events_context.vi.states[1] = ViState{};
        events_context.vi.regs = {};
        events_context.vi.update_screen_regs = {};
        events_context.sp.mq = NULLPTR;
        events_context.dp.mq = NULLPTR;
        events_context.ai.mq = NULLPTR;
        events_context.si.mq = NULLPTR;
    }
    total_vis = 0;
    if (!jp_config_publisher_gate.rearm_after_verified_exit(
            old_generation, true)) {
        return false;
    }
    {
        std::lock_guard<std::mutex> lock(jp_config_action_publish_mutex);
        jp_config_actions_closed_for_pc_exit = false;
    }
    return true;
}

"""


def events(src: str) -> str:
    if "jp_rearm_events_after_verified_exit" in src:
        raise ValueError("Events rearm already installed")
    src = once(src,
        "    void jp_report_config_action_producer_gate();\n",
        "    void jp_report_config_action_producer_gate();\n"
        "    bool jp_rearm_events_after_verified_exit(std::uint64_t);\n",
        "events rearm declaration")
    # On the new generation, VI may start before the game loads osViSetMode.
    # Prime its next-mode pointer with the existing valid dummy mode first.
    src = once(src,
        "    events_context.rdram = rdram;\n",
        "    events_context.rdram = rdram;\n"
        "    set_dummy_vi(false);\n",
        "seed VI mode before starting new native VI worker")
    src = once(src,
        "void ultramodern::jp_report_config_action_producer_gate() {\n",
        EVENT_REARM + "void ultramodern::jp_report_config_action_producer_gate() {\n",
        "events state/queue/gate rearm")
    return "// Experimental integrated reset VI, message targets and action queue rearm.\n" + src


SAVE_REARM = r"""// A reset never discards dirty save bytes. Stage16 must have finalized
// every previous mutation and joined the worker before this is called.
bool ultramodern::jp_rearm_saving_after_verified_exit() {
    if (!ultramodern::jp_save_worker_joined_for_future_reset()) {
        return false;
    }
    // The stopped worker may have left notifications after the final
    // synchronous Stage16 flush. They must not leak into a fresh save.
    while (save_context.write_sempahore.tryWait()) {}
    while (save_context.swap_file_pending_sempahore.tryWait()) {}
    while (save_context.swap_file_ready_sempahore.tryWait()) {}
    jp_save_mutation_epoch.store(0, std::memory_order_release);
    jp_save_finalized_epoch.store(0, std::memory_order_release);
    jp_save_exit_finalized.store(false, std::memory_order_release);
    return true;
}

"""


def saving(src: str) -> str:
    if "jp_rearm_saving_after_verified_exit" in src:
        raise ValueError("Saving rearm already installed")
    src = once(src,
        '#include <ultramodern/ultramodern.hpp>\n',
        '#include <ultramodern/ultramodern.hpp>\n'
        'namespace ultramodern { bool jp_rearm_saving_after_verified_exit(); }\n',
        "save rearm forward declaration")
    src = once(src,
        "void ultramodern::init_saving(RDRAM_ARG1) {\n",
        SAVE_REARM + "void ultramodern::init_saving(RDRAM_ARG1) {\n",
        "safe save epoch and notification rearm")
    return "// Experimental integrated reset finalized saving worker rearm.\n" + src


RESET_OWNER = r"""// Experimental reset ownership: F8 requests are handled by the existing
// normal-exit join path, then a new guest generation is created inside the
// SAME recomp::start() invocation (without a new PID or SDL_Window).
namespace recomp { bool jp_request_integrated_reset(); }
namespace recomp { bool jp_request_return_to_launcher(); }
static std::atomic_bool jp_integrated_restart_requested{false};
static std::optional<std::u8string> jp_integrated_restart_game_id;
static std::atomic_uint64_t jp_integrated_generation{1};
static bool jp_integrated_autostart_next = false;

static bool jp_request_generation_restart(bool start_game_after_restart) {
    {
        std::lock_guard<std::mutex> lock(current_game_mutex);
        if (jp_integrated_restart_requested.load(std::memory_order_acquire) ||
            game_status.load(std::memory_order_acquire) != GameStatus::Running ||
            !current_game.has_value()) {
            return false;
        }
        jp_integrated_restart_game_id = start_game_after_restart ? current_game : std::nullopt;
        jp_integrated_restart_requested.store(true, std::memory_order_release);
    }
    std::fprintf(stderr,
        "[Goemon JP integrated reset] request=1 generation=%llu "
        "stopping=1 pid=unchanged; entering verified teardown.\n",
        static_cast<unsigned long long>(
            jp_integrated_generation.load(std::memory_order_acquire)));
    // End only the current guest-runner loop. Do NOT call
    // ultramodern::quit(), which clears current_game before guest threads
    // have joined; their entry dispatch may still read that game id.
    // The normal-exit join machinery uses exited but the process and SDL
    // window are retained by our outer generation loop.
    exited.store(true, std::memory_order_release);
    game_status.notify_all();
    return true;
}

bool recomp::jp_request_integrated_reset() {
    return jp_request_generation_restart(true);
}

bool recomp::jp_request_return_to_launcher() {
    return jp_request_generation_restart(false);
}

"""


def recomp(src: str) -> str:
    if "jp_request_integrated_reset" in src:
        raise ValueError("Integrated reset already installed in recomp source")
    if "[Goemon JP exit barrier stage12]" not in src or \
       "jp_timer_exit_joined_stage17()" not in src:
        raise ValueError("Reset requires generated Stage12 + Stage17 complete exit proof")
    src = once(src,
        "namespace ultramodern { bool jp_timer_exit_joined_stage17(); }\n",
        "namespace ultramodern { bool jp_timer_exit_joined_stage17(); }\n"
        "namespace ultramodern {\n"
        "    bool jp_rearm_guest_after_verified_exit();\n"
        "    bool jp_rearm_timer_after_verified_exit(std::uint64_t);\n"
        "    bool jp_rearm_events_after_verified_exit(std::uint64_t);\n"
        "    bool jp_rearm_saving_after_verified_exit();\n"
        "    bool jp_clear_native_running_queue_after_verified_exit(bool);\n"
        "    bool jp_flush_external_messages_after_join(std::uint64_t);\n"
        "    bool jp_rearm_external_messages_after_verified_exit(std::uint64_t);\n"
        "}\n"
        "namespace recomp { bool jp_reset_audio_buffer_after_join(); }\n",
        "integrated ownership declarations")
    # The launcher can register an imported ROM's hash before starting the
    # first guest. Mod registration is independently idempotent by game ID.
    src = once(src,
        "        game_roms.insert({ entry.game_id, entry });\n",
        "        game_roms.insert_or_assign(entry.game_id, entry);\n",
        "refresh the selected game entry before gameplay")
    src = once(src,
        "void ultramodern::quit() {\n",
        RESET_OWNER + "void ultramodern::quit() {\n",
        "integrated F8 request ownership")
    # N64ModernRuntime::load_stored_rom permanently flips the registered
    # GameEntry.has_compressed_code flag to false after its first load.
    # Unlike the initial boot, a generation-2 load would then feed the raw
    # compressed stored ROM straight to overlays/guest memory. Restore the
    # flag before each USA or Japanese ROM load, while preserving its transition
    # back to false before mod loading in the original function.
    src = once(src,
        '    std::vector<uint8_t> stored_rom_data = read_file(config_path / find_it->second.stored_filename());\n',
        '    std::vector<uint8_t> stored_rom_data = read_file(config_path / find_it->second.stored_filename());\n'
        '    if ((game_id == u8"mnsg.jp" || game_id == u8"mnsg.us") &&\n'
        '        find_it->second.decompression_routine != nullptr) {\n'
        '        if (!find_it->second.has_compressed_code) {\n'
        '            std::fprintf(stderr,\n'
        '                "[Goemon JP integrated reset] restoring compressed game ROM before entrypoint replay.\\n");\n'
        '        }\n'
        '        find_it->second.has_compressed_code = true;\n'
        '    }\n',
        "restore regional decompression across repeated guest generations")
    src = once(src,
        "    // Allocate rdram without comitting it. Use a platform-specific virtual allocation function\n",
        "    // One app process and window; each loop body owns one fresh RDRAM.\n"
        "    for (;;) {\n"
        "    // Allocate rdram without comitting it. Use a platform-specific virtual allocation function\n",
        "generation-scoped allocation loop")
    src = once(src,
        "    }, window_handle, rdram};\n",
        "    }, window_handle, rdram};\n"
        "    if (jp_integrated_autostart_next) {\n"
        "        jp_integrated_autostart_next = false;\n"
        "        recomp::start_game(*jp_integrated_restart_game_id);\n"
        "    }\n",
        "replay registered JP game selection after new preinit starts")
    src = once(src,
        "    if (jp_guest_exit_drained &&\n"
        "        ultramodern::jp_timer_exit_joined_stage17()) {\n"
        "    // Free rdram.\n",
        "    const bool jp_native_scheduler_queue_cleared =\n"
        "        !jp_integrated_restart_requested.load(std::memory_order_acquire) ||\n"
        "        ultramodern::jp_clear_native_running_queue_after_verified_exit(\n"
        "            jp_exit_known_joined && jp_guest_exit_drained);\n"
        "    const bool jp_external_queue_drained =\n"
        "        !jp_integrated_restart_requested.load(std::memory_order_acquire) ||\n"
        "        (jp_exit_known_joined &&\n"
        "         ultramodern::jp_flush_external_messages_after_join(\n"
        "             jp_integrated_generation.load(std::memory_order_acquire)));\n"
        "    bool jp_integrated_rdram_released = false;\n"
        "    if (jp_guest_exit_drained &&\n"
        "        ultramodern::jp_timer_exit_joined_stage17() &&\n"
        "        (!jp_integrated_restart_requested.load(std::memory_order_acquire) ||\n"
        "         jp_exit_known_joined) && jp_native_scheduler_queue_cleared &&\n"
        "        jp_external_queue_drained) {\n"
        "    if (jp_integrated_restart_requested.load(std::memory_order_acquire)) {\n"
        "        // Unpatch modded functions while the old region's metadata is still valid.\n"
        "        std::lock_guard lock(mod_context_mutex);\n"
        "        if (mod_context != nullptr) mod_context->unload_mods();\n"
        "    }\n"
        "    // Free rdram.\n",
        "never release RDRAM on failed integrated shutdown barrier")

    src = once(src,
        '    if (free_failed) {\n'
        '        printf("Failed to free rdram\\n");\n'
        '    }\n'
        '    } else {\n',
        '    if (free_failed) {\n'
        '        printf("Failed to free rdram\\n");\n'
        '    } else {\n'
        '        jp_integrated_rdram_released = true;\n'
        '    }\n'
        '    } else {\n',
        "record successful verified RDRAM unmap for restart")

    # The original pinned file ends with recomp::start(), and Stage17 only
    # prefixes instrumentation. Insert BEFORE its final closing brace.
    if not src.endswith("}\n"):
        raise ValueError("Generated recomp.cpp no longer ends in recomp::start()")
    tail = r"""
    // This is the only point where the same-process restart may proceed:
    // game, guests, VI/GFX/SP, cleaner, timer, save and action producers
    // have ALREADY joined; RDRAM was actually unmapped successfully.
    if (jp_integrated_restart_requested.load(std::memory_order_acquire)) {
        const auto old_generation =
            jp_integrated_generation.load(std::memory_order_acquire);
        const bool joined = jp_exit_known_joined && jp_guest_exit_drained &&
            ultramodern::jp_timer_exit_joined_stage17() &&
            jp_integrated_rdram_released;
        if (!joined) {
            std::fprintf(stderr,
                "[Goemon JP integrated reset] generation=%llu result=ABORT "
                "reason=unproven-shutdown-or-memory-release; no memory reuse.\n",
                static_cast<unsigned long long>(old_generation));
            break; // Normal process exit; never re-enter half-joined state.
        }
        // Each rearm is legal only when its own old-generation proof passes.
        // Failure at any point ends the process safely, not an unsafe reset.
        const bool guest_ready =
            ultramodern::jp_rearm_guest_after_verified_exit();
        const bool event_ready = guest_ready &&
            ultramodern::jp_rearm_events_after_verified_exit(old_generation);
        const bool timer_ready = event_ready &&
            ultramodern::jp_rearm_timer_after_verified_exit(old_generation);
        const bool save_ready = timer_ready &&
            ultramodern::jp_rearm_saving_after_verified_exit();
        const bool audio_ready = save_ready &&
            recomp::jp_reset_audio_buffer_after_join();
        const bool external_ready = audio_ready &&
            ultramodern::jp_rearm_external_messages_after_verified_exit(
                old_generation);
        if (!external_ready) {
            std::fprintf(stderr,
                "[Goemon JP integrated reset] generation=%llu result=ABORT "
                "reason=failed-generation-rearm; no new RDRAM allocated.\n",
                static_cast<unsigned long long>(old_generation));
            break;
        }
        {
            std::lock_guard<std::mutex> lock(current_game_mutex);
            current_game.reset();
        }
        // A previously signalled renderer shutdown semaphore has been
        // consumed by the old gfx worker before its real thread join.
        game_status.store(GameStatus::None, std::memory_order_release);
        exited.store(false, std::memory_order_release);
        jp_integrated_autostart_next = jp_integrated_restart_game_id.has_value();
        jp_integrated_generation.fetch_add(1, std::memory_order_acq_rel);
        jp_integrated_restart_requested.store(false, std::memory_order_release);
        std::fprintf(stderr,
            "[Goemon JP integrated reset] generation=%llu result=REARMED "
            "pid=unchanged window=preserved next_boot=%u.\n",
            static_cast<unsigned long long>(old_generation + 1),
            static_cast<unsigned>(jp_integrated_autostart_next));
        continue;
    }
    break;
    } // Per-generation RDRAM allocation and verified teardown loop.
"""
    return "// Experimental integrated same-process reset loop.\n" + src[:-2] + tail + "}\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--generated-dir", type=Path, required=True)
    p.add_argument("--runtime-root", type=Path, required=True)
    a = p.parse_args()
    d = a.generated_dir
    changes = [
        ("threadqueue.cpp", "threadqueue_integrated.cpp", scheduler),
        ("overlays.cpp", "overlays_integrated.cpp", overlays),
        ("threads.cpp", "threads_reset.cpp", threads),
        ("mesgqueue.cpp", "mesgqueue_integrated.cpp", messages),
        ("events.cpp", "events_integrated.cpp", events),
        ("timer_stage18.cpp", "timer_integrated.cpp", timer),
        ("pi_stage16.cpp", "pi_integrated.cpp", saving),
        ("recomp_stage17.cpp", "recomp_integrated.cpp", recomp),
    ]
    for source, target, transform in changes:
        if source == "threadqueue.cpp":
            path = a.runtime_root / "ultramodern/src" / source
        elif source == "overlays.cpp":
            path = a.runtime_root / "librecomp/src" / source
        else:
            path = d / source
        original = path.read_text(encoding="utf-8")
        result = transform(original)
        output = d / target
        temp = output.with_suffix(output.suffix + ".tmp")
        temp.write_text(result, encoding="utf-8")
        temp.replace(output)
        print(f"[JP integrated reset] {source} -> {target}")


if __name__ == "__main__":
    main()
