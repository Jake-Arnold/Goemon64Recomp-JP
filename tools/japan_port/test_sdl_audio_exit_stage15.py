#!/usr/bin/env python3
"""Stage15 ROM-free contract tests: real SDL audio callbacks and app-exit teardown.

The concrete integration is reviewed in the standalone Japanese frontend
source. No ROM, SDL window or proprietary game data are used by these tests.
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
MAIN = (ROOT / "src/main/main.cpp").read_text(encoding="utf-8")
UI = (ROOT / "src/ui/ui_launcher.cpp").read_text(encoding="utf-8")
CMAKE = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
INPUT = (ROOT / "src/game/input.cpp").read_text(encoding="utf-8")


def body(start: str, end: str) -> str:
    assert MAIN.count(start) == 1, f"Missing/ambiguous start anchor: {start}"
    return MAIN.split(start, 1)[1].split(end, 1)[0]


class SdlAudioExitStage15Tests(unittest.TestCase):
    def test_japanese_only_opt_in_default_off(self):
        self.assertIn('option(GOEMON_JP_SDL_AUDIO_STAGE15 "Enable SDL audio callback fence and device close on PC exit" OFF)', CMAKE)
        self.assertIn("GOEMON_JP_SDL_AUDIO_STAGE15 AND NOT GOEMON_JP_CONFIG_FENCE_STAGE14", CMAKE)
        self.assertIn("GOEMON_JP_SDL_AUDIO_STAGE15_ACTIVE", CMAKE)

    def test_header_and_gate_are_jp_only(self):
        self.assertIn('#ifdef GOEMON_JP_SDL_AUDIO_STAGE15_ACTIVE\n#include <chrono>\n#include "../game/jp_generation_publisher_gate.hpp"', MAIN)
        self.assertIn("static goemon_jp::reset::PublisherGate jp_sdl_audio_gate{1};", MAIN)

    def test_sample_queue_holds_lease_across_sdl_queue(self):
        s = body("void queue_samples(int16_t* audio_data, size_t sample_count) {",
                 "\nsize_t get_frames_remaining() {")
        self.assertIn("#ifdef GOEMON_JP_SDL_AUDIO_STAGE15_ACTIVE", s)
        self.assertIn("if (!jp_audio_lease) return;", s)
        self.assertLess(s.index("jp_sdl_audio_gate.try_acquire()"),
                        s.index("SDL_ConvertAudio(&audio_convert)"))
        self.assertLess(s.index("jp_sdl_audio_gate.try_acquire()"),
                        s.index("SDL_QueueAudio(audio_device,"))
        self.assertNotIn("jp_sdl_audio_gate.close_for_pc_exit(", s)

    def test_remaining_frames_leased_and_deny_returns_zero(self):
        s = body("size_t get_frames_remaining() {",
                 "\nvoid update_audio_converter() {")
        self.assertLess(s.index("jp_sdl_audio_gate.try_acquire()"),
                        s.index("SDL_GetQueuedAudioSize(audio_device)"))
        self.assertIn("if (!jp_audio_lease) return 0;", s)

    def test_frequency_change_leased(self):
        s = body("void set_frequency(uint32_t freq) {",
                 "\nvoid reset_audio(uint32_t output_freq) {")
        self.assertIn("jp_sdl_audio_gate.try_acquire()", s)
        self.assertIn("if (!jp_audio_lease) return;", s)
        self.assertLess(s.index("jp_sdl_audio_gate.try_acquire()"),
                        s.index("update_audio_converter();"))

    def test_exit_fence_after_recomp_start(self):
        s = body("    recomp::start(\n", "\n    NFD_Quit();")
        self.assertIn("jp_sdl_audio_gate.close_for_pc_exit(std::chrono::milliseconds{1000})", s)
        self.assertLess(s.index("threads_callbacks\n    );"),
                        s.index("jp_sdl_audio_gate.close_for_pc_exit("))

    def test_close_after_lease_drain_only(self):
        s = body("const bool jp_audio_leases_drained =", "\n    NFD_Quit();")
        self.assertIn("if (jp_audio_leases_drained && audio_device != 0)", s)
        self.assertLess(s.index("SDL_PauseAudioDevice(audio_device, 1)"),
                        s.index("SDL_ClearQueuedAudio(audio_device)"))
        self.assertLess(s.index("SDL_ClearQueuedAudio(audio_device)"),
                        s.index("SDL_CloseAudioDevice(audio_device)"))
        self.assertIn("audio_device = 0;", s)

    def test_reports_both_false_and_true_exit_status(self):
        s = body("const bool jp_audio_leases_drained =", "\n    NFD_Quit();")
        self.assertIn("jp_audio_leases_drained && jp_audio_device_closed", s)
        self.assertIn("jp_audio_after == 0", s)
        self.assertIn("[Goemon JP SDL audio exit stage15]", s)
        self.assertIn("no live reset or generation rearm.", s)

    def test_f8_does_not_close_sdl(self):
        s = INPUT.split("[Goemon JP reset] Request received;", 1)[1].split(
            "reset_was_held = reset_held;", 1)[0]
        for forbidden in ("SDL_CloseAudioDevice", "SDL_ClearQueuedAudio",
                          "jp_sdl_audio_gate.close_for_pc_exit(",
                          "request_guest_shutdown_for_future_reset();",
                          "memset(", "munmap("):
            self.assertNotIn(forbidden, s)

    def test_normal_usa_build_unchanged(self):
        self.assertIn('version_string = GOEMON_LAUNCHER_VERSION', MAIN)
        self.assertIn('const std::string version_string = GOEMON_LAUNCHER_VERSION', MAIN)
        self.assertIn('jp_sdl_audio_build_mode = " [sdl-audio-exit]"', MAIN)
        self.assertIn('version_string += " [sdl-audio-exit]"', UI)

    def test_exit_printf_specifiers_match(self):
        p = re.search(r'printf\("Goemon 64: Recompiled JP ([^\n]+)\n\s*version_string.c_str\(\),([^;]+);', MAIN)
        self.assertIsNotNone(p)
        self.assertEqual(p.group(1).count("%s"), len(re.findall(r'jp_\w+_build_mode', p.group(2))) + 3)

    def test_reuses_proven_stage14_gate(self):
        gate = (ROOT / "src/game/jp_generation_publisher_gate.hpp").read_text()
        self.assertIn("close_for_pc_exit(", gate)
        self.assertIn("in_flight_ == 0", gate)
        self.assertIn("open_ = false;", gate)
        self.assertIn("~Permit() { release(); }", gate)
        self.assertNotIn("reopen(", gate)


if __name__ == "__main__":
    unittest.main()
