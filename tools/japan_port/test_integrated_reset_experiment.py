#!/usr/bin/env python3
"""Integrated F8 reset experiment: source and native host regression.

Run AFTER CMake generation to inspect the *actual* generated runtime.
A passing host test is not a claim that the graphics game reset works.
"""
from __future__ import annotations
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "build-generated/japan-native-game/jp_reset_timer_runtime"

class IntegratedResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "tools/japan_port/patch_integrated_reset_experiment.py").read_text()
        cls.cmake = (ROOT / "CMakeLists.txt").read_text()
        cls.input = (ROOT / "src/game/input.cpp").read_text()
        cls.front = (ROOT / "src/main/main.cpp").read_text()
        cls.gate = (ROOT / "src/game/jp_generation_publisher_gate.hpp").read_text()
        cls.clock = (ROOT / "src/game/jp_timer_epoch_clock.hpp").read_text()

    def test_default_off_and_requires_stage18(self):
        self.assertIn('option(GOEMON_JP_INTEGRATED_F8_RESET_EXPERIMENTAL', self.cmake)
        self.assertIn('option(GOEMON_JP_INTEGRATED_F8_RESET_EXPERIMENTAL "Experimental same-process F8 reset (may be unstable; disposable saves only)" OFF)', self.cmake)
        self.assertIn("GOEMON_JP_INTEGRATED_F8_RESET_EXPERIMENTAL AND NOT GOEMON_JP_TIMER_EPOCH_STAGE18", self.cmake)

    def test_frontend_macro_is_defined_after_japanese_target_exists(self):
        # CMake refuses target_compile_definitions for a not-yet-created
        # executable. This failed an actual Ubuntu configure (17 Nov).
        creation = self.cmake.index(
            "add_executable(Goemon64RecompiledJP ${JP_SOURCES})")
        frontend = self.cmake[creation:]
        self.assertRegex(
            frontend,
            r"if\(GOEMON_JP_INTEGRATED_F8_RESET_EXPERIMENTAL\)\s+(?:#[^\n]*\s+)*"
            r"foreach\(region_target Goemon64Recompiled Goemon64RecompiledJP\)\s+"
            r"target_compile_definitions\(\$\{region_target\} PRIVATE\s+"
            r"GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE\b",
        )

    def test_both_regional_frontends_link_the_real_audio_bridge(self):
        self.assertIn("bool jp_reset_audio_buffer_after_join()", self.front)
        self.assertIn(
            "foreach(region_target Goemon64Recompiled Goemon64RecompiledJP)", self.cmake)
        self.assertIn(
            "GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE GOEMON_JP_SDL_AUDIO_STAGE15_ACTIVE",
            self.cmake)
        self.assertNotIn("jp_integrated_audio_unavailable_usa.cpp", self.cmake)

    def test_generated_runtime_owners_together(self):
        for name in ("threads_reset.cpp", "threadqueue_integrated.cpp", "mesgqueue_integrated.cpp", "events_integrated.cpp",
                     "timer_integrated.cpp", "pi_integrated.cpp",
                     "recomp_integrated.cpp"):
            self.assertIn(name, self.script)
            self.assertIn(name, self.cmake)
        self.assertIn("JP_INTEGRATED_RESULT EQUAL 0", self.cmake)

    def test_no_process_replacement(self):
        for token in ("execv(", "execvp(", "system(", "fork(", "posix_spawn("):
            self.assertNotIn(token, self.script)
            self.assertNotIn(token, self.front)

    def test_requested_from_f8_but_not_direct_memory_cleanup(self):
        h = self.input.split("[Goemon JP reset] Request received;", 1)[0]
        self.assertIn("jp_request_integrated_reset();", self.input)
        self.assertIn("#ifdef GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE", self.input)
        for token in ("munmap(", "memset(", "request_guest_shutdown_for_future_reset("):
            self.assertNotIn(token, h)

    def test_reset_goes_through_verified_normal_exit_barrier(self):
        self.assertIn("jp_exit_known_joined && jp_guest_exit_drained", self.script)
        self.assertIn("jp_integrated_rdram_released", self.script)
        self.assertIn("jp_external_queue_drained", self.script)
        self.assertIn("reason=unproven-shutdown-or-memory-release", self.script)
        self.assertIn("result=ABORT", self.script)

    def test_native_scheduler_queue_released_only_after_join(self):
        # Static running_queue_impl is not in RDRAM, and survives munmap.
        # Clearing it before all guest/native threads join would be unsafe.
        scheduler_file = ROOT / "lib/N64ModernRuntime/ultramodern/src/threadqueue.cpp"
        if not scheduler_file.is_file():
            self.skipTest("Pinned runtime not checked out")
        original = scheduler_file.read_text(encoding="utf-8")
        self.assertIn(
            "static PTR(OSThread) running_queue_impl = NULLPTR;", original)
        self.assertNotIn("jp_clear_native_running_queue_after_verified_exit", original)
        self.assertIn("jp_native_scheduler_queue_cleared", self.script)
        self.assertIn("jp_exit_known_joined && jp_guest_exit_drained", self.script)
        self.assertIn("jp_native_scheduler_queue_cleared &&", self.script)

        import importlib.util
        path = ROOT / "tools/japan_port/patch_integrated_reset_experiment.py"
        spec = importlib.util.spec_from_file_location(
            "jp_scheduler_rearm_test", path)
        assert spec and spec.loader
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        generated = patch.scheduler(original)
        self.assertIn("if (!joined)", generated)
        self.assertIn("running_queue_impl = NULLPTR;", generated)
        self.assertIn("native_running_queue_cleared=1", generated)
        with self.assertRaisesRegex(ValueError, "already instrumented"):
            patch.scheduler(generated)

    def test_japanese_rom_decompression_is_restored_on_every_boot(self):
        # The upstream runtime mutates has_compressed_code=false after the
        # first load. A second game generation must not load the original
        # *compressed* stored ROM without decompressing it again.
        pinned = (ROOT / "lib/N64ModernRuntime/librecomp/src/recomp.cpp")
        if not pinned.is_file():
            self.skipTest("Pinned runtime not checked out")
        original = pinned.read_text(encoding="utf-8")
        self.assertIn("find_it->second.has_compressed_code = false;", original)
        self.assertIn("if (find_it->second.has_compressed_code) {", original)
        import importlib.util
        path = ROOT / "tools/japan_port/patch_integrated_reset_experiment.py"
        spec = importlib.util.spec_from_file_location(
            "jp_reset_rom_replay_test", path)
        assert spec and spec.loader
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        # Compose the real pinned recomp through every predecessor stage.
        stage7_spec = importlib.util.spec_from_file_location(
            "jp_reset_stage7_rom_test",
            path.with_name("patch_native_boot_save_audio_stage7.py"))
        stage12_spec = importlib.util.spec_from_file_location(
            "jp_reset_stage12_rom_test",
            path.with_name("patch_exit_barrier_stage12.py"))
        stage17_spec = importlib.util.spec_from_file_location(
            "jp_reset_stage17_rom_test",
            path.with_name("patch_timer_producer_fence_stage17.py"))
        assert stage7_spec and stage7_spec.loader
        assert stage12_spec and stage12_spec.loader
        assert stage17_spec and stage17_spec.loader
        stage7 = importlib.util.module_from_spec(stage7_spec)
        stage12 = importlib.util.module_from_spec(stage12_spec)
        stage17 = importlib.util.module_from_spec(stage17_spec)
        stage7_spec.loader.exec_module(stage7)
        stage12_spec.loader.exec_module(stage12)
        stage17_spec.loader.exec_module(stage17)
        generated = patch.recomp(
            stage17.instrument_recomp_after_stage12(
                stage12.instrument_recomp_stage7(
                    stage7.instrument_recomp(original))))
        function = generated.split(
            "bool recomp::load_stored_rom(std::u8string& game_id) {", 1)[1].split(
                "const recomp::Version& recomp::get_project_version()", 1)[0]
        self.assertLess(
            function.index("find_it->second.has_compressed_code = true;"),
            function.index("if (find_it->second.has_compressed_code) {"))
        self.assertLess(
            function.index('game_id == u8"mnsg.jp" || game_id == u8"mnsg.us"'),
            function.index("find_it->second.has_compressed_code = true;"))
        self.assertIn("find_it->second.has_compressed_code = false;", function)
        self.assertIn("restoring compressed game ROM", function)

    def test_rearms_guest_timer_events_and_saving(self):
        for token in ("jp_rearm_guest_after_verified_exit",
                      "jp_rearm_timer_after_verified_exit",
                      "jp_rearm_events_after_verified_exit",
                      "jp_rearm_saving_after_verified_exit"):
            self.assertIn(token, self.script)
        self.assertIn("if (!external_ready)", self.script)
        self.assertIn("jp_rearm_external_messages_after_verified_exit(", self.script)
        self.assertIn("result=REARMED", self.script)

    def test_reuses_same_window_and_audio_device(self):
        self.assertIn("if (window != nullptr)", self.front)
        self.assertIn("SDL window reused=", self.front)
        self.assertIn("jp_reset_audio_buffer_after_join()", self.front)
        self.assertIn("SDL_ClearQueuedAudio(audio_device)", self.front)
        self.assertNotIn("SDL_DestroyWindow(window)", self.front)

    def test_guarded_clock_and_gate_renewal(self):
        self.assertIn("#ifdef GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE", self.gate)
        self.assertIn("!all_old_workers_joined || open_ || in_flight_ != 0", self.gate)
        self.assertIn("generation_ == std::numeric_limits<std::uint64_t>::max()", self.gate)
        self.assertIn("rebase_after_verified_exit", self.clock)
        self.assertIn("!proof.guest_refs_released", self.clock)

    def test_no_unproven_live_memory_clear(self):
        for f in (self.script, self.input):
            self.assertNotIn("memset(rdram", f)
            self.assertNotIn("mprotect(rdram", f)

    def test_generated_runtime_integration(self):
        files = [GEN / name for name in
                 ("threads_reset.cpp","mesgqueue_integrated.cpp","events_integrated.cpp",
                  "timer_integrated.cpp","pi_integrated.cpp",
                  "recomp_integrated.cpp", "threadqueue_integrated.cpp", "overlays_integrated.cpp")]
        if not all(f.exists() for f in files):
            self.skipTest("Run integrated reset CMake configure to generate runtime sources")
        texts = [f.read_text() for f in files]
        self.assertIn("jp_rearm_guest_after_verified_exit()", texts[0])
        self.assertIn("jp_flush_external_messages_after_join(", texts[1])
        self.assertIn("jp_rearm_events_after_verified_exit(", texts[2])
        self.assertIn("jp_rearm_timer_after_verified_exit(", texts[3])
        self.assertIn("jp_rearm_saving_after_verified_exit()", texts[4])
        self.assertIn("jp_request_integrated_reset()", texts[5])
        self.assertIn("jp_exit_known_joined && jp_guest_exit_drained", texts[5])
        self.assertIn("jp_integrated_rdram_released", texts[5])
        self.assertIn("jp_external_queue_drained", texts[5])
        self.assertIn("for (;;) {", texts[5])
        self.assertIn("jp_native_scheduler_queue_cleared", texts[5])
        self.assertIn("jp_clear_native_running_queue_after_verified_exit", texts[6])
        self.assertIn("clear_region_patches()", texts[7])
        self.assertIn("manual_patch_symbols_by_vram.clear();", texts[7])
        self.assertIn("mod_context->unload_mods();", texts[5])

    def test_actual_pinned_runtime_generator_composition(self):
        # Compose the generated overlays against the pinned runtime.
        runtime = ROOT / "lib/N64ModernRuntime"
        inputs = {
            "scheduler": runtime / "ultramodern/src/threadqueue.cpp",
            "overlays": runtime / "librecomp/src/overlays.cpp",
            "threads": runtime / "ultramodern/src/threads.cpp",
            "messages": runtime / "ultramodern/src/mesgqueue.cpp",
            "events": runtime / "ultramodern/src/events.cpp",
            "timer": runtime / "ultramodern/src/timer.cpp",
            "saving": runtime / "librecomp/src/pi.cpp",
            "recomp": runtime / "librecomp/src/recomp.cpp",
        }
        if not all(path.is_file() for path in inputs.values()):
            self.skipTest("Requires pinned N64ModernRuntime checkout")
        import importlib.util

        def module(name, filename):
            spec = importlib.util.spec_from_file_location(
                name, ROOT / "tools/japan_port" / filename)
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

        stage7 = module("jp_reset_stage7_test", "patch_native_boot_save_audio_stage7.py")
        stage9 = module("jp_reset_stage9_test", "patch_guest_exit_stage9.py")
        stage14 = module("jp_reset_stage14_test", "patch_config_publisher_fence_stage14.py")
        stage12 = module("jp_reset_stage12_test", "patch_exit_barrier_stage12.py")
        stage16 = module("jp_reset_stage16_test", "patch_save_finalize_stage16.py")
        stage17 = module("jp_reset_stage17_test", "patch_timer_producer_fence_stage17.py")
        stage18 = module("jp_reset_stage18_test", "patch_timer_epoch_stage18.py")
        integrated = module("jp_reset_integrated_test", "patch_integrated_reset_experiment.py")

        original = {key: path.read_text(encoding="utf-8")
                    for key, path in inputs.items()}
        generated = {
            "scheduler": original["scheduler"],
            "overlays": original["overlays"],
            "threads": stage9.instrument_threads(original["threads"]),
            "messages": stage9.stage8.stage6.stage5.stage4.instrument_messages(original["messages"]),
            "events": stage14.instrument_events(original["events"]),
            "timer": stage18.instrument_stage17_timer(
                stage17.instrument_timer(original["timer"])),
            "saving": stage16.instrument_pi(original["saving"]),
            "recomp": stage17.instrument_recomp_after_stage12(
                stage12.instrument_recomp_stage7(
                    stage7.instrument_recomp(original["recomp"]))),
        }
        methods = {
            "scheduler": integrated.scheduler,
            "overlays": integrated.overlays,
            "threads": integrated.threads,
            "messages": integrated.messages,
            "events": integrated.events,
            "timer": integrated.timer,
            "saving": integrated.saving,
            "recomp": integrated.recomp,
        }
        emit_root = os.environ.get("JP_INTEGRATED_EMIT_DIR")
        outputs = {
            "scheduler": "threadqueue_integrated.cpp",
            "overlays": "overlays_integrated.cpp",
            "threads": "threads_reset.cpp",
            "messages": "mesgqueue_integrated.cpp",
            "events": "events_integrated.cpp",
            "timer": "timer_integrated.cpp",
            "saving": "pi_integrated.cpp",
            "recomp": "recomp_integrated.cpp",
        }
        composed = {}
        for key, transform in methods.items():
            with self.subTest(component=key):
                result = transform(generated[key])
                composed[key] = result
                self.assertGreater(len(result), 500)
                self.assertIn("// Experimental ", result)
                if emit_root:
                    destination = Path(emit_root) / outputs[key]
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(result, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "already"):
                    transform(result)

        if all(key in composed for key in ("scheduler", "threads", "messages")):
            scheduling = stage9.stage8.stage6.stage5.instrument_scheduler(
                (runtime / "ultramodern/src/scheduling.cpp").read_text(encoding="utf-8"))
            pause = module("jp_pause_for_message_test", "patch_native_pause_resume.py")
            self.run_message_continuation_regression(runtime, {
                "threadqueue.cpp": composed["scheduler"],
                "threads.cpp": pause.threads(composed["threads"]),
                "mesgqueue.cpp": pause.messages(composed["messages"]),
                "scheduling.cpp": scheduling,
            })

    def run_message_continuation_regression(self, runtime, sources):
        compiler = shutil.which("g++") or shutil.which("c++")
        if compiler is None:
            return
        with tempfile.TemporaryDirectory() as temp:
            build = Path(temp)
            for name, source in sources.items():
                (build / name).write_text(source, encoding="utf-8")
            fixture = Path(__file__).with_name("test_message_continuation.cpp")
            probe_tools = fixture.with_name("native_compile")
            sdk = runtime / "N64Recomp/include"
            includes = [
                runtime / "ultramodern/include",
                runtime / "thirdparty/concurrentqueue",
                runtime / "thirdparty",
                ROOT / "src/game",
                probe_tools,
                sdk,
            ]
            (build / "funcs.h").write_text(
                "void nested(uint8_t*, recomp_context*);\n"
                "void osRecvMesg_recomp(uint8_t*, recomp_context*);\n"
                "void osSendMesg_recomp(uint8_t*, recomp_context*);\n")
            (build / "funcs_0.c").write_text("""#include "recomp.h"
#include "funcs.h"

RECOMP_FUNC void entry(uint8_t* rdram, recomp_context* ctx) {
    uint64_t hi = 0, lo = 0, result = 0;
    int c1cs = 0;
    hi = 13;
    MEM_W(0, ctx->r8) += 1;
    nested(rdram, ctx);
    ctx->r16 = hi + ctx->r17;
    return;
;}
RECOMP_FUNC void nested(uint8_t* rdram, recomp_context* ctx) {
    uint64_t hi = 0, lo = 0, result = 0;
    int c1cs = 0;
    lo = 7;
    if (ctx->r7 == 0) {
        osRecvMesg_recomp(rdram, ctx);
    } else {
        osSendMesg_recomp(rdram, ctx);
    }
    ctx->r17 = lo + ctx->r2;
    return;
;}
""")
            probe_source = build / "checkpoint.c"
            subprocess.run([
                sys.executable, str(probe_tools / "generate_checkpoint_probe.py"),
                "--generated", str(build), "--function", "entry",
                "--callee", "nested", "--message-continuations",
                "--instrument-root-source", str(build / "funcs_0.c"), "--output", str(probe_source),
            ], check=True, cwd=ROOT, timeout=10)
            probe_object = build / "checkpoint.o"
            subprocess.run([
                shutil.which("cc") or compiler, "-x", "c", "-std=gnu17", "-O2",
                *[f"-I{path}" for path in includes],
                "-c", str(probe_source), "-o", str(probe_object),
            ], check=True, cwd=ROOT, timeout=30)
            target = build / "message-continuation"
            subprocess.run([
                compiler, "-std=c++20", "-O2", "-pthread",
                "-DGOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE",
                *[f"-I{path}" for path in includes],
                *[str(build / name) for name in sources],
                str(fixture), str(probe_tools / "message_checkpoint_bridge.cpp"),
                str(probe_object), "-o", str(target),
            ], check=True, cwd=ROOT, timeout=60)
            subprocess.run([str(target)], check=True, cwd=ROOT, timeout=12)

    def test_host_double_reboot_gate_regression(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if compiler is None:
            self.skipTest("No C++ compiler installed")
        source = Path(__file__).with_name("test_integrated_reset_gates.cpp")
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "jp_reset_gates"
            subprocess.run([compiler, "-std=c++20", "-O2", "-Wall",
                            "-Wextra", "-Werror", "-pthread",
                            str(source), "-o", str(target)],
                           check=True, cwd=ROOT, timeout=45)
            result = subprocess.run([str(target)], check=True, cwd=ROOT,
                                    capture_output=True, text=True, timeout=25)
            self.assertIn("generation=3 repeated=2", result.stdout)

if __name__ == "__main__":
    unittest.main()
