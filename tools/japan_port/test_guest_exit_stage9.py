#!/usr/bin/env python3
"""JP stage9 guest stop/join at normal process exit; F8 remains diagnostics."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PINNED = ROOT / "lib/N64ModernRuntime"
path = Path(__file__).with_name("patch_guest_exit_stage9.py")
spec = importlib.util.spec_from_file_location("jp_exit9", path)
assert spec and spec.loader
stage9 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage9)

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


class ExitStage9(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = (PINNED / "ultramodern/src/threads.cpp").read_text()
        cls.threads = stage9.instrument_threads(cls.original)
        cls.recomp = stage9.instrument_recomp_stage7(STAGE7)

    def test_original_submodule_still_original(self):
        self.assertNotIn("jp_cleanup_exit_deadline", self.original)

    def test_stage9_preserves_guest_join_registry(self):
        for name in ("jp_guest_contexts", "jp_guest_stop_requested",
                     "jp_guest_creations_in_flight", "jp_guest_join_cv",
                     "wait_for_guest_joins_for_future_reset"):
            self.assertIn(name, self.threads)

    def test_cleaner_waits_for_join_or_timeout(self):
        cleaner = self.threads.split("void thread_cleaner_func()", 1)[1].split(
            "void ultramodern::init_thread_cleanup()", 1)[0]
        self.assertIn("std::chrono::milliseconds{1500}", cleaner)
        self.assertIn("if ((requested && all_joined) || now >= jp_cleanup_exit_deadline)", cleaner)
        self.assertNotIn("No completed guest thread has arrived for 10 ms.", cleaner)
        self.assertIn("jp_guest_threads_joined.fetch_add", cleaner)

    def test_host_join_precedes_guest_stop_and_native_teardown(self):
        points = [self.recomp.index(s) for s in (
            "game_thread.join();",
            "jp_boot_thread_joined.store(true",
            "    ultramodern::request_guest_shutdown_for_future_reset();",
            "ultramodern::wait_for_guest_joins_for_future_reset(900);",
            "    ultramodern::shutdown_timers();",
            "    ultramodern::join_event_threads();",
        )]
        self.assertEqual(points, sorted(points))

    def test_guest_drain_precedes_conditional_unmap(self):
        self.assertIn("if (jp_guest_exit_drained) {\n    // Free rdram.", self.recomp)
        self.assertLess(self.recomp.index("if (jp_guest_exit_drained) {"),
                        self.recomp.index("munmap(rdram, allocation_size)"))
        self.assertIn("RDRAM retained:", self.recomp)
        self.assertIn("OS process exit will reclaim it", self.recomp)

    def test_normal_exit_only_not_f8(self):
        inp = (ROOT / "src/game/input.cpp").read_text()
        self.assertIn("report_guest_join_barrier_readiness();", inp)
        self.assertNotIn("request_guest_shutdown_for_future_reset();", inp)
        self.assertNotIn("wait_for_guest_joins_for_future_reset(", inp)
        self.assertIn("F8 not armed", self.recomp)

    def test_flag_is_opt_in_and_requires_graphics_stage8(self):
        cmake = (ROOT / "CMakeLists.txt").read_text()
        self.assertIn('option(GOEMON_JP_EXIT_STAGE9 "Enable bounded guest OSThread shutdown only on PC exit" OFF)', cmake)
        self.assertIn("GOEMON_JP_EXIT_STAGE9 AND NOT GOEMON_JP_GRAPHICS_STAGE8", cmake)
        self.assertIn("patch_guest_exit_stage9.py", cmake)
        self.assertIn("--mode guest", cmake)
        self.assertIn("--mode recomp", cmake)
        self.assertIn("recomp_stage9.cpp", cmake)

    def test_version_jp_only(self):
        main = (ROOT / "src/main/main.cpp").read_text()
        ui = (ROOT / "src/ui/ui_launcher.cpp").read_text()
        self.assertIn('version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn('jp_exit_build_mode = " [guest-exit-drain]"', main)
        self.assertIn('version_string += " [guest-exit-drain]"', ui)

    def test_refuses_duplicate_guest_patch(self):
        with self.assertRaisesRegex(ValueError, "already patched"):
            stage9.instrument_threads(self.threads)

    def test_refuses_duplicate_recomp_patch(self):
        with self.assertRaisesRegex(ValueError, "already patched"):
            stage9.instrument_recomp_stage7(self.recomp)

    def test_refuses_other_recomp_stage(self):
        with self.assertRaisesRegex(ValueError, "requires the generated stage7"):
            stage9.instrument_recomp_stage7(STAGE7.replace(
                "// JP stage7 native boot census", "// wrong stage"
            ))

    def test_wait_api_and_stop_request_only_called_once(self):
        self.assertEqual(self.recomp.count("request_guest_shutdown_for_future_reset();"), 2)
        self.assertEqual(self.recomp.count("wait_for_guest_joins_for_future_reset("), 2)

    def test_generated_write_repeating_does_not_update_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "recomp_stage9.cpp"
            write = stage9.stage8.stage6.stage5.stage4.stage3.write_if_changed
            self.assertTrue(write(dest, self.recomp))
            stamp = dest.stat().st_mtime_ns
            self.assertFalse(write(dest, self.recomp))
            self.assertEqual(stamp, dest.stat().st_mtime_ns)


if __name__ == "__main__":
    unittest.main()
