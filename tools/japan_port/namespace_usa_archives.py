#!/usr/bin/env python3
"""Prefix the USA guest and patch symbols so they can coexist with JP."""
import argparse
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nm", required=True)
    parser.add_argument("--objcopy", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("archives", nargs=2, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    symbols = set()
    for archive in args.archives:
        output = subprocess.check_output(
            [args.nm, "--extern-only", "--defined-only", "--format=posix", archive],
            text=True,
        )
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 3 and len(parts[1]) == 1:
                symbols.add(parts[0])
    if not {"recomp_entrypoint", "mm_patches_bin"} <= symbols:
        parser.error("Expected both a USA guest entrypoint and patch archive")

    # Use exactly the same map for both archives, including patch references to
    # USA guest functions. Host symbols undefined by the inputs keep their ABI.
    names = {symbol: "goemon_usa_" + symbol for symbol in sorted(symbols)}
    mapping = args.output / "usa-symbols.txt"
    mapping.write_text("".join(f"{old} {new}\n" for old, new in names.items()))
    for archive in args.archives:
        subprocess.run(
            [args.objcopy, "--redefine-syms", mapping, archive, args.output / archive.name],
            check=True,
        )
    header = args.output / "goemon_usa_symbols.h"
    header.write_text("#pragma once\n" + "".join(
        f"#define {old} {new}\n" for old, new in names.items()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", old)
    ))


if __name__ == "__main__":
    main()
