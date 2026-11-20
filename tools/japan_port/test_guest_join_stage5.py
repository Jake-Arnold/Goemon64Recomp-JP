#!/usr/bin/env python3
"""JP v0.2.10 guest join-stage source-contract tests (NO live reset).

These tests only validate generated code structure. They cannot prove that
all guest and host workers quiesce, nor that RDRAM can safely be reset.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "lib/N64ModernRuntime"
SCRIPT = ROOT / "tools/japan_port/patch_guest_join_stage5.py"
spec = importlib.util.spec_from_file_location("jp_guest_stage5", SCRIPT)
assert spec is not None and spec.loader is not None
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


class GuestJoinStage5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw_threads = (RUNTIME / "ultramodern/src/threads.cpp").read_text()
        cls.raw_sched = (RUNTIME / "ultramodern/src/scheduling.cpp").read_text()
        cls.raw_queue = (RUNTIME / "ultramodern/src/mesgqueue.cpp").read_text()
        cls.threads = patch.instrument_threads(cls.raw_threads)
        cls.scheduler = patch.instrument_scheduler(cls.raw_sched)
        cls.queue = patch.stage4.instrument_messages(cls.raw_queue)

    def test_pinned_runtime_is_not_modified(self) -> None:
        self.assertNotIn("jp_guest_join_cv", self.raw_threads)
        self.assertNotIn("jp_guest_stop_requested", self.raw_sched)
        self.assertNotIn("jp_send_waits", self.raw_queue)

    def test_join_api_integer_type_is_declared_after_header(self) -> None:
        # This declaration precedes the original N64ModernRuntime includes.
        # A prior build failed because uint32_t was not declared here.
        generated = self.threads
        early_header = generated.index("#include <cstdint>")
        declaration = generated.index(
            "bool wait_for_guest_joins_for_future_reset(std::uint32_t timeout_ms);"
        )
        definition = generated.index(
            "bool ultramodern::wait_for_guest_joins_for_future_reset(std::uint32_t timeout_ms)"
        )
        self.assertLess(early_header, declaration)
        self.assertLess(declaration, definition)
        self.assertEqual(
            generated[:declaration].count("#include <cstdint>"), 1,
        )

    def test_join_predicate_requires_stop_request(self) -> None:
        predicate = self.threads.split(
            "bool ultramodern::wait_for_guest_joins_for_future_reset(", 1
        )[1].split("\n}", 1)[0]
        self.assertIn("jp_guest_stop_requested.load(std::memory_order_acquire)", predicate)
        self.assertIn("jp_guest_creations_in_flight.load(std::memory_order_acquire) == 0", predicate)
        self.assertIn("jp_guest_contexts.empty()", predicate)
        self.assertIn("jp_guest_threads_created.load(std::memory_order_acquire) ==", predicate)
        self.assertIn("jp_guest_threads_finished.load(std::memory_order_acquire)", predicate)
        self.assertIn("jp_guest_threads_joined.load(std::memory_order_acquire)", predicate)
        self.assertIn("jp_guest_join_cv.wait_for(", predicate)
        self.assertIn("std::chrono::milliseconds{timeout_ms}", predicate)

    def test_join_predicate_refuses_guest_self_wait(self) -> None:
        predicate = self.threads.split(
            "bool ultramodern::wait_for_guest_joins_for_future_reset(", 1
        )[1].split("\n}", 1)[0]
        self.assertIn("if (thread_self != NULLPTR ||", predicate)
        self.assertIn("return false;", predicate)

    def test_join_wakes_on_early_creation_rejection(self) -> None:
        create = self.threads.split('extern "C" void osCreateThread(', 1)[1].split(
            'extern "C" void osStopThread(', 1
        )[0]
        rejected = create.index(
            "jp_guest_creations_in_flight.fetch_sub(1, std::memory_order_relaxed);"
        )
        self.assertIn("jp_guest_join_cv.notify_all();", create[rejected:])
        self.assertLess(
            rejected, create.index("jp_guest_join_cv.notify_all();", rejected)
        )

    def test_join_wakes_on_successful_creation(self) -> None:
        create = self.threads.split('extern "C" void osCreateThread(', 1)[1].split(
            'extern "C" void osStopThread(', 1
        )[0]
        completed = create.index(
            "jp_guest_creations_in_flight.fetch_sub(1, std::memory_order_release);"
        )
        self.assertLess(completed, create.index(
            "jp_guest_join_cv.notify_all();", completed
        ))
        self.assertLess(create.index("context->initialized.wait();"), completed)

    def test_host_join_precedes_notification_and_cleanup(self) -> None:
        cleaner = self.threads.split("void thread_cleaner_func()", 1)[1].split(
            "void ultramodern::init_thread_cleanup()", 1
        )[0]
        positions = [
            cleaner.index("to_delete->host_thread.join();"),
            cleaner.index("jp_guest_contexts.erase(to_delete);"),
            cleaner.index("jp_guest_threads_joined.fetch_add(1, std::memory_order_release);"),
            cleaner.index("jp_guest_join_cv.notify_all();"),
            cleaner.index("delete to_delete;"),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_scheduler_rejects_work_after_future_stop(self) -> None:
        sched = self.scheduler
        self.assertIn(
            "if (ultramodern::guest_shutdown_requested_for_future_reset()) return;",
            sched,
        )
        self.assertEqual(sched.count(
            "if (ultramodern::guest_shutdown_requested_for_future_reset()) return;"
        ), 2)
        self.assertIn("void ultramodern::schedule_running_thread(", sched)
        self.assertIn("void ultramodern::check_running_queue(", sched)
        self.assertIn("ultramodern::thread_queue_insert(", sched)

    def test_new_guest_create_gate_precedes_guest_memory_access(self) -> None:
        method = self.threads.split('extern "C" void osCreateThread(', 1)[1].split(
            'extern "C" void osStopThread(', 1
        )[0]
        self.assertLess(
            method.index("guest_shutdown_requested_for_future_reset()"),
            method.index("OSThread *t = TO_PTR(OSThread, t_);"),
        )
        self.assertIn("throw ultramodern::thread_terminated{};", method)

    def test_new_guest_start_gate_precedes_guest_state(self) -> None:
        method = self.threads.split('extern "C" void osStartThread(', 1)[1].split(
            'extern "C" void osCreateThread(', 1
        )[0]
        self.assertLess(
            method.index("guest_shutdown_requested_for_future_reset()"),
            method.index("OSThread* t = TO_PTR(OSThread, t_);"),
        )

    def test_prior_guest_and_queue_audits_retained(self) -> None:
        for name in (
            "void ultramodern::report_guest_thread_reset_blockers()",
            "void ultramodern::report_guest_shutdown_readiness()",
            "jp_guest_contexts.insert(context)",
            "jp_guest_threads_finished.fetch_add(",
            "queue drained, guest shutdown NOT proven.",
        ):
            self.assertIn(name, self.threads)
        self.assertIn("void ultramodern::report_guest_message_queue_waits()", self.queue)
        self.assertIn("std::chrono::milliseconds{10}", self.queue)

    def test_f8_reports_join_readiness_without_arming_stop(self) -> None:
        src = (ROOT / "src/game/input.cpp").read_text()
        self.assertIn("ultramodern::report_guest_join_barrier_readiness();", src)
        self.assertNotIn("request_guest_shutdown_for_future_reset();", src)
        self.assertNotIn("wait_for_guest_joins_for_future_reset(", src)
        handler = src.split("// Input plumbing only:", 1)[1].split(
            "\nvoid recomp::set_rumble(", 1
        )[0]
        for forbidden in ("ultramodern::quit(", "execv(", "munmap(", "memset("):
            self.assertNotIn(forbidden, handler)

    def test_compilation_is_opt_in_and_requires_stage4(self) -> None:
        cmake = (ROOT / "CMakeLists.txt").read_text()
        self.assertIn('option(GOEMON_JP_JOIN_STAGE5 "Enable inactive guarded guest join barrier and scheduler stop gates" OFF)', cmake)
        self.assertIn("GOEMON_JP_JOIN_STAGE5 AND NOT GOEMON_JP_QUEUE_STAGE4", cmake)
        self.assertIn("patch_guest_join_stage5.py", cmake)
        self.assertIn('JP_TIMER_GENERATED_DIR}/scheduling.cpp', cmake)
        self.assertIn('JP_TIMER_RUNTIME_ROOT}/ultramodern/src/scheduling.cpp', cmake)
        self.assertIn("GOEMON_JP_JOIN_STAGE5_ACTIVE", cmake)

    def test_version_markers_are_isolated_from_usa(self) -> None:
        main = (ROOT / "src/main/main.cpp").read_text()
        self.assertIn('const std::string version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn('jp_join_build_mode = " [join-prep]"', main)
        ui = (ROOT / "src/ui/ui_launcher.cpp").read_text()
        self.assertIn('version_string += " [join-prep]"', ui)

    def test_patcher_fail_closed_and_idempotent_writes(self) -> None:
        with self.assertRaises(ValueError):
            patch.instrument_threads(self.threads)
        with self.assertRaisesRegex(ValueError, "already applied"):
            patch.instrument_scheduler(self.scheduler)
        # Fail closed even if a copied generated file lost its header.
        partial = self.scheduler.replace(
            "// JP stage-5 scheduler stop gate; original submodule unchanged.\n",
            "", 1,
        )
        with self.assertRaisesRegex(ValueError, "already applied"):
            patch.instrument_scheduler(partial)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "threads.cpp"
            self.assertTrue(patch.stage4.stage3.write_if_changed(path, self.threads))
            mtime = path.stat().st_mtime_ns
            self.assertFalse(patch.stage4.stage3.write_if_changed(path, self.threads))
            self.assertEqual(mtime, path.stat().st_mtime_ns)


if __name__ == "__main__":
    unittest.main()
