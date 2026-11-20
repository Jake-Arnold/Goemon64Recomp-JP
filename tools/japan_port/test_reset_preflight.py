#!/usr/bin/env python3
"""Static guardrails for the JP native reset input preflight.

These intentionally test the safety boundary, not an in-process reset:
The input stage must not quit, spawn, or re-exec the native process on F8.
Runtime reset implementation will require additional integration tests.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class ResetInputPreflightTests(unittest.TestCase):
    def test_menu_action_is_exposed_without_n64_button_bit(self) -> None:
        text = source("include/recomp_input.h")
        self.assertIn('DEFINE_INPUT(RESET_GAME, 0, "Reset Game",', text)
        self.assertIn("std::vector<InputField> reset_game;", text)
        self.assertIn("case GameInput::RESET_GAME: return defaults.reset_game;", text)
        self.assertLess(text.index("DEFINE_RECOMP_UI_INPUTS"), text.index("enum class GameInput"))

    def test_default_keyboard_and_controller_bindings(self) -> None:
        text = source("src/game/input.cpp")
        self.assertRegex(text, r"(?s)\.reset_game\s*=\s*\{\s*\{\.input_type\s*=.*?SDL_SCANCODE_F8")
        self.assertIn(".reset_game = {}", text)

    def test_reset_is_edge_triggered_and_menu_safe(self) -> None:
        text = source("src/game/input.cpp")
        preflight = text.split("// Input plumbing only:", 1)[1].split("\nvoid recomp::set_rumble(", 1)[0]
        self.assertIn("reset_held && !reset_was_held", preflight)
        self.assertIn("ultramodern::is_game_started()", preflight)
        self.assertIn("!recomp::game_input_disabled()", preflight)
        self.assertIn("!recomp::all_input_disabled()", preflight)
        self.assertIn("reset_was_held = reset_held", preflight)

    def test_preflight_does_not_stop_or_restart_game(self) -> None:
        text = source("src/game/input.cpp")
        preflight = text.split("// Input plumbing only:", 1)[1].split("\nvoid recomp::set_rumble(", 1)[0]
        for unsafe in ("ultramodern::quit(", "execv(", "execl(", "std::system(", "SDL_Quit(", "abort(", "exit("):
            self.assertNotIn(unsafe, preflight)
        self.assertIn("backend not installed", preflight)

    def test_existing_control_persistence_handles_new_action(self) -> None:
        text = source("src/game/config.cpp")
        self.assertIn("GameInput::RESET_GAME, values.reset_game", text)
        self.assertIn("for (size_t i = 0; i < recomp::get_num_inputs(); i++)", text)
        self.assertIn('config_json["controller"]', text)
        self.assertIn('config_json["keyboard"]', text)

    def test_usa_build_is_not_changed_by_jp_reset(self) -> None:
        header = source("include/recomp_input.h")
        self.assertIn("#if defined(GOEMON_JAPAN_NATIVE) || defined(GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE)\n    #define DEFINE_JP_RESET_INPUT()", header)
        self.assertIn("#else\n    #define DEFINE_JP_RESET_INPUT()\n#endif", header)
        defaults = source("src/game/input.cpp")
        self.assertRegex(defaults, r"(?s)#if defined\(GOEMON_JAPAN_NATIVE\) \|\| defined\(GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE\)\n    \.reset_game")
        config = source("src/game/config.cpp")
        self.assertIn("#if defined(GOEMON_JAPAN_NATIVE) || defined(GOEMON_JP_EXPERIMENTAL_RESET_BACKEND_ACTIVE)\n    assign_mapping_complete(device, recomp::GameInput::RESET_GAME", config)

    def test_version_and_build_fingerprint(self) -> None:
        text = source("src/main/main.cpp")
        self.assertIn('const std::string version_string = GOEMON_LAUNCHER_VERSION;', text)
        self.assertIn('GOEMON_JP_BUILD_COMMIT', text)


if __name__ == "__main__":
    unittest.main()
