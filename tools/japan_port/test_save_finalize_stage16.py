#!/usr/bin/env python3
"""JP Stage16 ROM-free tests against pinned-runtime save overlay.

No ROM or save-file bytes are needed. These tests validate only source
instrumentation and the ordering of existing native save APIs.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "lib/N64ModernRuntime/librecomp/src/pi.cpp"
SCRIPT = Path(__file__).with_name("patch_save_finalize_stage16.py")
spec = importlib.util.spec_from_file_location("jp_save_stage16", SCRIPT)
assert spec and spec.loader
stage16 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage16)


class SaveFinalizeStage16Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pinned = RUNTIME.read_text(encoding="utf-8")
        cls.generated = stage16.instrument_pi(cls.pinned)
        cls.cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")

    def test_pinned_source_not_edited(self):
        self.assertNotIn("jp_save_mutation_epoch", self.pinned)
        self.assertIn("jp_save_mutation_epoch", self.generated)
        self.assertIn("src = stage7.instrument_pi(original)", SCRIPT.read_text())

    def test_required_cstdint_and_exception_headers(self):
        self.assertIn("#include <cstdint>", self.generated)
        self.assertIn("#include <exception>", self.generated)

    def test_three_mutations_counted_inside_buffer_mutex(self):
        s = self.generated
        self.assertEqual(
            s.count("jp_save_mutation_epoch.fetch_add(1, std::memory_order_release);"), 3)
        for write in ("memcpy(&save_context.save_buffer[offset], in, count);",
                      "save_context.save_buffer[offset + i] = MEM_B(i, rdram_address);",
                      "std::fill_n(save_context.save_buffer.begin() + start, size, value);"):
            self.assertIn(write, s)

    def test_stage7_save_write_signals_preserved(self):
        self.assertEqual(self.generated.count("save_context.write_sempahore.signal();"), 3)
        self.assertEqual(self.generated.count(
            "jp_save_write_signals.fetch_add(1, std::memory_order_relaxed);"), 3)

    def test_snapshot_captured_inside_save_file_lock(self):
        s = self.generated.split("void update_save_file() {", 1)[1].split(
            "extern std::atomic_bool exited;", 1)[0]
        # The local epoch declaration (= 0) is correctly outside the lock;
        # the *snapshot assignment* must follow the locked file write.
        self.assertIn("std::uint64_t jp_save_snapshot_epoch = 0;", s)
        snapshot_assignment = s.index("jp_save_snapshot_epoch =\n")
        self.assertLess(
            s.index("std::lock_guard lock{ save_context.save_buffer_mutex }"),
            snapshot_assignment)
        self.assertLess(
            s.index("save_file.write(save_context.save_buffer.data()"),
            snapshot_assignment)
        self.assertIn("if (!save_file.good()) saving_failed = true;", s)

    def test_finalized_epoch_only_on_success(self):
        s = self.generated.split("void update_save_file() {", 1)[1].split(
            "extern std::atomic_bool exited;", 1)[0]
        self.assertIn("if (!saving_failed) {", s)
        self.assertLess(s.index("jp_save_flush_successes.fetch_add("),
                        s.index("jp_save_finalized_epoch.store("))
        self.assertIn("jp_save_snapshot_epoch", s)

    def test_final_flush_only_after_join(self):
        s = self.generated.split("void ultramodern::join_saving_thread() {", 1)[1].split(
            "void do_dma(", 1)[0]
        self.assertLess(s.index("save_context.saving_thread.join();"),
                        s.index("const auto jp_save_exit_expected ="))
        self.assertLess(s.index("const auto jp_save_exit_expected ="),
                        s.index("update_save_file();"))
        self.assertLess(s.index("update_save_file();"),
                        s.index("[Goemon JP save finalize stage16] mutated="))

    def test_no_write_if_no_dirty_mutations(self):
        s = self.generated.split("void ultramodern::join_saving_thread() {", 1)[1].split(
            "void do_dma(", 1)[0]
        self.assertIn("jp_save_exit_expected > jp_save_exit_previous", s)
        self.assertIn("if (jp_save_worker_joined.load(std::memory_order_acquire) &&", s)

    def test_flush_failure_stays_unfinalized(self):
        s = self.generated
        self.assertIn("catch (const std::exception& e)", s)
        self.assertIn("catch (...)", s)
        self.assertIn("jp_save_exit_completed >= jp_save_exit_expected", s)
        self.assertIn("jp_save_exit_finalized.store(jp_save_exit_complete", s)
        self.assertIn("durable=unproven;", s)

    def test_stage12_save_predicate_now_requires_finalization(self):
        s = self.generated.split(
            "bool ultramodern::jp_save_worker_joined_for_future_reset() {", 1)[1].split(
            "void update_save_file() {", 1)[0]
        self.assertIn("jp_save_exit_finalized.load(std::memory_order_acquire)", s)
        self.assertIn("jp_save_worker_joined.load(std::memory_order_acquire)", s)
        self.assertIn("!jp_save_worker_active.load(std::memory_order_acquire)", s)

    def test_previous_jp_save_lifecycle_preserved(self):
        for key in ("[Goemon JP save audit]", "[Goemon JP save exit]",
                    "jp_save_file_io_active", "jp_save_flush_attempts",
                    "jp_save_flush_successes"):
            self.assertIn(key, self.generated)

    def test_generated_source_is_single_application(self):
        with self.assertRaisesRegex(ValueError, "already instrumented"):
            stage16.instrument_pi(self.generated)

    def test_cmake_opt_in_and_replaces_only_pi(self):
        c = self.cmake
        self.assertIn('option(GOEMON_JP_SAVE_STAGE16 "Enable pending save finalization after worker join on PC exit" OFF)', c)
        self.assertIn("GOEMON_JP_SAVE_STAGE16 AND NOT GOEMON_JP_SDL_AUDIO_STAGE15", c)
        self.assertIn("patch_save_finalize_stage16.py", c)
        self.assertIn("pi_stage16.cpp", c)
        self.assertIn("GOEMON_JP_SAVE_STAGE16_ACTIVE", c)

    def test_no_f8_save_operation(self):
        inp = (ROOT / "src/game/input.cpp").read_text()
        f8 = inp.split("[Goemon JP reset] Request received;", 1)[1].split(
            "reset_was_held = reset_held;", 1)[0]
        for forbidden in ("update_save_file(", "jp_save_mutation_epoch",
                          "save_context.", "request_guest_shutdown_for_future_reset();",
                          "memset(", "munmap("):
            self.assertNotIn(forbidden, f8)

    def test_jp_version_usa_unchanged(self):
        main = (ROOT / "src/main/main.cpp").read_text()
        ui = (ROOT / "src/ui/ui_launcher.cpp").read_text()
        self.assertIn('version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn('jp_save_final_build_mode = " [save-finalize]"', main)
        self.assertIn('version_string += " [save-finalize]"', ui)


if __name__ == "__main__":
    unittest.main()
