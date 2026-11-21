#!/usr/bin/env python3
"""Fail-closed, C/C++, and previous-revision repair checks for generated source."""
import shutil
import subprocess
import tempfile
from pathlib import Path
from instrument_generated_file22 import SYMBOLS, DECL, OLD_DECL, instrument


def compile_source(c_path, cpp_path):
    for tool in ("clang", "clang++"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} is required by generated-code test")
    subprocess.run([
        "clang", "-std=gnu17", "-Wall", "-Wextra", "-Werror",
        "-Wno-unused-parameter", "-fsyntax-only", str(c_path),
    ], check=True)
    subprocess.run([
        "clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror",
        "-Wno-unused-parameter", "-fsyntax-only", str(cpp_path),
    ], check=True)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        names = sorted(SYMBOLS)
        c_path, cpp_path = root / "funcs_99.c", root / "funcs_100.cpp"
        preamble = (
            "#include <stdint.h>\n"
            "typedef struct recomp_context { int unused; } recomp_context;\n"
            "#define RECOMP_FUNC\n"
        )
        c_path.write_text(preamble + "\n".join(
            f"RECOMP_FUNC void {n}(uint8_t* rdram, recomp_context* ctx) {{ (void)ctx; }}"
            for n in names[:6]
        ))
        cpp_path.write_text(preamble + "\n".join(
            f"void {n}(uint8_t* rdram, recomp_context* ctx) {{ (void)ctx; }}"
            for n in names[6:-1]
        ))
        before = c_path.read_text()
        try:
            instrument(root)
        except RuntimeError as exc:
            assert "Missing" in str(exc)
        else:
            raise AssertionError("Missing file22 symbols must fail closed")
        assert c_path.read_text() == before

        cpp_path.write_text(cpp_path.read_text() + "\n" +
            ("void " + names[-1] + "(uint8_t* rdram, recomp_context* ctx) { }\n") * 2)
        try:
            instrument(root)
        except RuntimeError as exc:
            assert "Duplicate" in str(exc)
        else:
            raise AssertionError("Duplicate file22 symbol must fail closed")
        cpp_path.write_text(preamble + "\n".join(
            f"void {n}(uint8_t* rdram, recomp_context* ctx) {{ (void)ctx; }}"
            for n in names[6:]
        ))

        instrument(root)
        assert c_path.read_text().count("GOEMON_USA_FILE22_ENTRY_PROBE") == 6
        assert cpp_path.read_text().count("GOEMON_USA_FILE22_ENTRY_PROBE") == 6
        assert DECL in c_path.read_text() and DECL in cpp_path.read_text()
        compile_source(c_path, cpp_path)

        unchanged = c_path.read_text()
        instrument(root)
        assert c_path.read_text() == unchanged

        # Simulate the previous failed run that injected C++ syntax in .c.
        c_path.write_text(unchanged.replace(DECL, OLD_DECL, 1))
        instrument(root)
        repaired = c_path.read_text()
        assert repaired == unchanged and OLD_DECL not in repaired
        assert repaired.count("GOEMON_USA_FILE22_ENTRY_PROBE") == 6
        compile_source(c_path, cpp_path)
        print("file22 generated C/C++ syntax, migration, idempotency: PASS")


if __name__ == "__main__":
    main()
