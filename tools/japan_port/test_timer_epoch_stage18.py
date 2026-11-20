#!/usr/bin/env python3
"""Stage18 pinned-source, no-ROM timer timebase regression tests."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "lib/N64ModernRuntime/ultramodern/src/timer.cpp"
SCRIPT = Path(__file__).with_name("patch_timer_epoch_stage18.py")
spec = importlib.util.spec_from_file_location("jp_stage18", SCRIPT)
assert spec and spec.loader
stage18 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage18)


class Stage18TimerEpochTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = RUNTIME.read_text(encoding="utf-8")
        cls.stage17 = stage18.load_stage1()
        stage17_script = SCRIPT.with_name("patch_timer_producer_fence_stage17.py")
        p = importlib.util.spec_from_file_location("jp_stage17_for18", stage17_script)
        assert p and p.loader
        stage17 = importlib.util.module_from_spec(p)
        p.loader.exec_module(stage17)
        cls.pinned = raw
        cls.before = stage17.instrument_timer(raw)
        cls.after = stage18.instrument_stage17_timer(cls.before)
        cls.cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")

    def test_preserves_original_pinned_timer(self):
        self.assertIn("static int64_t ostime_offset = 0;", self.pinned)
        self.assertNotIn("jp_timer_epoch_clock", self.pinned)

    def test_preserves_stage17_publisher_fence(self):
        for text in ("jp_timer_publisher_gate.try_acquire()",
                     "jp_timer_exit_joined_stage17()",
                     "jp_timer_exit_gate_drained.load(", "ShutdownTimerAction{}"):
            self.assertIn(text, self.after)
        self.assertEqual(self.after.count("jp_timer_publisher_gate.try_acquire()"), 2)

    def test_requires_stage17_generated_input(self):
        with self.assertRaisesRegex(ValueError, "requires the generated Stage17"):
            stage18.instrument_stage17_timer(self.pinned)

    def test_duplicate_instrumentation_fails(self):
        with self.assertRaisesRegex(ValueError, "already instrumented"):
            stage18.instrument_stage17_timer(self.after)

    def test_clock_has_no_old_mutable_globals(self):
        self.assertNotIn("static int64_t ostime_offset = 0", self.after)
        self.assertNotIn("static std::chrono::high_resolution_clock::time_point start_time =", self.after)
        self.assertIn("static goemon_jp::reset::TimerEpochClock jp_timer_epoch_clock{counter_per_ms};", self.after)

    def test_guest_time_read_write_synchronized(self):
        self.assertIn("uint64_t total_count = jp_timer_epoch_clock.guest_time();", self.after)
        self.assertIn("jp_timer_epoch_clock.set_guest_time(t);", self.after)
        self.assertIn("return jp_timer_epoch_clock.ticks_now();", self.after)

    def test_runtime_deadline_and_time_origin(self):
        for token in ("return jp_timer_epoch_clock.start() + ticks_to_duration(ticks);",
                      "return jp_timer_epoch_clock.start();",
                      "return jp_timer_epoch_clock.since_start();"):
            self.assertIn(token, self.after)

    def test_f8_clock_report_cannot_mutate(self):
        r = self.after.split("void ultramodern::jp_report_timer_epoch_stage18() {", 1)[1].split(
            "void ultramodern::jp_report_timer_producer_stage17()", 1)[0]
        self.assertIn("jp_timer_epoch_clock.snapshot()", r)
        self.assertIn("rearm_enabled=0 f8_no_change=1", r)
        for forbidden in ("set_guest_time(", "rebase_for_test(", "close_for_pc_exit(",
                          "shutdown_timers(", "munmap(", "memset("):
            self.assertNotIn(forbidden, r)

    def test_test_rebase_not_available_in_production(self):
        header = (ROOT / "src/game/jp_timer_epoch_clock.hpp").read_text()
        self.assertIn("#ifdef GOEMON_JP_STAGE18_TEST_REBASE", header)
        self.assertIn("rebase_for_test(", header)
        self.assertNotIn("GOEMON_JP_STAGE18_TEST_REBASE", self.cmake)
        self.assertIn("std::lock_guard<std::mutex>", header)
        self.assertIn("guest_time_offset_ = 0;", header)

    def test_input_f8_is_readonly(self):
        inp = (ROOT / "src/game/input.cpp").read_text()
        handler = inp.split("[Goemon JP reset] Request received;", 1)[1].split(
            "reset_was_held = reset_held;", 1)[0]
        self.assertIn("jp_report_timer_epoch_stage18();", handler)
        for forbidden in ("rebase_for_test(", "init_timers(", "shutdown_timers(",
                          "request_guest_shutdown_for_future_reset();"):
            self.assertNotIn(forbidden, handler)

    def test_cmake_default_off_and_after_stage17(self):
        self.assertIn('option(GOEMON_JP_TIMER_EPOCH_STAGE18 "Enable synchronized JP timer clock with test-only epoch rebase" OFF)', self.cmake)
        self.assertIn("GOEMON_JP_TIMER_EPOCH_STAGE18 AND NOT GOEMON_JP_TIMER_PUBLISH_STAGE17", self.cmake)
        self.assertIn("patch_timer_epoch_stage18.py", self.cmake)
        self.assertIn("timer_stage18.cpp", self.cmake)
        self.assertIn("GOEMON_JP_TIMER_EPOCH_STAGE18_ACTIVE", self.cmake)

    def test_jp_version_usa_unchanged(self):
        main = (ROOT / "src/main/main.cpp").read_text()
        ui = (ROOT / "src/ui/ui_launcher.cpp").read_text()
        self.assertIn('version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn('[timer-epoch-clock]', main)
        self.assertIn('[timer-epoch-clock]', ui)

    def test_native_test_compiles_and_runs(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if compiler is None:
            self.skipTest("C++ compiler unavailable")
        source = SCRIPT.with_name("test_timer_epoch_clock_stage18.cpp")
        with tempfile.TemporaryDirectory() as td:
            executable = Path(td) / "stage18_clock"
            subprocess.run(
                [compiler, "-std=c++20", "-O2", "-Wall", "-Wextra",
                 "-Werror", "-pthread", str(source), "-o", str(executable)],
                cwd=ROOT, check=True, timeout=45,
            )
            done = subprocess.run(
                [str(executable)], cwd=ROOT, check=True, timeout=25,
                capture_output=True, text=True,
            )
            self.assertIn("Stage18 timer epoch host test PASS", done.stdout)
            self.assertIn("samples=60000 generation=2", done.stdout)


if __name__ == "__main__":
    unittest.main()
