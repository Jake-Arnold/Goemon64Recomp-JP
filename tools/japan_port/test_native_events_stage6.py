#!/usr/bin/env python3
"""JP v0.2.11 native event-worker reset preflight source-contract tests.

These tests do not exercise stop/teardown, do not reset RAM, and do not
prove same-PID boot safety. Actual Ubuntu gameplay validation is required.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/japan_port/patch_native_events_stage6.py"
RUNTIME = ROOT / "lib/N64ModernRuntime"
spec = importlib.util.spec_from_file_location("jp_stage6", SCRIPT)
assert spec is not None and spec.loader is not None
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


class NativeEventStage6Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.original = (RUNTIME / "ultramodern/src/events.cpp").read_text()
        cls.generated = patch.instrument_events(cls.original)

    def test_pinned_events_submodule_unmodified(self) -> None:
        self.assertNotIn("jp_event_workers_initialized", self.original)
        self.assertIn("void ultramodern::join_event_threads()", self.original)
        self.assertIn("events_context.sp_task_queue.enqueue(nullptr);", self.original)

    def test_event_worker_flags_are_atomic(self) -> None:
        src = self.generated
        for name in (
            "jp_event_workers_initialized",
            "jp_event_vi_active", "jp_event_gfx_active", "jp_event_sp_active",
            "jp_event_vi_joined", "jp_event_gfx_joined", "jp_event_sp_joined",
        ):
            self.assertIn(f"static std::atomic_bool {name}", src)

    def test_worker_scope_tracks_early_returns_and_normal_exits(self) -> None:
        src = self.generated
        self.assertIn("struct JpEventWorkerScope", src)
        self.assertIn("~JpEventWorkerScope()", src)
        for method, marker in (
            ("void vi_thread_func()", "jp_event_vi_active"),
            ("void task_thread_func(", "jp_event_sp_active"),
            ("void gfx_thread_func(", "jp_event_gfx_active"),
        ):
            part = src.split(method, 1)[1]
            self.assertIn(f"JpEventWorkerScope jp_worker_scope({marker});", part[:250])
        self.assertIn("active.store(true, std::memory_order_release);", src)
        self.assertIn("active.store(false, std::memory_order_release);", src)

    def test_pending_counters_are_atomic_and_includes_inflight(self) -> None:
        src = self.generated
        self.assertIn("static std::atomic<uint64_t> jp_event_gfx_tasks_pending{0};", src)
        self.assertIn("static std::atomic<uint64_t> jp_event_sp_tasks_pending{0};", src)
        self.assertIn("Pending includes tasks being processed.", src)

    def test_pending_gfx_increment_before_queue_publish(self) -> None:
        submit = self.generated.split(
            "void ultramodern::submit_rsp_task(", 1
        )[1].split("void ultramodern::send_si_message()", 1)[0]
        self.assertLess(
            submit.index("jp_event_gfx_tasks_pending.fetch_add(1"),
            submit.index("events_context.action_queue.enqueue(SpTaskAction{ *task });"),
        )
        self.assertLess(
            submit.index("jp_event_sp_tasks_pending.fetch_add(1"),
            submit.index("events_context.sp_task_queue.enqueue(task);"),
        )

    def test_gfx_completion_after_displaylist_callbacks(self) -> None:
        render = self.generated.split("void gfx_thread_func(", 1)[1].split(
            "#define VI_CTRL_TYPE_16", 1
        )[0]
        self.assertLess(
            render.index("ultramodern::extensions::on_displaylist_completed(displaylist);"),
            render.index("jp_event_gfx_tasks_pending.fetch_sub(1"),
        )
        self.assertIn("renderer_context->send_dl(&task_action->task);", render)

    def test_rsp_task_completion_after_sp_notification(self) -> None:
        task = self.generated.split("void task_thread_func(", 1)[1].split(
            "std::atomic_uint32_t display_refresh_rate", 1
        )[0]
        self.assertLess(
            task.index("sp_complete();"),
            task.index("jp_event_sp_tasks_pending.fetch_sub(1"),
        )
        self.assertIn("if (task == nullptr)", task)

    def test_event_init_marks_ready_after_thread_launch(self) -> None:
        init = self.generated.split("void ultramodern::init_events(", 1)[1].split(
            "void ultramodern::join_event_threads()", 1
        )[0]
        self.assertLess(
            init.index("events_context.vi.thread = std::thread{ vi_thread_func };"),
            init.index("jp_event_workers_initialized.store(true"),
        )
        self.assertIn("jp_event_gfx_joined.store(false", init)
        self.assertIn("jp_event_vi_joined.store(false", init)
        self.assertIn("jp_event_sp_joined.store(false", init)

    def test_native_event_join_order_is_unchanged(self) -> None:
        joined = self.generated.split(
            "void ultramodern::join_event_threads()", 1
        )[1]
        keys = (
            "events_context.sp.gfx_thread.join();",
            "events_context.vi.thread.join();",
            "events_context.sp_task_queue.enqueue(nullptr);",
            "events_context.sp.task_thread.join();",
        )
        positions = [joined.index(key) for key in keys]
        self.assertEqual(positions, sorted(positions))

    def test_join_flags_change_only_after_each_join(self) -> None:
        joined = self.generated.split(
            "void ultramodern::join_event_threads()", 1
        )[1]
        for worker, flag in (
            ("sp.gfx_thread", "gfx"),
            ("vi.thread", "vi"),
            ("sp.task_thread", "sp"),
        ):
            self.assertLess(
                joined.index(f"events_context.{worker}.join();"),
                joined.index(f"jp_event_{flag}_joined.store(true"),
            )

    def test_event_only_predicate_requires_all_joined_and_zero_tasks(self) -> None:
        predicate = self.generated.split(
            "bool ultramodern::jp_event_workers_joined_for_future_reset()", 1
        )[1].split("\n}", 1)[0]
        self.assertIn("jp_event_workers_initialized.load", predicate)
        for name in ("vi", "gfx", "sp"):
            self.assertIn(f"jp_event_{name}_joined.load", predicate)
            self.assertIn(f"!jp_event_{name}_active.load", predicate)
        for task in ("gfx", "sp"):
            self.assertIn(f"jp_event_{task}_tasks_pending.load", predicate)
        self.assertIn("== 0", predicate)

    def test_f8_is_diagnostic_only(self) -> None:
        src = (ROOT / "src/game/input.cpp").read_text()
        self.assertIn("ultramodern::jp_report_native_event_worker_blockers();", src)
        self.assertNotIn("jp_event_workers_joined_for_future_reset();", src)
        handler = src.split("// Input plumbing only:", 1)[1].split(
            "\nvoid recomp::set_rumble(", 1
        )[0]
        for forbidden in (
            "request_guest_shutdown_for_future_reset();",
            "ultramodern::quit(", "execv(", "munmap(", "memset(",
        ):
            self.assertNotIn(forbidden, handler)

    def test_native_shutdown_logging_does_not_claim_full_reset(self) -> None:
        src = self.generated
        self.assertIn("[Goemon JP native workers]", src)
        self.assertIn("[Goemon JP event exit]", src)
        self.assertIn("event-only audit, RDRAM reset unavailable.", src)
        self.assertIn("event-only shutdown.", src)

    def test_cmake_is_opt_in_and_generated_source_only(self) -> None:
        cmake = (ROOT / "CMakeLists.txt").read_text()
        self.assertIn(
            'option(GOEMON_JP_EVENTS_STAGE6 "Enable JP native event worker lifecycle and task census" OFF)',
            cmake,
        )
        self.assertIn("GOEMON_JP_EVENTS_STAGE6 AND NOT GOEMON_JP_JOIN_STAGE5", cmake)
        self.assertIn("patch_native_events_stage6.py", cmake)
        self.assertIn('JP_TIMER_GENERATED_DIR}/events.cpp', cmake)
        self.assertIn('JP_TIMER_RUNTIME_ROOT}/ultramodern/src/events.cpp', cmake)
        self.assertIn("GOEMON_JP_EVENTS_STAGE6_ACTIVE", cmake)

    def test_version_in_launcher_title_and_cli(self) -> None:
        main = (ROOT / "src/main/main.cpp").read_text()
        ui = (ROOT / "src/ui/ui_launcher.cpp").read_text()
        self.assertIn('const std::string version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn('jp_events_build_mode = " [native-events-audit]"', main)
        self.assertIn('version_string += " [native-events-audit]"', ui)
        # Later stages may append additional CLI build labels after stage 6.
        self.assertIn("jp_join_build_mode, jp_events_build_mode", main)

    def test_double_injection_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "already applied"):
            patch.instrument_events(self.generated)
        without_header = self.generated.split("\n", 1)[1]
        with self.assertRaisesRegex(ValueError, "already applied"):
            patch.instrument_events(without_header)

    def test_idempotent_build_tree_output(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "events.cpp"
            self.assertTrue(patch.stage5.stage4.stage3.write_if_changed(
                path, self.generated,
            ))
            initial = path.stat().st_mtime_ns
            self.assertFalse(patch.stage5.stage4.stage3.write_if_changed(
                path, self.generated,
            ))
            self.assertEqual(initial, path.stat().st_mtime_ns)


if __name__ == "__main__":
    unittest.main()
