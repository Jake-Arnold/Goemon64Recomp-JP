# Original USA file22: byte image and translated entry research

Status: opt-in original-USA native recomp research only. No ROM bytes,
extracted assembly, generated recompiled sources or toolchain is committed.

## Reference provenance

USA retail ROM SHA-1: df8083a54296b8c151917c5333e1c85f014a2a66.
Independent 32 MiB rommy-decompressed USA ROM SHA-1:
6ea0ed71032ce08fc2745f412d84936382197494.

Original-USA Splat identifies static overlay file 22:
- ROM span 0x0069E080..0x006A9920 (47,264 bytes).
- Original code 0x0069E080..0x0069F400 (4,992 bytes, 12 functions).
- Original link address and observed guest load address 0x801CB460.
- Separate BSS size 0x360 is excluded from the ROM-byte comparison.
- Full payload SHA-256:
  bcd5148e8302d461b7773d03612f35b37437853def0d38e7ece0fbe2f33db839
- Code SHA-256:
  0cec5c523d2b6a7e54ed9bad6db61939fd1497ed4d4a3bffe043e00daf8e987f

## Exact image witnesses

The new file22-only loader callback compares guest RDRAM to a reference
immediately after the original ROM copy/decompress call and BEFORE
overlay_apply_relocations. The existing postprocess callback compares after
relocation returns and trailing zero-fill is completed. The reference file
is the external user-local ./mnsg.z64, authenticated before launch by the
runner against the full 32 MiB SHA-1 and file22 SHA-256 digests. At runtime,
a full-length check and independent FNV64 reference-slice fingerprints
prevent unrelated source files from silently passing. The observer then
compares EVERY byte in the 47,264-byte guest image against the original
natural-order ROM image, undoing x86-64 N64Recomp RDRAM word swapping.

Because the original link address and actual destination are both
0x801CB460 for this static overlay, relocation delta is zero. Exact
reference-image matches before AND after the relocation call prove correct
final bytes for this specific zero-delta load. They do not prove arbitrary
MIPS relocation types, relocation-table integrity at other load addresses,
or changes to guest RAM that happen after these observations.

## Execution-path witness

The source-only Python instrumentation scans local ignored
RecompiledFuncs C++ for precisely 12 original-USA file22 functions,
validates each unique ROM-offset suffix and guest-VRAM mapping, and inserts
one read-only host callback at each translated function's entry.
The code belonging to the original translated function is not replaced.
The host records the original ROM function address, original guest entry,
whether file22 is still active in the shared static-overlay slot, and
whether both byte comparisons passed. This is a direct witness that a
translated original-address function was ENTERED. It is NOT a sampled MIPS
PC register, authenticated guest frame or completed scene transition.
Generated code is deliberately not committed.

A missing function or duplicate function causes the instrumenter to stop
before writing anything. If generated symbols are absent, re-run the trusted
local original-USA N64Recomp generation and retry.

## Run

From the existing local checkout:

    bash tools/usa_file22/rebuild_and_capture_file22.sh \
      "$HOME/Documents/ChatGPT/Goemon64Recomp-JP"

Or use the separate source-only wrapper kit. The runner verifies the
authentic reference, runs sanitizer-backed tests, rebuilds MIPS and native
patches, instruments the ignored original-USA generated C++, builds the
unified launcher, and launches USA tracing. Select USA, play through
startup, and close normally. It writes metadata-only log and JSON files
in the home directory.

The analyzer only accepts live events from the launcher session log.
Synthetic preflight fixture output is not runtime evidence.

Evidence gates:
- pre-relocation full-image exact comparison = PASS;
- post-relocation full-image exact comparison = PASS;
- at least one original-ROM file22 translated function entry with the
  file22 slot active, both image checks passed and USA system validated.

Retain research-only status until these are witnessed in actual gameplay.
No matching-C promotion, full decomp CI or G2 exact-ROM rebuild is claimed.
