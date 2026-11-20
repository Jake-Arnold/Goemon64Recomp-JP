#!/usr/bin/env python3
"""Stage17 ROM-free regression: pinned timer publisher gate and RDRAM safety."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "lib/N64ModernRuntime"
SCRIPT = Path(__file__).with_name("patch_timer_producer_fence_stage17.py")
spec = importlib.util.spec_from_file_location("jp_timer_stage17", SCRIPT)
assert spec and spec.loader
stage17 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage17)


class Stage17TimerProducerFence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pin_timer = (RUNTIME / "ultramodern/src/timer.cpp").read_text()
        cls.timer = stage17.instrument_timer(cls.pin_timer)
        cls.pin_recomp = (RUNTIME / "librecomp/src/recomp.cpp").read_text()
        import importlib
        stage12_path = SCRIPT.with_name("patch_exit_barrier_stage12.py")
        stage7_path = SCRIPT.with_name("patch_native_boot_save_audio_stage7.py")
        s7 = importlib.util.spec_from_file_location("jp_stage7_for17", stage7_path)
        s12 = importlib.util.spec_from_file_location("jp_stage12_for17", stage12_path)
        assert s7 and s7.loader and s12 and s12.loader
        stage7 = importlib.util.module_from_spec(s7)
        stage12 = importlib.util.module_from_spec(s12)
        s7.loader.exec_module(stage7)
        s12.loader.exec_module(stage12)
        cls.recomp12 = stage12.instrument_recomp_stage7(
            stage7.instrument_recomp(cls.pin_recomp))
        cls.recomp17 = stage17.instrument_recomp_after_stage12(cls.recomp12)
        cls.cmake = (ROOT / "CMakeLists.txt").read_text()
        cls.input = (ROOT / "src/game/input.cpp").read_text()

    def test_exact_pinned_source_composition(self):
        self.assertIn("stage1.timer_source(original)", SCRIPT.read_text())
        self.assertNotIn("jp_timer_publisher_gate", self.pin_timer)
        self.assertIn("jp_timer_publisher_gate", self.timer)

    def test_f8_admission_is_read_only(self):
        s = self.input.split("[Goemon JP reset] Request received;", 1)[1].split(
            "reset_was_held = reset_held;", 1)[0]
        self.assertIn("ultramodern::jp_report_timer_producer_stage17();", s)
        for disallowed in ("shutdown_timers(", "jp_timer_publisher_gate.close_for_pc_exit(",
                           "request_guest_shutdown_for_future_reset();", "memset(", "munmap("):
            self.assertNotIn(disallowed, s)

    def test_add_timer_admission_before_guest_memory(self):
        s = self.timer.split('extern "C" int osSetTimer(', 1)[1].split(
            'extern "C" int osStopTimer(', 1)[0]
        self.assertLess(s.index("jp_timer_publisher_gate.try_acquire()"),
                        s.index("OSTimer* t = TO_PTR("))
        self.assertLess(s.index("jp_timer_publisher_gate.try_acquire()"),
                        s.index("timer_context.action_queue.enqueue(AddTimerAction"))
        self.assertIn("if (!jp_timer_lease) return -1;", s)

    def test_stop_timer_admission_before_queue_publish(self):
        s = self.timer.split('extern "C" int osStopTimer(', 1)[1].split(
            "#ifdef _WIN32", 1)[0]
        self.assertLess(s.index("jp_timer_publisher_gate.try_acquire()"),
                        s.index("timer_context.action_queue.enqueue(RemoveTimerAction"))
        self.assertIn("if (!jp_timer_lease) return -1;", s)

    def test_timer_close_before_enqueue_shutdown_and_join(self):
        s = self.timer.split("void ultramodern::shutdown_timers() {", 1)[1].split(
            "uint32_t ultramodern::get_speed_multiplier()", 1)[0]
        self.assertLess(s.index("jp_timer_publisher_gate.close_for_pc_exit("),
                        s.index("timer_context.action_queue.enqueue(ShutdownTimerAction"))
        self.assertLess(s.index("timer_context.action_queue.enqueue(ShutdownTimerAction"),
                        s.index("timer_context.thread.join();"))
        self.assertLess(s.index("timer_context.thread.join();"),
                        s.index("jp_timer_exit_worker_joined.store(true"))
        self.assertIn("std::chrono::milliseconds{500}", s)

    def test_timer_exit_predicate_requires_all_proofs(self):
        s = self.timer.split("bool ultramodern::jp_timer_exit_joined_stage17() {",1)[1].split(
            "void ultramodern::init_timers(",1)[0]
        for token in ("jp_timer_exit_gate_drained.load(", "jp_timer_exit_worker_joined.load(",
                      "!timer_context.thread.joinable()", "!state.open",
                      "state.in_flight == 0"):
            self.assertIn(token, s)

    def test_recomp_still_has_stage12_audit(self):
        self.assertIn("[Goemon JP exit barrier stage12]", self.recomp17)
        self.assertIn("jp_exit_save_joined && jp_exit_audio_dispatch_idle &&", self.recomp17)
        self.assertIn("ultramodern::jp_timer_exit_joined_stage17();", self.recomp17)

    def test_rdram_guard_includes_timer_publishers(self):
        s = self.recomp17.split("    // Stage17: RDRAM may only be released", 1)[1]
        self.assertIn("if (jp_guest_exit_drained &&\n        ultramodern::jp_timer_exit_joined_stage17()) {", s)
        self.assertIn("guest/timer join or publisher proof failed;", s)
        self.assertIn("munmap(rdram, allocation_size)", s)

    def test_epoch_not_rearmed(self):
        self.assertIn("epoch_rearm=unproven", self.timer)
        self.assertIn("normal PC exit only.", self.timer)
        self.assertNotIn("rearm_generation(", self.timer)
        self.assertNotIn("start_new_generation(", self.timer)

    def test_generated_versions_and_usa_isolation(self):
        main = (ROOT / "src/main/main.cpp").read_text()
        ui = (ROOT / "src/ui/ui_launcher.cpp").read_text()
        self.assertIn('version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn('jp_timer_producer_build_mode = " [timer-publisher-fence]"', main)
        self.assertIn('version_string += " [timer-publisher-fence]"', ui)

    def test_cmake_replace_exact_timer_and_recomp(self):
        for token in ('option(GOEMON_JP_TIMER_PUBLISH_STAGE17 "Enable timer producer fence and RDRAM guard on PC exit" OFF)',
                      "GOEMON_JP_TIMER_PUBLISH_STAGE17 AND NOT GOEMON_JP_SAVE_STAGE16",
                      "patch_timer_producer_fence_stage17.py",
                      "timer_stage17.cpp", "recomp_stage17.cpp",
                      "GOEMON_JP_TIMER_PUBLISH_STAGE17_ACTIVE"):
            self.assertIn(token, self.cmake)
        self.assertIn("Stage17 requires generated Stage12 exit census", self.cmake)

    def test_reapplication_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "already instrumented"):
            stage17.instrument_timer(self.timer)
        with self.assertRaisesRegex(ValueError, "already instrumented"):
            stage17.instrument_recomp_after_stage12(self.recomp17)


if __name__ == "__main__":
    unittest.main()
