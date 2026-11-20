#!/usr/bin/env python3
"""Stage13 ROM-free source contract and host C++ concurrency regression."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "src/game/jp_generation_coordinator.hpp"
CPP_TEST = Path(__file__).with_name("test_generation_coordinator_stage13.cpp")


class JapanGenerationStage13(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.header = HEADER.read_text(encoding="utf-8")
        cls.input = (ROOT / "src/game/input.cpp").read_text(encoding="utf-8")
        cls.cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")

    def test_independent_generation_state(self):
        self.assertIn("namespace goemon_jp::reset", self.header)
        self.assertIn("std::atomic<std::uint64_t> generation_{1}", self.header)
        self.assertIn("std::atomic<Phase> phase_{Phase::Running}", self.header)
        self.assertNotIn("std::atomic_bool exited", self.header)
        self.assertNotIn("munmap(", self.header)
        self.assertNotIn("memset(", self.header)
        self.assertNotIn("request_guest_shutdown_for_future_reset(", self.header)

    def test_atomic_request_tickets_and_fail_closed_result(self):
        self.assertIn("attempts_.fetch_add(1, std::memory_order_relaxed) + 1", self.header)
        self.assertIn("Rejection::BackendNotInstalled", self.header)
        self.assertIn("record_unarmed_request()", self.header)
        self.assertNotIn("install_backend(", self.header)
        self.assertNotIn("begin_stop(", self.header)

    def test_required_future_fences_are_explicit_not_proven(self):
        self.assertIn("kFutureRequiredFenceMask = (1u << 10) - 1u", self.header)
        for name in ("GuestCreation", "GuestJoined", "NativeBootJoined",
                     "ViGfxSpJoined", "GraphicsAndActionsSettled",
                     "TimerEpochRearmable", "AudioDispatchAndSdlSettled",
                     "SaveWorkerDurability", "InputAndConfigPublishers",
                     "SchedulerMemoryAndOverlayReinit"):
            self.assertIn(name, self.header)

    def test_existing_one_shot_f8_guard_preserved(self):
        self.assertIn("!reset_was_held && ultramodern::is_game_started()", self.input)
        self.assertIn("!recomp::game_input_disabled() && !recomp::all_input_disabled()", self.input)
        self.assertIn("reset_was_held = reset_held;", self.input)

    def test_f8_records_but_never_stops_or_resets(self):
        f8 = self.input.split("[Goemon JP reset] Request received;", 1)[1].split(
            "reset_was_held = reset_held;", 1)[0]
        self.assertIn("#ifdef GOEMON_JP_GENERATION_STAGE13_ACTIVE", f8)
        self.assertEqual(f8.count("record_unarmed_request()"), 1)
        self.assertIn("admitted=0 backend=0 verified_fences=0x000", f8)
        for forbidden in ("request_guest_shutdown_for_future_reset();",
                          "ultramodern::jp_drain_action_queue_after_pc_exit(",
                          "shutdown_timers(", "memset(", "munmap(",
                          "game_entry.entrypoint(", "exited.store("):
            self.assertNotIn(forbidden, f8)

    def test_cmake_default_off_and_requires_previous_stage(self):
        self.assertIn('option(GOEMON_JP_GENERATION_STAGE13 "Enable fail-closed Japanese generation reset admission on F8" OFF)', self.cmake)
        self.assertIn("GOEMON_JP_GENERATION_STAGE13 AND NOT GOEMON_JP_BARRIER_STAGE12", self.cmake)
        self.assertIn("GOEMON_JP_GENERATION_STAGE13_ACTIVE", self.cmake)

    def test_version_and_launcher_usa_unchanged(self):
        main = (ROOT / "src/main/main.cpp").read_text(encoding="utf-8")
        ui = (ROOT / "src/ui/ui_launcher.cpp").read_text(encoding="utf-8")
        self.assertIn('version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn('jp_generation_build_mode = " [generation-admission]"', main)
        self.assertIn('version_string += " [generation-admission]"', ui)

    def test_host_concurrency_exercise(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if compiler is None:
            self.skipTest("native C++ compiler not installed")
        with tempfile.TemporaryDirectory() as td:
            binary = Path(td) / "stage13_host_test"
            subprocess.run(
                [compiler, "-std=c++20", "-Wall", "-Wextra", "-Werror",
                 "-O2", "-pthread", str(CPP_TEST), "-o", str(binary)],
                check=True, timeout=45, cwd=ROOT,
            )
            p = subprocess.run([str(binary)], check=True, capture_output=True,
                               text=True, timeout=20, cwd=ROOT)
            self.assertIn("4001 unique rejected requests", p.stdout)
            self.assertIn("generation=1, phase=running", p.stdout)


if __name__ == "__main__":
    unittest.main()
