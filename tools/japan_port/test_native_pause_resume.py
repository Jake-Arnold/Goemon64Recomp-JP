#!/usr/bin/env python3
"""ROM-free JP F6 pause/resume test suite, including real generated C++."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).with_name("patch_native_pause_resume.py")
SPEC = importlib.util.spec_from_file_location("jp_native_pause", SCRIPT)
assert SPEC and SPEC.loader
pause = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pause)

SOURCE_FILES = {
    "timer": ("timer_integrated.cpp", "timer_pause.cpp", pause.timer),
    "events": ("events_integrated.cpp", "events_pause.cpp", pause.events),
    "messages": ("mesgqueue_integrated.cpp", "mesgqueue_pause.cpp", pause.messages),
    "threads": ("threads_reset.cpp", "threads_pause.cpp", pause.threads),
    "recomp": ("recomp_integrated.cpp", "recomp_pause.cpp", pause.recomp),
}


class NativePauseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        cls.input = (ROOT / "src/game/input.cpp").read_text(encoding="utf-8")
        cls.front = (ROOT / "src/main/main.cpp").read_text(encoding="utf-8")
        cls.clock = (ROOT / "src/game/jp_timer_epoch_clock.hpp").read_text(encoding="utf-8")
        cls.controller = (ROOT / "src/game/jp_pause_controller.hpp").read_text(encoding="utf-8")
        cls.src = SCRIPT.read_text(encoding="utf-8")

    def test_opt_in_and_dependency_on_integrated_reset(self):
        self.assertIn(
            'option(GOEMON_JP_NATIVE_F6_PAUSE "F6 guest-time/VI/scheduler/timer/audio cooperative pause and resume (experimental)" OFF)',
            self.cmake)
        self.assertIn(
            "GOEMON_JP_NATIVE_F6_PAUSE AND NOT GOEMON_JP_INTEGRATED_F8_RESET_EXPERIMENTAL",
            self.cmake)
        self.assertIn("GOEMON_JP_NATIVE_F6_PAUSE_ACTIVE", self.cmake)

    def test_all_five_runtime_owners_replace_integrated_sources(self):
        for old, new, _ in SOURCE_FILES.values():
            self.assertIn(old, self.cmake)
            self.assertIn(new, self.cmake)
            self.assertIn(f'("{old}", "{new}"', self.src)
        self.assertIn("JP_PAUSE_RESULT EQUAL 0", self.cmake)

    def test_f6_is_dispatched_on_host_sdl_pump_not_vi_poll(self):
        # A paused VI worker stops poll_inputs(). The host SDL event pump
        # must still process F6 release/repress without guest dispatch.
        section = self.input.split("bool sdl_event_filter(", 1)[1].split(
            "void recomp::handle_events()", 1)[0]
        self.assertIn("SDL_EventType::SDL_KEYDOWN", section)
        self.assertIn("handle_pause_binding(recomp::InputDevice::Keyboard", section)
        self.assertIn("handle_pause_binding(recomp::InputDevice::Controller", section)
        handler = self.input.split("static bool handle_pause_binding(", 1)[1].split(
            "bool sdl_event_filter", 1)[0]
        self.assertIn("GameInput::PAUSE_GAME", handler)
        self.assertIn(".input_id = SDL_SCANCODE_F6", self.input)
        self.assertIn("!keyevent->repeat", section)
        self.assertIn("jp_guest_is_paused()", handler)
        self.assertIn("jp_set_guest_paused(!paused)", handler)
        self.assertIn("jp_set_audio_paused(!paused)", handler)
        self.assertIn("!paused && (!ultramodern::is_game_started()", handler)
        self.assertIn("return false; // Host hotkey is not a guest/UI key.", section)
        polling = self.input.split("void recomp::poll_inputs() {", 1)[1]
        self.assertNotIn("jp_pause_was_held", polling)
        self.assertNotIn("InputState.keys[SDL_SCANCODE_F6]", polling)
        self.assertNotIn("jp_set_guest_paused(!paused)", polling)
        # Inspect only the F6 branch. The full event filter correctly
        # contains a separate SDL_QUIT handler on window close.
        for forbidden in ("munmap(", "memset(", "SDL_DestroyWindow(",
                          "ultramodern::quit()", "jp_request_integrated_reset("):
            self.assertNotIn(forbidden, handler)

    def test_f8_while_paused_uses_host_event_pump(self):
        section = self.input.split("bool sdl_event_filter(", 1)[1].split(
            "void recomp::handle_events()", 1)[0]
        paused_reset = section.split(
            "keyevent->keysym.scancode == SDL_SCANCODE_F8", 1)[1]
        self.assertIn("ultramodern::jp_guest_is_paused()", paused_reset)
        self.assertIn("jp_set_guest_paused(false)", paused_reset)
        self.assertIn("jp_set_audio_paused(false)", paused_reset)
        self.assertIn("jp_request_integrated_reset()", paused_reset)
        # Existing non-paused F8 controller/keyboard path remains active.
        self.assertIn("GameInput::RESET_GAME", self.input)

    def test_audio_queue_and_window_are_preserved(self):
        section = self.front.split("void jp_set_audio_paused(bool pause)", 1)[1].split(
            "} // namespace recomp", 1)[0]
        self.assertIn("SDL_PauseAudioDevice(audio_device, pause ? 1 : 0);", section)
        self.assertIn('saved_title + " [PAUSED', section)
        self.assertIn("SDL_SetWindowTitle(window, saved_title.c_str());", section)
        for forbidden in ("SDL_ClearQueuedAudio(", "SDL_CloseAudioDevice(",
                          "SDL_DestroyWindow(", "SDL_CreateWindow("):
            self.assertNotIn(forbidden, section)

    def test_clock_freezes_and_shifts_not_catches_up(self):
        for token in ("TimePoint paused_at_", "bool paused_ = false",
                      "start_ += now - paused_at_;",
                      "return paused_ ? paused_at_ : Clock::now();",
                      "ticks_at(effective_now())",
                      "return effective_now() - start_;"):
            self.assertIn(token, self.clock)
        self.assertIn("paused_ = false;", self.clock)

    def test_single_process_wide_pause_gate(self):
        self.assertIn("inline Controller global_controller{}", self.controller)
        self.assertIn("std::condition_variable cv_", self.controller)
        self.assertIn("wait_if_paused(", self.controller)
        self.assertIn("process_exiting.load(", self.controller)
        self.assertIn("cv_.wait_for(lock, std::chrono::milliseconds{10})", self.controller)

    def test_pause_never_increments_guest_generation(self):
        section = self.src.split("PAUSE_TIMER_API = r", 1)[1].split('"""', 2)[1]
        self.assertIn("jp_timer_epoch_clock.set_paused(pause)", section)
        self.assertIn("controller().set_paused(pause)", section)
        for forbidden in ("rebase_after_verified_exit", "munmap(", "mmap(",
                          "start_game(", "current_game.reset(", "ShutdownTimerAction"):
            self.assertNotIn(forbidden, section)

    def test_timer_resumes_without_late_or_early_interrupt(self):
        self.assertIn("time_now() < cur_timer->timestamp", self.src)
        self.assertIn("active_timers.insert(cur_timer_)", self.src)
        self.assertIn("wait_if_paused(exited)", self.src)

    def test_renderer_input_remains_separate(self):
        self.assertIn("graphics action loop boundary", self.src)
        self.assertIn("VI frame dispatch after wall-clock sleep", self.src)
        self.assertIn("SP task execution checkpoint", self.src)
        self.assertIn("OSThread context resume checkpoint", self.src)
        self.assertIn("external message wake checkpoint", self.src)

    def test_pc_exit_also_releases_pause_gate(self):
        self.assertIn("normal quit releases paused workers", self.src)
        self.assertIn("F8 reset while paused releases guest workers", self.src)
        self.assertIn("jp_set_guest_paused(false)", self.src)

    def test_real_integrated_source_composition(self):
        inp = Path(os.environ.get(
            "JP_PAUSE_INPUT_DIR",
            ROOT / "build-generated/japan-native-game/jp_reset_timer_runtime"))
        missing = [old for old, _, _ in SOURCE_FILES.values()
                   if not (inp / old).is_file()]
        if missing:
            self.skipTest("Requires real integrated generated sources: " + ", ".join(missing))
        emit = os.environ.get("JP_PAUSE_EMIT_DIR")
        for label, (old, new, transform) in SOURCE_FILES.items():
            with self.subTest(owner=label):
                result = transform((inp / old).read_text(encoding="utf-8"))
                self.assertGreater(len(result), 500)
                self.assertIn("// JP F6 cooperative pause:", result)
                if emit:
                    output = Path(emit) / new
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(result, encoding="utf-8")
                with self.assertRaises(ValueError):
                    transform(result)

    def test_native_pause_clock_and_worker_gate(self):
        compiler = shutil.which("g++") or shutil.which("clang++") or shutil.which("c++")
        if compiler is None:
            self.skipTest("No C++ compiler")
        source = SCRIPT.with_name("test_native_pause_resume.cpp")
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "jp_pause_host"
            subprocess.run([compiler, "-std=c++20", "-O2", "-Wall",
                            "-Wextra", "-Werror", "-pthread",
                            str(source), "-o", str(exe)],
                           cwd=ROOT, check=True, timeout=45)
            out = subprocess.run([str(exe)], cwd=ROOT, check=True,
                                 capture_output=True, text=True, timeout=25)
            self.assertIn("JP F6 pause/resume host PASS", out.stdout)
            self.assertIn("no_catchup=1", out.stdout)
            self.assertIn("worker_parked=1", out.stdout)


if __name__ == "__main__":
    unittest.main()
