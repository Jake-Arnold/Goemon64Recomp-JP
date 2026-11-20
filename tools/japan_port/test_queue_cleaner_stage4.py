#!/usr/bin/env python3
"""Source-contract tests for JP v0.2.9 queue and cleaner lifecycle stage.

These tests do not prove that an actual N64 reset is safe. They verify
that the generated runtime is bounded and observable, and F8 never
arms guest shutdown or clears memory in this build.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "lib/N64ModernRuntime"
SCRIPT = ROOT / "tools/japan_port/patch_queue_cleaner_stage4.py"
spec = importlib.util.spec_from_file_location("jp_stage4", SCRIPT)
assert spec and spec.loader
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


class QueueCleanerStage4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.original_threads = (RUNTIME / "ultramodern/src/threads.cpp").read_text()
        cls.original_queue = (RUNTIME / "ultramodern/src/mesgqueue.cpp").read_text()
        cls.threads = patch.instrument_threads(cls.original_threads)
        cls.queue = patch.instrument_messages(cls.original_queue)

    def test_original_runtime_is_unchanged(self) -> None:
        self.assertNotIn("jp_send_waits", self.original_queue)
        self.assertNotIn("jp_active_receivers", self.original_queue)
        self.assertIn("external_messages.wait_dequeue(to_send);", self.original_queue)
        self.assertIn("while (!exited)", self.original_threads)

    def test_queue_waits_are_counted(self) -> None:
        for name in (
            "jp_send_waits", "jp_recv_waits", "jp_active_senders",
            "jp_active_receivers", "jp_external_waits",
            "void ultramodern::report_guest_message_queue_waits()",
        ):
            self.assertIn(name, self.queue)
        self.assertIn('blocked_send=%llu blocked_recv=%llu', self.queue)
        self.assertIn("reset not armed.", self.queue)

    def test_wait_counters_use_raii_and_survive_throw(self) -> None:
        src = self.queue
        self.assertIn("struct JpQueueWaitScope", src)
        self.assertIn("counter.fetch_add(1, std::memory_order_relaxed);", src)
        self.assertIn("counter.fetch_sub(1, std::memory_order_relaxed);", src)
        for label in ("jp_active_senders", "jp_active_receivers"):
            self.assertIn(f"JpQueueWaitScope blocked({label});", src)
        self.assertEqual(src.count("JpQueueWaitScope blocked("), 2)

    def test_external_wait_is_bounded_with_cooperative_checkpoint(self) -> None:
        src = self.queue
        func = src.split("void ultramodern::wait_for_external_message(", 1)[1].split(
            "void ultramodern::wait_for_external_message_timed(", 1
        )[0]
        self.assertNotIn("external_messages.wait_dequeue(to_send);", func)
        self.assertIn("external_messages.wait_dequeue_timed(", func)
        self.assertIn("std::chrono::milliseconds{10}", func)
        self.assertEqual(func.count("ultramodern::guest_shutdown_checkpoint();"), 2)

    def test_timed_external_wait_checks_cancel_on_success_and_timeout(self) -> None:
        src = self.queue
        func = src.split("void ultramodern::wait_for_external_message_timed(", 1)[1].split(
            'extern "C" void osCreateMesgQueue(', 1
        )[0]
        self.assertIn("external_messages.wait_dequeue_timed(", func)
        self.assertEqual(func.count("ultramodern::guest_shutdown_checkpoint();"), 2)
        self.assertLess(
            func.index("ultramodern::guest_shutdown_checkpoint();"),
            func.index("do_send(PASS_RDRAM to_send.mq"),
        )

    def test_cleaner_drains_ready_completions_before_idle_exit(self) -> None:
        src = self.threads
        func = src.split("void thread_cleaner_func()", 1)[1].split(
            "void ultramodern::init_thread_cleanup()", 1
        )[0]
        self.assertIn("while (true)", func)
        self.assertIn("deleted_threads.wait_dequeue_timed(to_delete, 10ms)", func)
        self.assertIn("else if (exited.load(std::memory_order_acquire))", func)
        self.assertIn("to_delete->host_thread.join();", func)
        self.assertLess(func.index("to_delete->host_thread.join();"),
                        func.index("jp_guest_contexts.erase(to_delete);"))
        self.assertIn("guest shutdown NOT proven.", func)
        self.assertIn("jp_guest_threads_created.load", func)
        self.assertIn("jp_guest_threads_finished.load", func)
        self.assertIn("jp_guest_threads_joined.load", func)

    def test_f8_does_not_invoke_stop_or_memory_reset(self) -> None:
        src = (ROOT / "src/game/input.cpp").read_text()
        self.assertIn("ultramodern::report_guest_message_queue_waits();", src)
        self.assertNotIn("request_guest_shutdown_for_future_reset();", src)
        handler = src.split("// Input plumbing only:", 1)[1].split(
            "\nvoid recomp::set_rumble(", 1
        )[0]
        for forbidden in ("ultramodern::quit(", "execv(", "munmap(", "memset("):
            self.assertNotIn(forbidden, handler)

    def test_stage_dependencies_and_exact_source_replacement(self) -> None:
        cmake = (ROOT / "CMakeLists.txt").read_text()
        self.assertIn('option(GOEMON_JP_QUEUE_STAGE4 "Enable JP queue and cleaner lifecycle diagnostics" OFF)', cmake)
        self.assertIn("GOEMON_JP_QUEUE_STAGE4 AND NOT GOEMON_JP_GUEST_STAGE3", cmake)
        self.assertIn("patch_queue_cleaner_stage4.py", cmake)
        self.assertIn('JP_TIMER_GENERATED_DIR}/mesgqueue.cpp', cmake)
        self.assertIn('JP_TIMER_RUNTIME_ROOT}/ultramodern/src/mesgqueue.cpp', cmake)

    def test_stage3_registry_and_counters_retained(self) -> None:
        src = self.threads
        for marker in (
            "std::unordered_set<UltraThreadContext*> jp_guest_contexts;",
            "request_guest_shutdown_for_future_reset()",
            "jp_guest_threads_created.fetch_add(",
            "jp_guest_threads_finished.fetch_add(",
            "jp_guest_threads_joined.fetch_add(",
        ):
            self.assertIn(marker, src)

    def test_japan_version_and_build_modes(self) -> None:
        main = (ROOT / "src/main/main.cpp").read_text()
        self.assertIn('version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn('jp_queue_build_mode = " [queue-audit]"', main)
        self.assertIn('jp_queue_build_mode = " [queue-off]"', main)
        launcher = (ROOT / "src/ui/ui_launcher.cpp").read_text()
        self.assertIn('version_string += " [queue-audit]"', launcher)

    def test_generation_is_fail_closed_and_idempotent(self) -> None:
        with self.assertRaises(ValueError):
            patch.instrument_messages(self.queue)
        with self.assertRaises(ValueError):
            patch.instrument_threads(self.threads)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "mesgqueue.cpp"
            self.assertTrue(patch.stage3.write_if_changed(target, self.queue))
            mtime = target.stat().st_mtime_ns
            self.assertFalse(patch.stage3.write_if_changed(target, self.queue))
            self.assertEqual(target.stat().st_mtime_ns, mtime)


if __name__ == "__main__":
    unittest.main()
