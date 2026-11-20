#!/usr/bin/env python3
"""Stage11 read-only F8 preflight contract tests. No ROM required."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
PINNED_EVENTS = ROOT / "lib/N64ModernRuntime/ultramodern/src/events.cpp"
SCRIPT = Path(__file__).with_name("patch_live_preflight_stage11.py")
spec = importlib.util.spec_from_file_location("jp_live_stage11", SCRIPT)
assert spec and spec.loader
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


class JapanLiveStage11Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pinned = PINNED_EVENTS.read_text(encoding="utf-8")
        cls.generated = patch.instrument_events(cls.pinned)
        cls.body = cls.generated.split(
            "void ultramodern::jp_report_live_generation_preflight() {", 1
        )[1].split("\n}\n", 1)[0]
        cls.input = (ROOT / "src/game/input.cpp").read_text(encoding="utf-8")
        cls.cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")

    def test_pinned_runtime_not_modified(self):
        self.assertNotIn("jp_report_live_generation_preflight()", self.pinned)

    def test_composes_existing_stage10_after_prior_sources(self):
        self.assertIn("jp_drain_action_queue_after_pc_exit(", self.generated)
        self.assertIn("[Goemon JP action exit stage10]", self.generated)
        self.assertIn("jp_gfx_tasks_submitted", self.generated)
        self.assertEqual(self.generated.count(
            "void ultramodern::jp_report_live_generation_preflight() {"), 1)

    def test_reads_live_native_producers(self):
        for name in ("jp_event_vi_active", "jp_event_gfx_active",
                     "jp_event_sp_active", "jp_event_workers_initialized",
                     "jp_event_gfx_tasks_pending", "jp_event_sp_tasks_pending"):
            self.assertIn(name + ".load(std::memory_order_acquire)", self.body)

    def test_reads_graphics_queue_and_config_producers(self):
        for name in ("jp_gfx_tasks_submitted", "jp_gfx_tasks_completed",
                     "events_context.action_queue.size_approx()",
                     "jp_config_actions_closed_for_pc_exit"):
            self.assertIn(name, self.body)
        self.assertIn("std::lock_guard<std::mutex> lock(jp_config_action_publish_mutex);",
                      self.body)

    def test_reports_known_blocker_mask_without_claiming_barrier(self):
        for bit in ("vi ? 1u", "gfx ? 2u", "sp ? 4u",
                    "gfx_pending ? 8u", "sp_pending ? 16u",
                    "submitted != completed ? 32u",
                    "queued ? 64u", "config_open ? 128u"):
            self.assertIn(bit, self.body)
        self.assertIn("ready=0", self.body)
        self.assertIn("guest/boot/timer/audio/save/input fences unproven", self.body)

    def test_observer_does_not_stop_or_mutate_generation(self):
        for forbidden in ("request_guest_shutdown", "exited.store(",
                          "action_queue.try_dequeue", "action_queue.enqueue",
                          "shutdown_timers(", "join(", "munmap(", "memset(",
                          "SDL_PauseAudio", "game_entry.entrypoint"):
            self.assertNotIn(forbidden, self.body)

    def test_one_shot_f8_only(self):
        self.assertIn("!reset_was_held && ultramodern::is_game_started()", self.input)
        f8 = self.input.split("[Goemon JP reset] Request received;", 1)[1].split(
            "reset_was_held = reset_held;", 1
        )[0]
        self.assertIn("#ifdef GOEMON_JP_LIVE_STAGE11_ACTIVE", f8)
        self.assertEqual(f8.count("ultramodern::jp_report_live_generation_preflight();"), 1)
        self.assertNotIn("ultramodern::jp_drain_action_queue_after_pc_exit(", f8)
        self.assertNotIn("request_guest_shutdown_for_future_reset();", f8)

    def test_cmake_gated_and_off_by_default(self):
        self.assertIn('option(GOEMON_JP_LIVE_STAGE11 "Enable read-only live-generation producer preflight on F8" OFF)', self.cmake)
        self.assertIn("GOEMON_JP_LIVE_STAGE11 AND NOT GOEMON_JP_ACTION_STAGE10", self.cmake)
        self.assertIn('patch_live_preflight_stage11.py', self.cmake)
        self.assertIn("GOEMON_JP_LIVE_STAGE11_ACTIVE", self.cmake)
        self.assertIn("GOEMON_JP_ACTION_STAGE10", self.cmake)

    def test_version_and_launcher_tag(self):
        main = (ROOT / "src/main/main.cpp").read_text(encoding="utf-8")
        ui = (ROOT / "src/ui/ui_launcher.cpp").read_text(encoding="utf-8")
        self.assertIn('version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn('jp_live_build_mode = " [live-preflight]"', main)
        self.assertIn('version_string += " [live-preflight]"', ui)

    def test_stage11_generated_declaration_present(self):
        self.assertIn("void jp_report_live_generation_preflight();", self.generated)
        self.assertEqual(self.generated.count("[Goemon JP live preflight stage11]"), 1)

    def test_double_patching_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "already applied"):
            patch.instrument_events(self.generated)


if __name__ == "__main__":
    unittest.main()
