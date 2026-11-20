#!/usr/bin/env python3
"""JP v0.2.13 graphics queue census tests. No real reset is enabled."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "lib/N64ModernRuntime"
SCRIPT = ROOT / "tools/japan_port/patch_graphics_queue_stage8.py"
spec = importlib.util.spec_from_file_location("jp_graphics_stage8", SCRIPT)
assert spec is not None and spec.loader is not None
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


class GraphicsQueueStage8Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = (RUNTIME / "ultramodern/src/events.cpp").read_text()
        cls.generated = patch.instrument_events(cls.original)

    def test_pinned_submodule_left_unmodified(self):
        self.assertNotIn("jp_gfx_tasks_submitted", self.original)
        self.assertNotIn("jp_report_graphics_queue_settlement", self.original)
        self.assertIn("while (!exited)", self.original)

    def test_retains_stage6_event_and_task_tracking(self):
        for key in (
            "jp_event_gfx_tasks_pending",
            "jp_event_sp_tasks_pending",
            "jp_event_workers_initialized",
            "jp_event_gfx_joined",
            "[Goemon JP native workers]",
            "[Goemon JP event exit]",
        ):
            self.assertIn(key, self.generated)

    def test_atomic_task_lifecycle_is_reported(self):
        for field in ("submitted", "started", "completed"):
            self.assertIn(f"static std::atomic<uint64_t> jp_gfx_tasks_{field}", self.generated)
            self.assertIn(f"jp_gfx_tasks_{field}.load(std::memory_order_acquire)", self.generated)
        self.assertIn("[Goemon JP gfx queue]", self.generated)

    def test_submission_precedes_visibility_to_consumer(self):
        start = self.generated.split(
            "void ultramodern::submit_rsp_task(", 1
        )[1].split("void ultramodern::send_si_message()", 1)[0]
        self.assertLess(start.index("jp_gfx_tasks_submitted.fetch_add("),
                        start.index("jp_event_gfx_tasks_pending.fetch_add("))
        self.assertLess(start.index("jp_event_gfx_tasks_pending.fetch_add("),
                        start.index("events_context.action_queue.enqueue(SpTaskAction{ *task });"))

    def test_started_only_counts_graphics_tasks(self):
        src = self.generated
        fragment = src.split("void gfx_thread_func(", 1)[1].split(
            "#define VI_CTRL_TYPE_16", 1
        )[0]
        self.assertIn("if (const auto* task_action = std::get_if<SpTaskAction>(&action)) {", fragment)
        self.assertLess(
            fragment.index("if (const auto* task_action = std::get_if<SpTaskAction>(&action)) {"),
            fragment.index("jp_gfx_tasks_started.fetch_add("),
        )
        self.assertLess(
            fragment.index("jp_gfx_tasks_started.fetch_add("),
            fragment.index("renderer_context->send_dl(&task_action->task);"),
        )
        self.assertEqual(fragment.count("jp_gfx_tasks_started.fetch_add("), 1)

    def test_completed_only_after_graphics_processing(self):
        render = self.generated.split("void gfx_thread_func(", 1)[1].split(
            "#define VI_CTRL_TYPE_16", 1
        )[0]
        locations = [
            render.index("renderer_context->send_dl(&task_action->task);"),
            render.index("ultramodern::extensions::on_displaylist_completed(displaylist);"),
            render.index("jp_gfx_tasks_completed.fetch_add("),
            render.index("jp_event_gfx_tasks_pending.fetch_sub("),
        ]
        self.assertEqual(locations, sorted(locations))

    def test_read_only_queue_size_is_accessed_after_context_definition(self):
        src = self.generated
        self.assertLess(src.index("} events_context{};"),
                        src.index("void ultramodern::jp_report_graphics_queue_settlement()"))
        report = src.split("void ultramodern::jp_report_graphics_queue_settlement() {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn("events_context.action_queue.size_approx()", report)
        self.assertNotIn("try_dequeue(", report)
        self.assertNotIn("enqueue(", report)
        self.assertNotIn("clear(", report)

    def test_future_queue_settlement_condition_fails_closed(self):
        gate = self.generated.split(
            "bool ultramodern::jp_graphics_queue_settled_for_future_reset() {", 1
        )[1].split("\n}", 1)[0]
        for key in (
            "jp_event_gfx_joined.load",
            "!jp_event_gfx_active.load",
            "jp_event_gfx_tasks_pending.load",
            "jp_gfx_tasks_submitted.load",
            "jp_gfx_tasks_completed.load",
            "events_context.action_queue.size_approx() == 0",
        ):
            self.assertIn(key, gate)
        self.assertIn("NOT a reset safety barrier", self.generated)

    def test_join_order_remains_and_exit_report_follows(self):
        shutdown = self.generated.split("void ultramodern::join_event_threads()", 1)[1]
        points = [shutdown.index(s) for s in (
            "events_context.sp.gfx_thread.join();",
            "events_context.vi.thread.join();",
            "events_context.sp_task_queue.enqueue(nullptr);",
            "events_context.sp.task_thread.join();",
            "ultramodern::jp_report_graphics_queue_settlement();",
        )]
        self.assertEqual(points, sorted(points))
        self.assertIn("Snapshot only, after native event joins.", shutdown)

    def test_f8_is_a_snapshot_not_a_reset(self):
        src = (ROOT / "src/game/input.cpp").read_text()
        self.assertIn("ultramodern::jp_report_graphics_queue_settlement();", src)
        handler = src.split("// Input plumbing only:", 1)[1].split(
            "\nvoid recomp::set_rumble(", 1
        )[0]
        for forbidden in (
            "request_guest_shutdown_for_future_reset();",
            "jp_graphics_queue_settled_for_future_reset();",
            "ultramodern::quit(", "execv(", "munmap(", "memset(",
        ):
            self.assertNotIn(forbidden, handler)

    def test_build_flag_requires_all_prior_stages(self):
        cmake = (ROOT / "CMakeLists.txt").read_text()
        self.assertIn(
            'option(GOEMON_JP_GRAPHICS_STAGE8 "Enable JP graphics action queue settlement audit" OFF)', cmake
        )
        self.assertIn("GOEMON_JP_GRAPHICS_STAGE8 AND NOT GOEMON_JP_BOOT_STAGE7", cmake)
        self.assertIn("patch_graphics_queue_stage8.py", cmake)
        self.assertIn("GOEMON_JP_GRAPHICS_STAGE8_ACTIVE", cmake)

    def test_version_identifies_active_mode_without_changing_usa(self):
        main = (ROOT / "src/main/main.cpp").read_text()
        ui = (ROOT / "src/ui/ui_launcher.cpp").read_text()
        self.assertIn('version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn('jp_gfx_build_mode = " [graphics-queue-audit]"', main)
        self.assertIn('version_string += " [graphics-queue-audit]"', ui)

    def test_double_generation_rejected_and_write_idempotent(self):
        with self.assertRaisesRegex(ValueError, "already applied"):
            patch.instrument_events(self.generated)
        no_marker = self.generated.split("\n", 1)[1]
        with self.assertRaisesRegex(ValueError, "already applied"):
            patch.instrument_events(no_marker)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "events.cpp"
            self.assertTrue(patch.stage6.stage5.stage4.stage3.write_if_changed(path, self.generated))
            mtime = path.stat().st_mtime_ns
            self.assertFalse(patch.stage6.stage5.stage4.stage3.write_if_changed(path, self.generated))
            self.assertEqual(path.stat().st_mtime_ns, mtime)


if __name__ == "__main__":
    unittest.main()
