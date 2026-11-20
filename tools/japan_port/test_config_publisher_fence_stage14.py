#!/usr/bin/env python3
"""ROM-free Stage14 source and C++ concurrent publication-fence tests."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).with_name("patch_config_publisher_fence_stage14.py")
spec = importlib.util.spec_from_file_location("jp_stage14_gate", SCRIPT)
assert spec and spec.loader
stage14 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage14)

STAGE11_EVENTS = """
namespace ultramodern {
    void jp_report_live_generation_preflight();
}
static std::mutex jp_config_action_publish_mutex;
static bool jp_config_actions_closed_for_pc_exit = false;
bool ultramodern::jp_drain_action_queue_after_pc_exit(bool all_guests_joined) {
    if (!all_guests_joined || some_native_worker) {
        return false;
    }
    std::lock_guard<std::mutex> lock(jp_config_action_publish_mutex);
    jp_config_actions_closed_for_pc_exit = true;
    Action queued;
    while (events_context.action_queue.try_dequeue(queued)) {}
    return true;
}
void ultramodern::trigger_config_action() {
    // The renderer has exited: do not enqueue stale UI config work.
    std::lock_guard<std::mutex> lock(jp_config_action_publish_mutex);
    if (jp_config_actions_closed_for_pc_exit) return;
    events_context.action_queue.enqueue(UpdateConfigAction{});
}
"""

class PublisherFenceStage14(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(stage14.stage11, "instrument_events",
                          side_effect=lambda _: STAGE11_EVENTS):
            cls.generated = stage14.instrument_events("PINNED_EVENTS_STUB")
        cls.cmake = (ROOT / "CMakeLists.txt").read_text()
        cls.inp = (ROOT / "src/game/input.cpp").read_text()
        cls.header = (ROOT / "src/game/jp_generation_publisher_gate.hpp").read_text()

    def test_stage11_composition(self):
        self.assertIn("src = stage11.instrument_events(original)", SCRIPT.read_text())
        self.assertIn('#include "jp_generation_publisher_gate.hpp"', self.generated)
        self.assertIn("void jp_report_live_generation_preflight();", self.generated)

    def test_wraps_real_config_enqueue(self):
        s = self.generated.split("void ultramodern::trigger_config_action() {",1)[1]
        self.assertLess(s.index("jp_config_publisher_gate.try_acquire()"),
                        s.index("events_context.action_queue.enqueue(UpdateConfigAction{})"))
        self.assertIn("if (!jp_permit) return;", s)

    def test_close_before_drain_after_workers(self):
        s = self.generated.split("bool ultramodern::jp_drain_action_queue_after_pc_exit(bool all_guests_joined) {",1)[1]
        self.assertLess(s.index("if (!all_guests_joined"),
                        s.index("jp_config_publisher_gate.close_for_pc_exit("))
        self.assertLess(s.index("jp_config_publisher_gate.close_for_pc_exit("),
                        s.index("action_queue.try_dequeue(queued)"))
        self.assertIn("std::chrono::milliseconds{500}", s)
        self.assertIn("publisher permit timeout, skip action drain", s)

    def test_live_snapshot_readonly(self):
        s = self.generated.split("void ultramodern::jp_report_config_action_producer_gate() {",1)[1].split(
            "bool ultramodern::jp_drain_action_queue_after_pc_exit",1)[0]
        self.assertIn("jp_config_publisher_gate.snapshot()", s)
        self.assertIn("f8_close=0", s)
        for call in ("try_acquire(", "close_for_pc_exit(", "try_dequeue(", "munmap(", "memset("):
            self.assertNotIn(call, s)

    def test_no_live_close_or_guest_stop(self):
        s = self.inp.split("[Goemon JP reset] Request received;",1)[1].split(
            "reset_was_held = reset_held;",1)[0]
        self.assertIn("jp_report_config_action_producer_gate();", s)
        self.assertNotIn("close_for_pc_exit(", s)
        self.assertNotIn("request_guest_shutdown_for_future_reset();", s)

    def test_close_permanent_and_generation_tagged(self):
        for token in ("class PublisherGate final", "close_for_pc_exit(",
                      "open_ = false;", "in_flight_ == 0", "std::uint64_t generation_"):
            self.assertIn(token, self.header)
        self.assertNotIn("reopen(", self.header)
        # Production rearm is restricted to a separately opted-in,
        # post-join integrated backend; default Stage14 remains exit-only.
        self.assertIn("#ifdef GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE", self.header)
        self.assertIn("rearm_after_verified_exit(", self.header)
        self.assertIn("!all_old_workers_joined || open_ || in_flight_ != 0", self.header)

    def test_raii_noncopyable_leases(self):
        for token in ("~Permit() { release(); }", "Permit(const Permit&) = delete;",
                      "Permit(Permit&& other) noexcept", "++in_flight_;", "--in_flight_;"):
            self.assertIn(token, self.header)

    def test_cmake_opt_in_only(self):
        self.assertIn('option(GOEMON_JP_CONFIG_FENCE_STAGE14 "Enable counted generation config producer fence on PC exit" OFF)', self.cmake)
        self.assertIn("GOEMON_JP_CONFIG_FENCE_STAGE14 AND NOT GOEMON_JP_GENERATION_STAGE13", self.cmake)
        self.assertIn("patch_config_publisher_fence_stage14.py", self.cmake)
        self.assertIn("target_include_directories(ultramodern PRIVATE", self.cmake)
        self.assertIn("GOEMON_JP_CONFIG_FENCE_STAGE14_ACTIVE", self.cmake)

    def test_version_tags(self):
        main = (ROOT / "src/main/main.cpp").read_text()
        ui = (ROOT / "src/ui/ui_launcher.cpp").read_text()
        self.assertIn('version_string = GOEMON_LAUNCHER_VERSION', main)
        self.assertIn("[config-publisher-fence]", main)
        self.assertIn("[config-publisher-fence]", ui)

    def test_duplicate_rejected(self):
        with self.assertRaisesRegex(ValueError, "already instrumented"):
            stage14.instrument_events(self.generated)

    def test_host_concurrency_binary(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if compiler is None:
            self.skipTest("C++ compiler unavailable")
        source = Path(__file__).with_suffix(".cpp")
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "stage14_gate"
            subprocess.run([compiler, "-std=c++20", "-Wall", "-Wextra",
                            "-Werror", "-O2", "-pthread", str(source), "-o", str(target)],
                           cwd=ROOT, check=True, timeout=45)
            result = subprocess.run([str(target)], cwd=ROOT, capture_output=True,
                                    text=True, check=True, timeout=20)
            self.assertIn("Stage14 host publisher gate: PASS", result.stdout)
            self.assertIn("attempts=30000", result.stdout)

if __name__ == "__main__":
    unittest.main()
