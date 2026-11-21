#!/usr/bin/env python3
import tempfile
from pathlib import Path
from instrument_generated_file22 import SYMBOLS, instrument

def main():
    with tempfile.TemporaryDirectory() as temp:
        src = Path(temp)
        names = sorted(SYMBOLS)
        a, b = src / "a.cpp", src / "b.cpp"
        a.write_text("\n".join(
            f"RECOMP_FUNC void {n}(uint8_t* rdram, recomp_context* ctx) {{ }}" for n in names[:6]))
        b.write_text("\n".join(
            f"void {n}(uint8_t* rdram, recomp_context* ctx) {{ }}" for n in names[6:-1]))
        before = a.read_text()
        try:
            instrument(src)
        except RuntimeError as exc:
            assert "Missing" in str(exc)
        else:
            raise AssertionError("Partial input should fail closed")
        assert a.read_text() == before
        b.write_text(b.read_text() + "\n" +
                     ("void " + names[-1] + "(uint8_t* rdram, recomp_context* ctx) { }\n") * 2)
        try:
            instrument(src)
        except RuntimeError as exc:
            assert "Duplicate" in str(exc)
        else:
            raise AssertionError("Duplicate function should fail closed")
        b.write_text("\n".join(
            f"void {n}(uint8_t* rdram, recomp_context* ctx) {{ }}" for n in names[6:]))
        instrument(src)
        assert a.read_text().count("GOEMON_USA_FILE22_ENTRY_PROBE") == 6
        assert b.read_text().count("GOEMON_USA_FILE22_ENTRY_PROBE") == 6
        after = a.read_text()
        instrument(src)
        assert a.read_text() == after
        print("generated file22 fail-closed/idempotency tests: PASS")

if __name__ == "__main__":
    main()
