#!/usr/bin/env python3
"""Runtime timer stage-1 validation against the pinned N64ModernRuntime tree."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[2]
PATCHER = ROOT / "tools/japan_port/patch_timer_runtime.py"
RUNTIME = ROOT / "lib/N64ModernRuntime"
spec = importlib.util.spec_from_file_location("patch_timer_runtime", PATCHER)
assert spec and spec.loader
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


class NativeTimerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.timer_original = (RUNTIME / "ultramodern/src/timer.cpp").read_text()
        cls.recomp_original = (RUNTIME / "librecomp/src/recomp.cpp").read_text()
        cls.timer = patcher.timer_source(cls.timer_original)
        cls.recomp = patcher.recomp_source(cls.recomp_original)

    def test_original_submodule_is_not_changed(self) -> None:
        self.assertIn("timer_context.thread.detach();", self.timer_original)
        self.assertNotIn("shutdown_timers()", self.timer_original)

    def test_timer_worker_is_joinable_and_stoppable(self) -> None:
        self.assertIn("struct ShutdownTimerAction {}", self.timer)
        self.assertIn("timer_context.thread.join();", self.timer)
        self.assertNotIn("timer_context.thread.detach();", self.timer)
        self.assertIn("shutdown_requested = true;", self.timer)
        self.assertIn("while (!shutdown_requested)", self.timer)
        self.assertIn("while (active_timers.empty() && !shutdown_requested)", self.timer)

    def test_no_guest_timer_dereference_after_shutdown(self) -> None:
        worker = self.timer.split("void timer_thread(", 1)[1].split(
            "void ultramodern::init_timers(", 1)[0]
        self.assertIn("if (shutdown_requested) {\n            break;\n        }", worker)
        self.assertLess(worker.index("if (shutdown_requested) {"), worker.index("PTR(OSTimer) cur_timer_"))

    def test_runtime_joins_timer_before_rdram_unmap(self) -> None:
        self.assertIn("namespace ultramodern { void shutdown_timers(); }", self.timer)
        self.assertIn("namespace ultramodern { void shutdown_timers(); }", self.recomp)
        # Two munmap calls exist: one on allocation/mprotect failure,
        # another at the end of normal shutdown. Check only the latter.
        normal_shutdown = self.recomp.split("    game_thread.join();", 1)[1]
        self.assertIn("    ultramodern::shutdown_timers();", normal_shutdown)
        self.assertLess(normal_shutdown.index("    ultramodern::shutdown_timers();"),
                        normal_shutdown.index("    ultramodern::join_event_threads();"))
        self.assertLess(normal_shutdown.index("    ultramodern::shutdown_timers();"),
                        normal_shutdown.rindex("munmap(rdram, allocation_size)"))

    def test_refuses_unexpected_repatch(self) -> None:
        with self.assertRaises(ValueError):
            patcher.timer_source(self.timer)

    def test_write_if_changed(self) -> None:
        with TemporaryDirectory() as temp:
            target = Path(temp) / "runtime.cpp"
            self.assertTrue(patcher.write_if_changed(target, self.timer))
            original_mtime = target.stat().st_mtime_ns
            self.assertFalse(patcher.write_if_changed(target, self.timer))
            self.assertEqual(target.stat().st_mtime_ns, original_mtime)

    def test_cmake_build_remains_opt_in(self) -> None:
        cmake = (ROOT / "CMakeLists.txt").read_text()
        self.assertIn('option(GOEMON_JP_TIMER_STAGE1 "Enable experimental JP timer lifecycle stage" OFF)', cmake)
        self.assertIn("patch_timer_runtime.py", cmake)
        self.assertIn("target_sources(ultramodern PRIVATE", cmake)
        self.assertIn("target_sources(librecomp PRIVATE", cmake)

    def test_version_is_incremented_for_japan_only(self) -> None:
        source = (ROOT / "src/main/main.cpp").read_text()
        self.assertIn('const std::string version_string = GOEMON_LAUNCHER_VERSION;', source)
        self.assertIn('jp_timer_build_mode = " [timer-stage1]"', source)
        self.assertIn('jp_timer_build_mode = " [timer-off]"', source)
        launcher = (ROOT / "src/ui/ui_launcher.cpp").read_text()
        self.assertIn('version_string += " [timer-stage1]"', launcher)
        self.assertIn('version_string += " [timer-off]"', launcher)
        cmake = (ROOT / "CMakeLists.txt").read_text()
        self.assertIn('target_compile_definitions(Goemon64RecompiledJP PRIVATE GOEMON_JP_TIMER_STAGE1_ACTIVE)', cmake)


if __name__ == "__main__":
    unittest.main()
