# USA native gameplay state / overlay registration request trace

**Status:** optional experimental diagnostics; original-USA fixed-symbol research only. Neither native gameplay verification nor an original-ROM exact recompilation/matching-C proof is included in this change.

## Build and enable

This source is compiled into the unified USA/Japan Linux launcher, but tracing is **disabled by default** and also disabled when Japan is selected. No runtime game-memory write or state modification is performed.

From the repository root, build the existing unified Linux target with the same generated/private game assets used by the previous successful launcher build:

```sh
cmake --build build-generated/launcher-unified --target Goemon64RecompiledJP -j"$(nproc)"
GOEMON_USA_NATIVE_STATE_TRACE=1 ./build-generated/launcher-unified/Goemon64RecompiledJP
```

Select **Mystical Ninja Starring Goemon — USA** and click **Start Game · USA**. The existing session logger records stderr under `~/.local/state/goemon64recomp-jp/logs/`. Search only for the `[Goemon USA native state trace]` prefix. To stop tracing, relaunch without the environment variable. The exact string `1` is required.

## Evidence and data fields

- `type=step_callback_snapshot_changed`: guest RDRAM is accessed synchronously when the patched original-USA game-step function `func_800012FC_1EFC` invokes `recomp_run_ui_callbacks`. This callback runs on the guest execution path and supplies `rdram`; it is **not** proof of a completed guest frame, authenticated guest PC, or frame-atomic capture. `step_call` is only a local counter of validated callback observations. It is **not a frame number**. Only changed tuples are logged.
- `type=overlay_registration_request`: emitted by host `recomp_load_overlays` **before** `load_overlays` is called, which itself precedes the patched MIPS loader's original ROM-byte copy/decompression and relocations. `source_rom`, `guest_destination`, and `source_length` are request arguments. They do **not** prove which original file ID completed, that the data was copied, or that the overlay is executing.
- `outer`, `secondary`, `tertiary`: original USA SYS_W selectors. `room`: intended room-field value at guest `0x800C7AB2`. `selection`: group-local selection at `0x800C7AB4`; this is **not** a scheduler flag/mode.
- A room value of `0` at boot is the game's default/uninitialized field value, **not evidence of room 0 being entered**. A room-field change, including a transient ID, is not evidence of completed scene entry.
- `usa_sys_valid=1` means the read-only N64 system-root sentinel at guest `0x8015C5C8` matched `0x8008CCC0` and the 18-way outer state was in range. It does not authenticate which ROM file the launcher imported. The USA region gate is set by the launcher, separately from this sentinel.
- `elapsed_us` is host monotonic time since USA region was selected, not in-game time. `guest_pc=unavailable` and `guest_frame=unavailable` are intentionally explicit.

No code bytes, full RDRAM, save files, host memory addresses, input events or routes are written into the trace. Log output is bounded to at most 4,096 events per process run; if that bound is reached, further events are silently discarded. The feature does not install guest execution breakpoints or change the original game program.

The normal external `decomp.state_runtime_trace` observer remains useful for longer sessions and independent process-memory confirmation. These native callback observations are better located in the game path, but are **not interchangeable with original MIPS execution proofs**.

## Local ROM-free test

```sh
clang++ -std=c++20 -Wall -Wextra -Wpedantic -Werror \
  -fsanitize=address,undefined -fno-omit-frame-pointer -g \
  src/game/usa_native_state_trace.cpp tools/tests/test_usa_native_state_trace.cpp \
  -o /tmp/goemon_usa_native_state_trace_test
/tmp/goemon_usa_native_state_trace_test
```

The fixture validates big-endian guest reads from word-swapped synthetic RDRAM, USA system sentinel rejection, outer selector bounds, the group-local room selection, disabled-by-default/Japan gating, and read-only instrumentation. This does **not** run the full native recomp or prove the embedded call sites were executed.

## Required next validation

Build the unified launcher and observe the trace prefix during USA startup; confirm no prefix under Japan selection. Compare native `room`/`outer` changes with `goemon-usa-state-overlay-observations-v2` host-poll results. Verify `recomp_run_ui_callbacks` actually fires during each relevant part of startup and confirm the overlay-registration callback arguments against the authenticated original-USA ROM loader. Only instrumentation inside verified loader completion or an actual guest-execution breakpoint can justify a completed overlay-load/guest-PC claim.
