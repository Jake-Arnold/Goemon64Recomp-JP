#!/usr/bin/env python3
"""Static safety tests for v0.2.6-dev guest-thread audit, not a reset test."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "lib/N64ModernRuntime"
PATCH_SCRIPT = ROOT / "tools/japan_port/patch_guest_threads_runtime.py"
spec = importlib.util.spec_from_file_location("jp_guest_audit", PATCH_SCRIPT)
assert spec is not None and spec.loader is not None
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


class GuestThreadAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.original = (RUNTIME / "ultramodern/src/threads.cpp").read_text(encoding="utf-8")
        cls.instrumented = patcher.instrument_threads(cls.original)

    def test_pinned_original_unmodified(self) -> None:
        self.assertNotIn("jp_guest_threads_created", self.original)
        self.assertIn("void ultramodern::cleanup_thread(", self.original)

    def test_all_lifecycle_points_instrumented(self) -> None:
        out = self.instrumented
        self.assertIn("jp_guest_threads_created.fetch_add(1,", out)
        self.assertIn("jp_guest_threads_finished.fetch_add(1,", out)
        self.assertIn("jp_guest_threads_joined.fetch_add(1,", out)
        self.assertIn("std::atomic<uint64_t> jp_guest_threads_created", out)
        self.assertIn("std::atomic<uint64_t> jp_guest_threads_finished", out)
        self.assertIn("std::atomic<uint64_t> jp_guest_threads_joined", out)
        # New counters must not change scheduling or cleanup behaviour.
        self.assertIn("thread_context->initialized.signal();", out)
        self.assertIn("ultramodern::cleanup_thread(thread_context);", out)
        self.assertIn("to_delete->host_thread.join();", out)
        self.assertIn("delete to_delete;", out)

    def test_counter_order(self) -> None:
        out = self.instrumented
        create = out.split('extern "C" void osCreateThread(', 1)[1].split(
            'extern "C" void osStopThread(', 1
        )[0]
        self.assertLess(create.index("context->initialized.wait();"),
                        create.index("jp_guest_threads_created.fetch_add"))
        finish = out.split("static void _thread_func(", 1)[1].split(
            'extern "C" void osStartThread(', 1
        )[0]
        self.assertLess(finish.index("jp_guest_threads_finished.fetch_add"),
                        finish.index("ultramodern::cleanup_thread(thread_context);"))
        cleaner = out.split("void thread_cleaner_func()", 1)[1].split(
            "void ultramodern::init_thread_cleanup()", 1
        )[0]
        self.assertLess(cleaner.index("to_delete->host_thread.join();"),
                        cleaner.index("jp_guest_threads_joined.fetch_add"))
        self.assertLess(cleaner.index("jp_guest_threads_joined.fetch_add"),
                        cleaner.index("delete to_delete;"))

    def test_probe_is_observational_only(self) -> None:
        out = self.instrumented
        probe = out.split("void ultramodern::report_guest_thread_reset_blockers()", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertIn("reset unavailable", probe)
        self.assertIn("std::memory_order_relaxed", probe)
        for unsafe in (
            "ultramodern::quit(", "std::exit(", "std::abort(",
            "execv(", "munmap(", "memset(", "osDestroyThread(",
        ):
            self.assertNotIn(unsafe, probe)

    def test_declared_namespace_member(self) -> None:
        self.assertIn(
            "namespace ultramodern { void report_guest_thread_reset_blockers(); }",
            self.instrumented,
        )

    def test_rejects_repatching(self) -> None:
        with self.assertRaises(ValueError):
            patcher.instrument_threads(self.instrumented)

    def test_noop_generated_file_does_not_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "threads.cpp"
            self.assertTrue(patcher.write_if_changed(path, self.instrumented))
            mtime = path.stat().st_mtime_ns
            self.assertFalse(patcher.write_if_changed(path, self.instrumented))
            self.assertEqual(mtime, path.stat().st_mtime_ns)

    def test_stage_is_japan_only_and_opt_in(self) -> None:
        cmake = (ROOT / "CMakeLists.txt").read_text()
        self.assertIn(
            'option(GOEMON_JP_THREAD_STAGE2 "Enable Japanese guest thread reset audit" OFF)',
            cmake,
        )
        self.assertIn("GOEMON_JP_THREAD_STAGE2 AND NOT GOEMON_JP_TIMER_STAGE1", cmake)
        self.assertIn(
            "target_compile_definitions(Goemon64RecompiledJP PRIVATE GOEMON_JP_THREAD_STAGE2_ACTIVE)",
            cmake,
        )
        self.assertIn("patch_guest_threads_runtime.py", cmake)

    def test_f8_probe_keeps_game_alive(self) -> None:
        src = (ROOT / "src/game/input.cpp").read_text()
        self.assertIn("#ifdef GOEMON_JP_THREAD_STAGE2_ACTIVE", src)
        self.assertIn("ultramodern::report_guest_thread_reset_blockers();", src)
        reset_code = src.split("// Input plumbing only:", 1)[1].split(
            "reset_was_held = reset_held;", 1
        )[0]
        self.assertIn("!recomp::all_input_disabled()", reset_code)
        self.assertIn("reset_held && !reset_was_held", reset_code)
        for unsafe in ("ultramodern::quit();", "execv(", "osDestroyThread("):
            self.assertNotIn(unsafe, reset_code)

    def test_japan_version_and_mode_markers(self) -> None:
        main = (ROOT / "src/main/main.cpp").read_text()
        self.assertIn('version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn('[threads-audit]', main)
        self.assertIn('[threads-off]', main)
        launcher = (ROOT / "src/ui/ui_launcher.cpp").read_text()
        self.assertIn('version_string += " [threads-audit]"', launcher)


if __name__ == "__main__":
    unittest.main()
