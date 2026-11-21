#!/usr/bin/env bash
# Source-only USA file22 witness builder; never commit generated game code.
set -euo pipefail
ROOT="${1:-$HOME/Documents/ChatGPT/Goemon64Recomp-JP}"
BRANCH="research/usa-native-guest-callback-trace-20261121"
BUILD="build-generated/usa-native-trace-configured"
EXPECTED_ROM_SHA="6ea0ed71032ce08fc2745f412d84936382197494"
cd "$ROOT"
[[ "$(git rev-parse --show-toplevel)" == "$PWD" ]] || { echo 'Not a repository root' >&2; exit 1; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || {
    echo 'Tracked local modifications present; refusing to overwrite them' >&2; exit 1;
}
git fetch origin "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git switch "$BRANCH"
else
    git switch -c "$BRANCH" "origin/$BRANCH"
fi
git merge --ff-only "origin/$BRANCH"
grep -q 'recomp_file22_copy_complete' patches/required.c
grep -q 'recomp_file22_copy_complete = 0x8F000064' patches/syms.ld
[[ -f src/game/usa_file22_reference.hpp && -f tools/usa_file22/instrument_generated_file22.py ]]
if [[ ! -e mnsg.z64 ]]; then
    if [[ -L mnsg.z64 ]]; then
        echo 'Dangling mnsg.z64 symlink; will not replace automatically' >&2; exit 1
    fi
    OLD="$HOME/Documents/ChatGPT/Goemon64Recomp-v0.2.25-rendering/mnsg.z64"
    [[ -f "$OLD" ]] || {
        echo "Missing 32 MiB decompressed USA ROM: $PWD/mnsg.z64 and $OLD" >&2; exit 1;
    }
    echo "$EXPECTED_ROM_SHA  $OLD" | sha1sum -c -
    ln -s "$OLD" mnsg.z64
fi
echo "$EXPECTED_ROM_SHA  mnsg.z64" | sha1sum -c -
python3 - <<'PY'
from hashlib import sha256
from pathlib import Path
r = Path("mnsg.z64").read_bytes()
checks = [
    ("file22-full", r[0x69E080:0x6A9920],
     "bcd5148e8302d461b7773d03612f35b37437853def0d38e7ece0fbe2f33db839"),
    ("file22-code", r[0x69E080:0x69F400],
     "0cec5c523d2b6a7e54ed9bad6db61939fd1497ed4d4a3bffe043e00daf8e987f"),
]
for name, data, expected in checks:
    actual = sha256(data).hexdigest()
    if actual != expected:
        raise SystemExit(f"{name} reference SHA-256 mismatch: {actual}")
    print(f"{name} independently derived SHA-256: {actual} PASS")
PY
[[ -x ./N64Recomp && -s rsp/aspMain.cpp && -f "$BUILD/CMakeCache.txt" ]] || {
    echo 'Missing local N64Recomp, original-USA RSP C, or configured unified build cache' >&2; exit 1;
}
python3 tools/usa_file22/test_instrument_generated_file22.py
python3 tools/usa_file22/test_analyze_file22_evidence.py
clang++ -std=c++20 -Wall -Wextra -Wpedantic -Werror \
    -fsanitize=address,undefined -fno-omit-frame-pointer -g \
    src/game/usa_native_state_trace.cpp tools/tests/test_usa_native_state_trace.cpp \
    -o /tmp/goemon-usa-native-file22-trace-test
/tmp/goemon-usa-native-file22-trace-test
clang++ -std=c++20 -Wall -Wextra -Wpedantic -Werror \
    -fsanitize=address,undefined -fno-omit-frame-pointer -g \
    tools/tests/test_usa_file22_reference.cpp \
    -o /tmp/goemon-usa-file22-reference-test
/tmp/goemon-usa-file22-reference-test mnsg.z64
make -C patches CC=clang LD=ld.lld -j4
./N64Recomp patches.toml
python3 tools/usa_file22/instrument_generated_file22.py RecompiledFuncs
cmake --build "$BUILD" --target Goemon64RecompiledJP -j4
BIN="$PWD/$BUILD/Goemon64RecompiledJP"
[[ -x "$BIN" ]] || { echo 'No built launcher' >&2; exit 1; }
printf '\nSelect original USA, start the game, play past startup, then close normally.\n'
GOEMON_USA_NATIVE_STATE_TRACE=1 "$BIN"
shopt -s nullglob
logs=("$HOME"/.local/state/goemon64recomp-jp/logs/session-*.log)
(( ${#logs[@]} > 0 )) || { echo 'No launcher session log found' >&2; exit 1; }
LOG="$(ls -t -- "${logs[@]}" | head -1)"
OUT="$HOME/goemon-usa-file22-proof-$(date +%Y%m%d-%H%M%S)"
grep -F '[Goemon USA native state trace]' "$LOG" > "$OUT.log" || {
    echo 'No USA trace in last session; verify selected region and process log' >&2; exit 1;
}
python3 tools/usa_file22/analyze_file22_evidence.py "$OUT.log" --json "$OUT.json"
printf 'Evidence files: %s.log / %s.json\n' "$OUT" "$OUT"
