#!/usr/bin/env python3
"""Instrument authenticated original-USA file-22 generated C++ functions.

The ignored RecompiledFuncs tree is a generated, uncommitted local input.
Fail closed if any of the 12 original-address functions are missing,
duplicated, or have a mismatched ROM/VMA relationship. Idempotent.
"""
import argparse
from pathlib import Path
import re

SYMBOLS = {
    "func_801CB460_69E080", "func_801CB4BC_69E0DC",
    "func_801CB53C_69E15C", "func_801CB610_69E230",
    "func_801CB668_69E288", "func_801CB95C_69E57C",
    "func_801CB9CC_69E5EC", "func_801CBAF8_69E718",
    "func_801CBC8C_69E8AC", "func_801CBF5C_69EB7C",
    "func_801CBF98_69EBB8", "func_801CC0C4_69ECE4",
}
DECL = (
    '// GOEMON_USA_FILE22_PROBE_DECLARATION (generated, do not commit)\n'
    '#include <cstdint>\n'
    'extern "C" void goemon_usa_file22_translated_entry_probe('
    'std::uint8_t* rdram, std::uint32_t rom_offset, '
    'std::uint32_t guest_entry);\n'
)
PATTERN = re.compile(
    r'\bvoid\s+(func_([0-9A-Fa-f]{8})_([0-9A-Fa-f]{6,8}))\s*'
    r'\(\s*(?:std::)?uint8_t\s*\*\s*rdram\s*,\s*recomp_context\s*\*\s*ctx\s*\)\s*\{',
    re.MULTILINE,
)

def instrument(source_dir: Path):
    if not source_dir.is_dir():
        raise RuntimeError(f"Missing generated original-USA source: {source_dir}")
    paths = sorted(p for p in source_dir.rglob("*")
                   if p.suffix in {".c", ".cc", ".cpp", ".cxx"})
    found = {}
    staged = {}
    for path in paths:
        data = path.read_text(encoding="utf-8")
        matches = [m for m in PATTERN.finditer(data) if m.group(1) in SYMBOLS]
        if not matches:
            continue
        for match in matches:
            name = match.group(1)
            if name in found:
                raise RuntimeError(f"Duplicate original-USA function {name}: {found[name]} and {path}")
            found[name] = str(path)
            rom, vram = int(match.group(3), 16), int(match.group(2), 16)
            if not (0x69E080 <= rom < 0x69F400 and
                    vram == 0x801CB460 + rom - 0x69E080):
                raise RuntimeError(f"File-22 layout mismatch for {name}")
        staged[path] = (data, matches)
    missing = sorted(SYMBOLS - found.keys())
    if missing:
        raise RuntimeError("Missing original-USA generated symbols: " + ", ".join(missing))
    changed = []
    for path, (data, matches) in staged.items():
        replacements = 0
        for match in reversed(matches):
            name = match.group(1)
            if f"GOEMON_USA_FILE22_ENTRY_PROBE {name}" in data:
                continue
            fragment = (
                f"\n    // GOEMON_USA_FILE22_ENTRY_PROBE {name}\n"
                f"    goemon_usa_file22_translated_entry_probe(rdram, "
                f"0x{int(match.group(3), 16):08X}u, "
                f"0x{int(match.group(2), 16):08X}u);"
            )
            data = data[:match.end()] + fragment + data[match.end():]
            replacements += 1
        if replacements:
            if "GOEMON_USA_FILE22_PROBE_DECLARATION" not in data:
                data = DECL + "\n" + data
            changed.append((path, data))
    # No writes until all required symbols and source-range checks pass.
    for path, data in changed:
        path.write_text(data, encoding="utf-8")
    print(f"Validated {len(found)} exact file-22 symbols across {len(staged)} generated file(s); updated {len(changed)}.")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path, help="Repository RecompiledFuncs directory")
    args = parser.parse_args()
    try:
        instrument(args.source_dir)
    except RuntimeError as exc:
        parser.exit(1, str(exc) + "\n")

if __name__ == "__main__":
    main()
