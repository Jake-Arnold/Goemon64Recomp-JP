#!/usr/bin/env python3
"""Stage-3 guest shutdown source-contract tests. This is NOT a reset test."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "lib/N64ModernRuntime"
PATCH = ROOT / "tools/japan_port/patch_guest_quiescence_stage3.py"
spec = importlib.util.spec_from_file_location("jp_guest_quiescence", PATCH)
assert spec is not None and spec.loader is not None
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


class GuestStopStage3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.original = (RUNTIME / "ultramodern/src/threads.cpp").read_text(encoding="utf-8")
        cls.output = patcher.instrument_threads(cls.original)

    def test_original_submodule_does_not_contain_reset_stop(self) -> None:
        self.assertNotIn("jp_guest_stop_requested", self.original)
        self.assertIn("thread_context->running.wait();", self.original)

    def test_preserves_stage2_counter_semantics(self) -> None:
        out = self.output
        for name in (
            "jp_guest_threads_created.fetch_add",
            "jp_guest_threads_finished.fetch_add",
            "jp_guest_threads_joined.fetch_add",
            "void ultramodern::report_guest_thread_reset_blockers()",
        ):
            self.assertIn(name, out)

    def test_registry_tracks_guest_lifetimes(self) -> None:
        out = self.output
        self.assertIn("std::unordered_set<UltraThreadContext*> jp_guest_contexts;", out)
        self.assertIn("std::mutex jp_guest_context_mutex;", out)
        self.assertIn("jp_guest_contexts.insert(context);", out)
        self.assertIn("jp_guest_contexts.erase(to_delete);", out)
        create = out.split('extern "C" void osCreateThread(', 1)[1].split(
            'extern "C" void osStopThread(', 1
        )[0]
        self.assertLess(create.index("jp_guest_contexts.insert(context);"),
                        create.index("context->host_thread = std::thread{"))
        # The rejection path decrements before spawning; the successful
        # path decrements only after the host thread acknowledges startup.
        # Test them separately instead of selecting the first decrement.
        self.assertEqual(create.count("jp_guest_creations_in_flight.fetch_sub("), 2)
        initialized = create.index("context->initialized.wait();")
        rejection_decrement = create.index(
            "jp_guest_creations_in_flight.fetch_sub(1, std::memory_order_relaxed);"
        )
        success_decrement = create.index(
            "jp_guest_creations_in_flight.fetch_sub(1, std::memory_order_release);"
        )
        self.assertLess(rejection_decrement, initialized)
        self.assertLess(initialized, success_decrement)
        self.assertLess(
            create.index("jp_guest_threads_created.fetch_add("),
            success_decrement,
        )
        cleaner = out.split("void thread_cleaner_func()", 1)[1].split(
            "void ultramodern::init_thread_cleanup()", 1
        )[0]
        self.assertLess(cleaner.index("to_delete->host_thread.join();"),
                        cleaner.index("jp_guest_contexts.erase(to_delete);"))
        self.assertLess(cleaner.index("jp_guest_contexts.erase(to_delete);"),
                        cleaner.index("delete to_delete;"))

    def test_stop_request_sets_flag_before_waking(self) -> None:
        out = self.output
        self.assertIn("stop=%s;", out)
        self.assertIn('"requested" : "not-armed"', out)
        request = out.split(
            "void ultramodern::request_guest_shutdown_for_future_reset()", 1
        )[1].split("\n}", 1)[0]
        self.assertLess(request.index("jp_guest_stop_requested.store(true"),
                        request.index("context->running.signal();"))
        self.assertIn("std::lock_guard<std::mutex>", request)
        self.assertNotIn("memset(", request)
        self.assertNotIn("munmap(", request)
        self.assertNotIn("execv(", request)
        self.assertNotIn("ultramodern::quit(", request)

    def test_waiting_threads_have_cooperative_checkpoint(self) -> None:
        out = self.output
        wait = out.split("void wait_for_resumed(", 1)[1].split(
            "void resume_thread(", 1
        )[0]
        self.assertLess(wait.index("thread_context->running.wait();"),
                        wait.index("ultramodern::guest_shutdown_checkpoint();"))
        self.assertLess(wait.index("ultramodern::guest_shutdown_checkpoint();"),
                        wait.index("TO_PTR(OSThread,"))
        self.assertIn("throw ultramodern::thread_terminated{};", out)

    def test_shutdown_skips_new_guest_entry_and_successor_scheduling(self) -> None:
        worker = self.output.split("static void _thread_func(", 1)[1].split(
            'extern "C" void osStartThread(', 1
        )[0]
        self.assertIn("if (!jp_guest_stop_requested.load(", worker)
        self.assertIn("run_thread_function(PASS_RDRAM", worker)
        self.assertIn("if (!jp_guest_stop_requested.load(", worker.split(
            "self->context = nullptr;", 1
        )[1])

    def test_pending_creations_rejected(self) -> None:
        create = self.output.split('extern "C" void osCreateThread(', 1)[1].split(
            'extern "C" void osStopThread(', 1
        )[0]
        self.assertIn("if (jp_guest_stop_requested.load(", create)
        self.assertIn("jp_guest_creations_in_flight.fetch_add", create)
        self.assertIn("jp_guest_creations_in_flight.fetch_sub", create)
        self.assertIn("throw ultramodern::thread_terminated{};", create)

    def test_f8_only_reports_and_never_arms_stop(self) -> None:
        src = (ROOT / "src/game/input.cpp").read_text()
        self.assertIn("ultramodern::report_guest_shutdown_readiness();", src)
        self.assertNotIn("request_guest_shutdown_for_future_reset();", src)
        reset_handler = src.split("// Input plumbing only:", 1)[1].split(
            "\nvoid recomp::set_rumble(", 1
        )[0]
        self.assertNotIn("ultramodern::quit(", reset_handler)
        self.assertNotIn("execv(", reset_handler)
        self.assertIn("reset_was_held = reset_held;", reset_handler)

    def test_source_guards_and_compilation_options(self) -> None:
        cmake = (ROOT / "CMakeLists.txt").read_text()
        self.assertIn('option(GOEMON_JP_GUEST_STAGE3 "Enable JP guest cooperative stop primitives (disabled in UI)" OFF)', cmake)
        self.assertIn("GOEMON_JP_GUEST_STAGE3 AND NOT GOEMON_JP_THREAD_STAGE2", cmake)
        self.assertIn("patch_guest_quiescence_stage3.py", cmake)
        self.assertIn("GOEMON_JP_GUEST_STAGE3_ACTIVE", cmake)
        self.assertIn("GOEMON_JP_THREAD_STAGE2_ACTIVE", cmake)

    def test_build_version_marker(self) -> None:
        main = (ROOT / "src/main/main.cpp").read_text()
        self.assertIn('const std::string version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn('jp_guest_build_mode = " [guest-prep]"', main)
        ui = (ROOT / "src/ui/ui_launcher.cpp").read_text()
        self.assertIn('version_string += " [guest-prep]"', ui)

    def test_source_generation_is_idempotent(self) -> None:
        with self.assertRaises(ValueError):
            patcher.instrument_threads(self.output)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "threads.cpp"
            self.assertTrue(patcher.write_if_changed(target, self.output))
            initial = target.stat().st_mtime_ns
            self.assertFalse(patcher.write_if_changed(target, self.output))
            self.assertEqual(initial, target.stat().st_mtime_ns)


if __name__ == "__main__":
    unittest.main()
