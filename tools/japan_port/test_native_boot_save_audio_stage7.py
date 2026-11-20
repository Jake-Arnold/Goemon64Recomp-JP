#!/usr/bin/env python3
"""JP v0.2.12 stage-7 source tests; they do not prove a real reset."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "lib/N64ModernRuntime"
SCRIPT = ROOT / "tools/japan_port/patch_native_boot_save_audio_stage7.py"
spec = importlib.util.spec_from_file_location("jp_stage7", SCRIPT)
assert spec is not None and spec.loader is not None
stage7 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage7)


class NativeBootSaveAudioStage7Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_boot = (RUNTIME / "librecomp/src/recomp.cpp").read_text()
        cls.raw_pi = (RUNTIME / "librecomp/src/pi.cpp").read_text()
        cls.raw_audio = (RUNTIME / "ultramodern/src/audio.cpp").read_text()
        cls.boot = stage7.instrument_recomp(cls.raw_boot)
        cls.pi = stage7.instrument_pi(cls.raw_pi)
        cls.audio = stage7.instrument_audio(cls.raw_audio)

    def test_pinned_sources_remain_unmodified(self):
        self.assertNotIn("jp_boot_thread_active", self.raw_boot)
        self.assertNotIn("jp_save_worker_initialized", self.raw_pi)
        self.assertNotIn("jp_audio_dispatch_inflight", self.raw_audio)

    def test_boot_worker_raii_precedes_preinit(self):
        launch = self.boot.split(
            "std::thread game_thread{[](ultramodern::renderer::WindowHandle ", 1
        )[1].split("}, window_handle, rdram};", 1)[0]
        self.assertLess(
            launch.index("JpBootActiveScope jp_native_scope(jp_boot_thread_active);"),
            launch.index("ultramodern::preinit(rdram, window_handle);"),
        )
        self.assertIn("~JpBootActiveScope()", self.boot)

    def test_boot_entrypoint_scoped_and_counted(self):
        boot = self.boot.split("bool wait_for_game_started(", 1)[1].split(
            "recomp::SaveType recomp::get_save_type()", 1
        )[0]
        self.assertLess(
            boot.index("jp_boot_entry_calls.fetch_add(1"),
            boot.index("game_entry.entrypoint(rdram, context);"),
        )
        self.assertLess(
            boot.index("JpBootActiveScope jp_entry_scope(jp_boot_entry_active);"),
            boot.index("game_entry.entrypoint(rdram, context);"),
        )

    def test_boot_join_precedes_timer_and_unmap(self):
        # Scope the ordering assertion to normal application shutdown.
        # The same source also unmaps RDRAM earlier on allocation failure.
        shutdown = self.boot.split("    graphics_shutdown_ready.signal();\n", 1)[1]
        points = [
            shutdown.index("game_thread.join();"),
            shutdown.index("jp_boot_thread_joined.store(true, std::memory_order_release);"),
            shutdown.index("ultramodern::shutdown_timers();"),
            shutdown.index("ultramodern::join_event_threads();"),
            shutdown.index("ultramodern::join_thread_cleaner_thread();"),
            shutdown.index("ultramodern::join_saving_thread();"),
            shutdown.index("// Free rdram."),
            shutdown.index("munmap(rdram, allocation_size)"),
        ]
        self.assertEqual(points, sorted(points))
        self.assertIn("[Goemon JP boot exit]", shutdown)

    def test_boot_join_predicate_is_not_reset_barrier(self):
        self.assertIn("bool recomp::jp_native_boot_joined_for_future_reset()", self.boot)
        self.assertIn("!jp_boot_entry_active.load", self.boot)
        # The early namespace forward declaration necessarily ends in ();
        # exactly one such occurrence is allowed (the declaration, not a call).
        self.assertIn("    bool jp_native_boot_joined_for_future_reset();", self.boot)
        self.assertEqual(self.boot.count("jp_native_boot_joined_for_future_reset();"), 1)

    def test_save_worker_lifetime_and_join(self):
        s = self.pi
        self.assertIn("JpSaveActiveScope jp_save_scope(jp_save_worker_active);", s)
        self.assertIn("jp_save_worker_initialized.store(true", s)
        self.assertLess(
            s.index("save_context.saving_thread.join();"),
            s.index("jp_save_worker_joined.store(true, std::memory_order_release);"),
        )
        self.assertIn("[Goemon JP save exit]", s)

    def test_save_signals_are_counted_without_rewriting_payloads(self):
        s = self.pi
        self.assertEqual(s.count("save_context.write_sempahore.signal();"), 3)
        self.assertEqual(
            s.count("jp_save_write_signals.fetch_add(1, std::memory_order_relaxed);"),
            3,
        )
        self.assertIn("save_file.write(save_context.save_buffer.data()", s)

    def test_save_io_scope_covers_file_finalization(self):
        fn = self.pi.split("void update_save_file() {", 1)[1].split(
            "void saving_thread_func(", 1
        )[0]
        self.assertLess(
            fn.index("JpSaveActiveScope jp_save_file_scope(jp_save_file_io_active);"),
            fn.index("recomp::finalize_output_file_with_backup("),
        )
        self.assertLess(
            fn.index("recomp::finalize_output_file_with_backup("),
            fn.index("jp_save_flush_successes.fetch_add("),
        )
        self.assertIn("jp_save_flush_attempts.fetch_add(", fn)

    def test_saving_report_does_not_claim_durability(self):
        self.assertIn("pending/durable writes unproven.", self.pi)
        self.assertIn("durability unproven.", self.pi)
        self.assertIn("bool ultramodern::jp_save_worker_joined_for_future_reset()", self.pi)

    def test_audio_callback_handoff_is_guarded(self):
        s = self.audio
        fn = s.split("void ultramodern::queue_audio_buffer(", 1)[1].split(
            "// For SDL2", 1
        )[0]
        self.assertIn("JpAudioDispatchScope jp_dispatch_scope;", fn)
        self.assertIn("jp_audio_queued_chunks.fetch_add(1", fn)
        self.assertIn("jp_audio_samples_submitted.fetch_add(sample_count", fn)
        self.assertLess(
            fn.index("JpAudioDispatchScope jp_dispatch_scope;"),
            fn.index("audio_callbacks.queue_samples(TO_PTR"),
        )
        self.assertIn("~JpAudioDispatchScope()", s)

    def test_audio_initialization_and_non_sdl_predicate(self):
        s = self.audio
        self.assertIn("jp_audio_callbacks_installed.store(true", s)
        self.assertIn("jp_audio_initialized.store(true", s)
        self.assertIn("bool ultramodern::jp_audio_dispatch_idle_for_future_reset()", s)
        self.assertIn("SDL device unverified.", s)

    def test_sdl_device_is_read_only(self):
        main = (ROOT / "src/main/main.cpp").read_text()
        fn = main.split("void jp_report_sdl_audio_queue() {", 1)[1].split(
            "\n}\n#endif", 1
        )[0]
        self.assertIn("SDL_GetQueuedAudioSize(audio_device)", fn)
        self.assertIn("playback still active.", fn)
        for forbidden in ("SDL_ClearQueuedAudio(", "SDL_CloseAudioDevice(", "SDL_PauseAudioDevice("):
            self.assertNotIn(forbidden, fn)

    def test_f8_reports_only_without_quit_or_reset(self):
        s = (ROOT / "src/game/input.cpp").read_text()
        for call in (
            "recomp::jp_report_native_boot_lifecycle();",
            "ultramodern::jp_report_save_worker_lifecycle();",
            "ultramodern::jp_report_audio_dispatch_lifecycle();",
            "jp_report_sdl_audio_queue();",
        ):
            self.assertIn(call, s)
        handler = s.split("// Input plumbing only:", 1)[1].split(
            "\nvoid recomp::set_rumble(", 1
        )[0]
        for forbidden in ("ultramodern::quit(", "execv(", "munmap(", "memset(",
                          "request_guest_shutdown_for_future_reset();"):
            self.assertNotIn(forbidden, handler)

    def test_cmake_stage_is_opt_in_and_replaces_three_sources(self):
        cmake = (ROOT / "CMakeLists.txt").read_text()
        self.assertIn('option(GOEMON_JP_BOOT_STAGE7 "Enable JP native boot, saving and audio lifecycle audit" OFF)', cmake)
        self.assertIn("GOEMON_JP_BOOT_STAGE7 AND NOT GOEMON_JP_EVENTS_STAGE6", cmake)
        self.assertIn("patch_native_boot_save_audio_stage7.py", cmake)
        for path in ("/librecomp/src/pi.cpp", "/ultramodern/src/audio.cpp",
                     "recomp_stage7.cpp", "pi_stage7.cpp", "audio_stage7.cpp"):
            self.assertIn(path, cmake)

    def test_version_is_jp_only(self):
        main = (ROOT / "src/main/main.cpp").read_text()
        ui = (ROOT / "src/ui/ui_launcher.cpp").read_text()
        self.assertIn('const std::string version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn('jp_boot_build_mode = " [boot-save-audio-audit]"', main)
        self.assertIn('version_string += " [boot-save-audio-audit]"', ui)

    def test_double_generation_fails_closed(self):
        with self.assertRaises(ValueError):
            stage7.instrument_recomp(self.boot)
        with self.assertRaisesRegex(ValueError, "already applied"):
            stage7.instrument_pi(self.pi)
        with self.assertRaisesRegex(ValueError, "already applied"):
            stage7.instrument_audio(self.audio)

    def test_generated_files_do_not_change_on_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pi_stage7.cpp"
            self.assertTrue(stage7.stage1.write_if_changed(path, self.pi))
            mtime = path.stat().st_mtime_ns
            self.assertFalse(stage7.stage1.write_if_changed(path, self.pi))
            self.assertEqual(path.stat().st_mtime_ns, mtime)


if __name__ == "__main__":
    unittest.main()
