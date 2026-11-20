#!/usr/bin/env python3
"""Source/ROM-contract tests for the exact JP Sasuke bomb camera fix."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from . import jp_sasuke_bomb_camera_fix as camera
except ImportError:
    import jp_sasuke_bomb_camera_fix as camera


FUNCTION = camera.FUNCTION_HEADER
NEXT = "\nRECOMP_FUNC void func_801F147C_5AD44C(uint8_t* rdram, recomp_context* ctx) {}\n"
SOURCE = (
    '#include "recomp.h"\n'
    + FUNCTION
    + "\n"
    + "    ctx->r4 = 0X104;\n"
    + "    ctx->r6 = ctx->r16;\n"
    + "    ctx->r7 = 0X43FA0000;\n"
    + "    ctx->r31 = 0X801F12B8;\n"
    + "    ctx->r5 = MEM_W(ctx->r15, 0X2C);\n"
    + "    func_8000F1E0_FDE0(rdram, ctx);\n"
    + "    ctx->r4 = ctx->r17;\n"
    + "}\n"
    + NEXT
)


class SasukeBombCameraTests(unittest.TestCase):
    def test_exact_japanese_rom_revision_and_instruction_sequence(self):
        self.assertEqual(camera.JP_SHA1, "6f8745fecc3a5e7d4918c7a73353eb0726116ded")
        self.assertEqual(camera.ROM_SIZE, 0x2000000)
        self.assertEqual(camera.ROM_EXPECTED[0x5AD280], bytes.fromhex("0C003C78 8DE5002C"))
        self.assertEqual(camera.ROM_EXPECTED[0x5ADE24], bytes.fromhex("3C058021 24A519E0"))
        self.assertEqual(camera.ROM_EXPECTED[0x5D17B4], bytes.fromhex("3C058021 24A519E0"))
        self.assertEqual(len(camera.ROM_EXPECTED[0x5ACF80]), 56)

    def test_requires_exact_rom_sha_even_when_opcode_matches(self):
        fake = bytearray(camera.ROM_SIZE)
        for at, expected in camera.ROM_EXPECTED.items():
            fake[at:at + len(expected)] = expected
        with self.assertRaisesRegex(ValueError, "exact 32 MiB"):
            camera.validate_rom(bytes(fake))

    def test_rejects_malformed_rom_size(self):
        with self.assertRaises(ValueError):
            camera.validate_rom(bytes(100))

    def test_rejects_changed_rom_opcode_after_sha_gate(self):
        # Simulate passing a hash check against one exact revision while a
        # malicious or changed ROM has altered a guarded instruction.
        fake = bytearray(camera.ROM_SIZE)
        for at, expected in camera.ROM_EXPECTED.items():
            fake[at:at + len(expected)] = expected
        fake[0x5AD284] = 0xFF
        class MockDigest:
            def hexdigest(self):
                return camera.JP_SHA1
        with patch.object(camera.hashlib, "sha1", return_value=MockDigest()):
            with self.assertRaisesRegex(ValueError, "0x5AD280"):
                camera.validate_rom(bytes(fake))

    def test_exact_function_and_one_call_only(self):
        patched = camera.inject(SOURCE)
        self.assertIn(camera.MARKER, patched)
        self.assertEqual(patched.count(camera.PATCH_LINE), 1)
        self.assertEqual(patched.count(camera.CALLEE + "(rdram, ctx);"), 1)
        self.assertIn(camera.FUNCTION_HEADER, patched)
        self.assertIn(NEXT, patched)

    def test_preserves_original_delay_slot_but_overwrites_argument_after_it(self):
        patched = camera.inject(SOURCE)
        positions = [
            patched.index("ctx->r5 = MEM_W(ctx->r15, 0X2C);"),
            patched.index("ctx->r5 = (gpr)(int64_t)(int32_t)0x802119E0u;"),
            patched.index("func_8000F1E0_FDE0(rdram, ctx);"),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_inserted_line_keeps_indentation(self):
        patched = camera.inject(SOURCE)
        self.assertIn("    // " + camera.MARKER, patched)
        self.assertIn("    " + camera.PATCH_LINE + "\n    " + camera.CALLEE, patched)

    def test_idempotent_reapply_preserves_hash(self):
        patched = camera.inject(SOURCE)
        self.assertEqual(camera.inject(patched), patched)

    def test_fails_closed_if_camera_delay_load_is_changed(self):
        bad = SOURCE.replace("ctx->r15, 0X2C", "ctx->r11, 0X2C")
        with self.assertRaisesRegex(ValueError, "Cannot verify expected"):
            camera.inject(bad)

    def test_fails_closed_if_call_is_different(self):
        bad = SOURCE.replace(camera.CALLEE, "func_8000F420_10020")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            camera.inject(bad)

    def test_fails_closed_if_camera_call_duplicated(self):
        bad = SOURCE.replace(
            "    func_8000F1E0_FDE0(rdram, ctx);",
            "    func_8000F1E0_FDE0(rdram, ctx);\n"
            "    func_8000F1E0_FDE0(rdram, ctx);",
        )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            camera.inject(bad)

    def test_fails_closed_if_bomb_function_ambiguous(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            camera.inject(SOURCE + FUNCTION + "\n}\n" + NEXT)

    def test_fails_closed_if_function_end_missing(self):
        with self.assertRaisesRegex(ValueError, "Missing end"):
            camera.inject(SOURCE.split(NEXT)[0])

    def test_malformed_existing_marker_rejected(self):
        with self.assertRaisesRegex(ValueError, "Malformed"):
            camera.inject(SOURCE.replace("ctx->r31", "// " + camera.MARKER + "\nctx->r31"))

    def test_requires_generated_recomp_header(self):
        with self.assertRaisesRegex(ValueError, "Not a generated"):
            camera.inject(SOURCE.replace('#include "recomp.h"', ""))

    def test_other_functions_and_usa_target_untouched(self):
        src = (
            '#include "recomp.h"\n'
            "RECOMP_FUNC void func_801EBF48_5A7E58(uint8_t* rdram, recomp_context* ctx) {\n"
            "    func_8000F420_10020(rdram, ctx);\n}\n"
        ) + SOURCE.split('#include "recomp.h"\n', 1)[1]
        got = camera.inject(src)
        self.assertEqual(got.split(camera.FUNCTION_HEADER, 1)[0],
                         src.split(camera.FUNCTION_HEADER, 1)[0])

    def test_fresh_codegen_hook_is_staged_atomically(self):
        prepare = (Path(__file__).parent / "prepare_japan_recomp.py").read_text()
        self.assertIn("from .jp_sasuke_bomb_camera_fix import", prepare)
        self.assertIn("verify_sasuke_rom(rom_data)", prepare)
        self.assertIn('(generated_dir / "funcs_32.c", patch_sasuke)', prepare)
        self.assertIn("prepared[source] = inject(original)", prepare)

    def test_version_isolated_from_usa(self):
        root = Path(__file__).resolve().parents[2]
        main = (root / "src/main/main.cpp").read_text()
        self.assertIn('version_string = GOEMON_LAUNCHER_VERSION', main)


if __name__ == "__main__":
    unittest.main()
