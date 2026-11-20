#!/usr/bin/env python3
"""Stage10 tests: PC-exit-only classification of abandoned VI/config actions."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PINNED = ROOT / "lib/N64ModernRuntime"
SCRIPT = Path(__file__).with_name("patch_action_drain_stage10.py")
spec = importlib.util.spec_from_file_location("jp_action_stage10", SCRIPT)
assert spec and spec.loader
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)

STAGE7 = r"""// JP stage7 native boot census, generated from pinned recomp.cpp.
#include <cstdio>
#include <atomic>
namespace ultramodern { void shutdown_timers(); }
void exit_app() {
    game_thread.join();
    jp_boot_thread_joined.store(true, std::memory_order_release);
    std::fprintf(stderr, "[Goemon JP boot exit] normal\n");
    // Timer may still reference guest RDRAM; join before unmapping RAM.
    ultramodern::shutdown_timers();
    ultramodern::join_event_threads();
    ultramodern::join_thread_cleaner_thread();
    ultramodern::join_saving_thread();
    // Free rdram.
    bool free_failed;
    free_failed = (munmap(rdram, allocation_size) == -1);
    if (free_failed) {
        printf("Failed to free rdram\n");
    }
}
"""


class ActionExitStage10(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = (PINNED / "ultramodern/src/events.cpp").read_text()
        cls.events = patch.instrument_events(cls.original)
        cls.recomp = patch.instrument_recomp_stage7(STAGE7)

    def test_pinned_event_source_unchanged(self):
        self.assertNotIn("jp_drain_action_queue_after_pc_exit", self.original)
        self.assertNotIn("jp_config_action_publish_mutex", self.original)

    def test_retains_stage8_graphics_counter_instrumentation(self):
        for symbol in ("jp_gfx_tasks_submitted", "jp_gfx_tasks_started",
                       "jp_gfx_tasks_completed", "[Goemon JP gfx queue]"):
            self.assertIn(symbol, self.events)

    def test_action_drain_handles_all_three_variant_types(self):
        for symbol in ("std::holds_alternative<SpTaskAction>(queued)",
                       "std::holds_alternative<ScreenUpdateAction>(queued)",
                       "std::holds_alternative<UpdateConfigAction>(queued)"):
            self.assertIn(symbol, self.events)
        self.assertIn("action_queue.try_dequeue(queued)", self.events)

    def test_only_after_all_known_guests_and_native_workers_join(self):
        source = self.events.split(
            "bool ultramodern::jp_drain_action_queue_after_pc_exit(", 1
        )[1].split("\n}\n", 1)[0]
        for symbol in ("!all_guests_joined", "!jp_event_gfx_joined.load",
                       "!jp_event_vi_joined.load", "!jp_event_sp_joined.load"):
            self.assertIn(symbol, source)
        self.assertLess(source.index("if (!all_guests_joined"),
                        source.index("action_queue.try_dequeue(queued)"))

    def test_failed_guest_or_native_barrier_skips_drain(self):
        self.assertIn("[Goemon JP action exit stage10] skipped=1", self.events)
        self.assertIn("do not drain live-generation work", self.events)
        self.assertIn("return false;", self.events)

    def test_config_actions_closed_under_same_mutex_as_drain(self):
        src = self.events
        self.assertIn("static std::mutex jp_config_action_publish_mutex;", src)
        self.assertEqual(src.count("std::lock_guard<std::mutex> lock(jp_config_action_publish_mutex);"), 2)
        self.assertIn("jp_config_actions_closed_for_pc_exit = true;", src)
        trigger = src.split("void ultramodern::trigger_config_action() {", 1)[1].split("\n}", 1)[0]
        self.assertLess(trigger.index("std::lock_guard"), trigger.index("if (jp_config_actions_closed_for_pc_exit) return;"))
        self.assertLess(trigger.index("if (jp_config_actions_closed_for_pc_exit) return;"),
                        trigger.index("events_context.action_queue.enqueue(UpdateConfigAction{});"))

    def test_no_graphics_renderer_work_executed_during_drain(self):
        body = self.events.split("bool ultramodern::jp_drain_action_queue_after_pc_exit(", 1)[1].split("\n}\n", 1)[0]
        for bad in ("renderer_context->send_dl", "renderer_context->update_screen",
                    "ultramodern::submit_rsp_task", "munmap(", "memset(", "init_events("):
            self.assertNotIn(bad, body)

    def test_expected_only_requires_zero_graphics_and_zero_remaining(self):
        drain = self.events.split("bool ultramodern::jp_drain_action_queue_after_pc_exit(", 1)[1].split("\n}\n", 1)[0]
        for condition in ("unexpected_graphics == 0", "pending == 0",
                          "remainder == 0", "jp_gfx_tasks_submitted.load",
                          "jp_gfx_tasks_completed.load"):
            self.assertIn(condition, drain)
        self.assertIn("no F8 reset", drain)

    def test_shutdown_order_guest_then_native_then_action_then_unmap(self):
        points = [self.recomp.index(x) for x in (
            "game_thread.join();",
            "    ultramodern::request_guest_shutdown_for_future_reset();",
            "ultramodern::wait_for_guest_joins_for_future_reset(900);",
            "    ultramodern::join_event_threads();",
            "ultramodern::jp_drain_action_queue_after_pc_exit(jp_guest_exit_drained)",
            "    ultramodern::join_thread_cleaner_thread();",
            "if (jp_guest_exit_drained) {",
            "munmap(rdram, allocation_size)",
        )]
        self.assertEqual(points, sorted(points))

    def test_config_only_after_native_join_and_guest_join(self):
        self.assertEqual(self.recomp.count("jp_drain_action_queue_after_pc_exit(jp_guest_exit_drained)"), 1)
        self.assertIn("(void)jp_actions_expected_only; // Not a real reset barrier.", self.recomp)
        self.assertIn("RDRAM retained:", self.recomp)

    def test_no_f8_queue_drain(self):
        inp = (ROOT / "src/game/input.cpp").read_text()
        self.assertNotIn("jp_drain_action_queue_after_pc_exit(", inp)
        self.assertNotIn("request_guest_shutdown_for_future_reset();", inp)

    def test_cmake_off_by_default_with_stage9_requirement(self):
        cmake = (ROOT / "CMakeLists.txt").read_text()
        self.assertIn(
            'option(GOEMON_JP_ACTION_STAGE10 "Enable PC-exit-only residual action queue drain" OFF)', cmake
        )
        self.assertIn("GOEMON_JP_ACTION_STAGE10 AND NOT GOEMON_JP_EXIT_STAGE9", cmake)
        self.assertIn("patch_action_drain_stage10.py", cmake)
        self.assertIn("recomp_stage10.cpp", cmake)

    def test_version_jp_0_2_17_usa_unchanged(self):
        main = (ROOT / "src/main/main.cpp").read_text()
        ui = (ROOT / "src/ui/ui_launcher.cpp").read_text()
        self.assertIn('version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn('jp_action_build_mode = " [action-exit-drain]"', main)
        self.assertIn('version_string += " [action-exit-drain]"', ui)

    def test_duplicate_event_generation_rejected(self):
        with self.assertRaisesRegex(ValueError, "already applied"):
            patch.instrument_events(self.events)

    def test_duplicate_recomp_generation_rejected(self):
        with self.assertRaisesRegex(ValueError, "already instrumented"):
            patch.instrument_recomp_stage7(self.recomp)

    def test_idempotent_build_generated_write(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "events.cpp"
            writer = patch.stage8.stage6.stage5.stage4.stage3.write_if_changed
            self.assertTrue(writer(dest, self.events))
            mtime = dest.stat().st_mtime_ns
            self.assertFalse(writer(dest, self.events))
            self.assertEqual(dest.stat().st_mtime_ns, mtime)


if __name__ == "__main__":
    unittest.main()
