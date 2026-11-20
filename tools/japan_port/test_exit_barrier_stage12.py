#!/usr/bin/env python3
"""ROM-free Stage12 contract tests for post-join, exit-only reset audits."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).with_name("patch_exit_barrier_stage12.py")
spec = importlib.util.spec_from_file_location("jp_reset_stage12", SCRIPT)
assert spec and spec.loader
stage12 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage12)

# Stage7 skeleton is sufficient: generator composes stage9 and stage10,
# then inserts Stage12 after the saving worker joins.
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


class JapanExitBarrierStage12(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated = stage12.instrument_recomp_stage7(STAGE7)
        cls.ordered = cls.generated.split("void exit_app() {", 1)[1]
        cls.report = cls.ordered.split(
            '    // Stage12: PC-exit-only census', 1)[1].split(
            '    // Never unmap while a stale guest worker', 1)[0]

    def test_stage9_and_stage10_preserved(self):
        self.assertIn("[Goemon JP guest exit stage9]", self.generated)
        self.assertIn("jp_drain_action_queue_after_pc_exit(jp_guest_exit_drained)", self.generated)
        self.assertIn("jp_actions_expected_only", self.report)

    def test_reports_after_guest_native_and_action_drain(self):
        points = [self.ordered.index(x) for x in (
            "game_thread.join();",
            "request_guest_shutdown_for_future_reset();",
            "wait_for_guest_joins_for_future_reset(900);",
            "ultramodern::join_event_threads();",
            "jp_drain_action_queue_after_pc_exit(jp_guest_exit_drained)",
            "ultramodern::join_thread_cleaner_thread();",
            "ultramodern::join_saving_thread();",
            "[Goemon JP exit barrier stage12]",
            "munmap(rdram, allocation_size)",
        )]
        self.assertEqual(points, sorted(points))

    def test_existing_host_predicates_checked(self):
        for pred in ("recomp::jp_native_boot_joined_for_future_reset()",
                     "ultramodern::jp_event_workers_joined_for_future_reset()",
                     "ultramodern::jp_graphics_queue_settled_for_future_reset()",
                     "ultramodern::jp_save_worker_joined_for_future_reset()",
                     "ultramodern::jp_audio_dispatch_idle_for_future_reset()"):
            self.assertIn(pred, self.report)

    def test_all_known_conditions_required_for_known_joined(self):
        for value in ("jp_guest_exit_drained && jp_exit_boot_joined",
                      "jp_exit_native_joined", "jp_exit_graphics_settled",
                      "jp_actions_expected_only", "jp_exit_save_joined",
                      "jp_exit_audio_dispatch_idle"):
            self.assertIn(value, self.report)

    def test_explicit_unproven_components_and_no_reset(self):
        for text in ("timer_epoch=unproven", "sdl_audio=unproven",
                     "producer_fences=unproven", "restart=0",
                     "PC exit only, F8 disabled."):
            self.assertIn(text, self.report)

    def test_no_new_mutation_in_report(self):
        for forbidden in ("munmap(", "memset(", "request_guest_shutdown(",
                          "shutdown_timers(", "join(", "action_queue.try_dequeue",
                          "action_queue.enqueue(", "game_entry.entrypoint("):
            self.assertNotIn(forbidden, self.report)

    def test_no_f8_called_into_stage12(self):
        inp = (ROOT / "src/game/input.cpp").read_text()
        self.assertNotIn("jp_exit_known_joined", inp)
        self.assertNotIn("jp_report_exit_barrier", inp)

    def test_build_flag_is_opt_in_and_stacked(self):
        c = (ROOT / "CMakeLists.txt").read_text()
        self.assertIn('option(GOEMON_JP_BARRIER_STAGE12 "Enable post-join cross-subsystem exit barrier census (not F8)" OFF)', c)
        self.assertIn("GOEMON_JP_BARRIER_STAGE12 AND NOT GOEMON_JP_LIVE_STAGE11", c)
        self.assertIn("patch_exit_barrier_stage12.py", c)
        self.assertIn("recomp_stage12.cpp", c)
        self.assertIn("GOEMON_JP_BARRIER_STAGE12_ACTIVE", c)

    def test_version_and_usa_version(self):
        main = (ROOT / "src/main/main.cpp").read_text()
        ui = (ROOT / "src/ui/ui_launcher.cpp").read_text()
        self.assertIn('version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn('jp_barrier_build_mode = " [exit-barrier-audit]"', main)
        self.assertIn('version_string += " [exit-barrier-audit]"', ui)

    def test_duplicate_generation_rejected(self):
        with self.assertRaisesRegex(ValueError, "already instrumented"):
            stage12.instrument_recomp_stage7(self.generated)


if __name__ == "__main__":
    unittest.main()
