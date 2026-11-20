#!/usr/bin/env python3
"""Build and run the real JP Linux session logger without the N64 runtime."""
from __future__ import annotations

import os
import resource
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
LOGGER = ROOT / "src/main/jp_session_logging.cpp"
MAIN = ROOT / "src/main/main.cpp"
CMAKE = ROOT / "CMakeLists.txt"

HARNESS = r"""
#include "jp_session_logging.h"
#include <cstdio>
#include <cstdlib>
#include <csignal>
#include <cstring>
#include <unistd.h>
#include <thread>
#include <chrono>

int main(int argc, char** argv) {
    const char* mode = argc > 1 ? argv[1] : "normal";
    if (std::strcmp(mode, "noinit") == 0) return 0;
    if (!goemon_jp::logging::start_session("vtest", "test-git-sha")) return 70;
    if (std::strcmp(mode, "brokenconsole") == 0)
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
    std::fprintf(stdout, "[test-stdout] a normal game message\n");
    std::fprintf(stderr, "[test-stderr] a runtime error diagnostic\n");
    if (std::strcmp(mode, "segv") == 0) { std::fflush(nullptr); raise(SIGSEGV); }
    if (std::strcmp(mode, "abort") == 0) { std::fflush(nullptr); raise(SIGABRT); }
    if (std::strcmp(mode, "quick") == 0) std::quick_exit(17);
    if (std::strcmp(mode, "heldpipe") == 0) {
        const pid_t child = fork();
        if (child < 0) return 89;
        if (child == 0) {
            // Retain only stdout and stderr's logger pipe writer. Drop
            // inherited parent capture descriptors so communicate() ends.
            for (int fd = 3; fd < 256; ++fd) close(fd);
            sleep(2);
            _exit(0);
        }
    }
    return 0;
}
"""


@unittest.skipUnless(os.name == "posix" and os.uname().sysname == "Linux",
                     "Linux-only native session logger")
class PersistentLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("clang++") or shutil.which("g++")
        if not cls.compiler:
            raise unittest.SkipTest("No C++20 compiler available")
        cls.build_dir = tempfile.TemporaryDirectory(prefix="goemon-jp-logger-build-")
        cls.binary = Path(cls.build_dir.name) / "log-harness"
        harness_path = Path(cls.build_dir.name) / "harness.cpp"
        harness_path.write_text(HARNESS)
        subprocess.run([
            cls.compiler, "-std=c++20", "-pthread",
            "-I", str(LOGGER.parent),
            str(LOGGER), str(harness_path), "-o", str(cls.binary),
        ], check=True, capture_output=True, text=True, timeout=120)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "build_dir"):
            cls.build_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="goemon-jp-session-")
        self.home = Path(self.temp.name)
        self.logdir = self.home / "xdg-state/goemon64recomp-jp/logs"

    def tearDown(self):
        self.temp.cleanup()

    def launch(self, mode="normal"):
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = str(self.home / "xdg-state")
        env["HOME"] = str(self.home)
        return subprocess.run(
            [str(self.binary), mode], env=env,
            capture_output=True, text=True, timeout=20,
            # Deliberately triggered SIGSEGV/SIGABRT must not leave huge
            # system core files during the lightweight logger unit tests.
            preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)),
        )

    def logs(self):
        return sorted(self.logdir.glob("session-*.log"))

    def test_captures_stdout_stderr_and_preserves_terminal_output(self):
        p = self.launch()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("[test-stdout]", p.stderr)
        self.assertIn("[test-stderr]", p.stderr)
        self.assertEqual(len(self.logs()), 1)
        text = self.logs()[0].read_text()
        self.assertIn("[test-stdout]", text)
        self.assertIn("[test-stderr]", text)
        self.assertIn("commit=test-git-sha", text)
        self.assertIn("Persistent stdout/stderr capture active", text)
        self.assertEqual(self.logs()[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.logdir.stat().st_mode & 0o777, 0o700)

    def test_crash_marker_and_native_core_semantics(self):
        p = self.launch("segv")
        self.assertEqual(p.returncode, -signal.SIGSEGV, p.stderr)
        text = (self.logdir / "latest.log").read_text()
        self.assertIn("[Goemon JP FATAL SIGNAL] SIGSEGV", text)
        self.assertIn("fault_address=0x", text)
        self.assertIn("[test-stderr]", text)

    def test_abort_marker(self):
        p = self.launch("abort")
        self.assertEqual(p.returncode, -signal.SIGABRT, p.stderr)
        self.assertIn("[Goemon JP FATAL SIGNAL] SIGABRT",
                      (self.logdir / "latest.log").read_text())

    def test_quick_exit_flushes_output(self):
        p = self.launch("quick")
        self.assertEqual(p.returncode, 17, p.stderr)
        text = (self.logdir / "latest.log").read_text()
        self.assertIn("[test-stdout]", text)
        self.assertIn("[test-stderr]", text)

    def test_inherited_pipe_writer_cannot_hang_normal_shutdown(self):
        started = time.monotonic()
        p = self.launch("heldpipe")
        elapsed = time.monotonic() - started
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertLess(elapsed, 1.5, "logger must not wait for inherited write descriptors")
        self.assertIn("[test-stderr]", (self.logdir / "latest.log").read_text())

    def test_disconnected_terminal_does_not_kill_game(self):
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = str(self.home / "xdg-state")
        env["HOME"] = str(self.home)
        p = subprocess.Popen(
            [str(self.binary), "brokenconsole"], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)),
        )
        assert p.stdout is not None and p.stderr is not None
        p.stdout.close()
        p.stderr.close()
        self.assertEqual(p.wait(timeout=5), 0)
        self.assertIn("[test-stderr]", (self.logdir / "latest.log").read_text())

    def test_standalone_start_with_both_standard_descriptors_closed(self):
        env = os.environ.copy()
        env["XDG_STATE_HOME"] = str(self.home / "xdg-state")
        env["HOME"] = str(self.home)

        def launch_without_terminal():
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            os.close(0)
            os.close(1)
            os.close(2)

        result = subprocess.run(
            [str(self.binary), "normal"], env=env,
            capture_output=True, text=True, timeout=10,
            preexec_fn=launch_without_terminal,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[test-stderr]", (self.logdir / "latest.log").read_text())
        self.assertIn("[test-stdout]", (self.logdir / "latest.log").read_text())

    def test_default_home_state_path_when_xdg_state_home_is_unset(self):
        env = os.environ.copy()
        env.pop("XDG_STATE_HOME", None)
        env["HOME"] = str(self.home)
        p = subprocess.run(
            [str(self.binary), "normal"], env=env,
            capture_output=True, text=True, timeout=10,
            preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)),
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        latest = self.home / ".local/state/goemon64recomp-jp/logs/latest.log"
        self.assertTrue(latest.exists())
        self.assertIn("[test-stdout]", latest.read_text())

    def test_noinit_creates_no_log(self):
        self.assertEqual(self.launch("noinit").returncode, 0)
        self.assertFalse(self.logdir.exists())

    def test_latest_symlink_tracks_newest_run(self):
        self.assertEqual(self.launch().returncode, 0)
        old = (self.logdir / "latest.log").resolve()
        self.assertEqual(self.launch().returncode, 0)
        self.assertTrue(old.exists())
        self.assertEqual(len(self.logs()), 2)
        self.assertNotEqual(old, (self.logdir / "latest.log").resolve())

    def test_rotation_keeps_latest_twenty_sessions(self):
        for _ in range(24):
            self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(len(self.logs()), 20)
        self.assertEqual((self.logdir / "latest.log").resolve(), self.logs()[-1])

    def test_early_initialization_is_jp_linux_only(self):
        text = MAIN.read_text()
        self.assertLess(text.index('std::strcmp(argv[1], "--version")'),
                        text.index("goemon_jp::logging::start_session("))
        self.assertLess(text.index("goemon_jp::logging::start_session("),
                        text.index("preload_executable(preload_context)"))
        self.assertIn('#if defined(__linux__) && defined(GOEMON_JAPAN_NATIVE)', text)
        self.assertIn('version_string = GOEMON_LAUNCHER_VERSION', text)

    def test_only_japanese_linux_binary_links_the_logger(self):
        text = CMAKE.read_text()
        self.assertIn('if(CMAKE_SYSTEM_NAME STREQUAL "Linux")', text)
        self.assertIn('target_sources(Goemon64RecompiledJP PRIVATE', text)
        self.assertIn('src/main/jp_session_logging.cpp', text)


if __name__ == "__main__":
    unittest.main()
