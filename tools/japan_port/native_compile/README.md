# Isolated native compilation of Japanese generated functions

This is an **object-library compile gate**, not a runnable Goemon PC port.
It does not modify the USA game, launcher, graphics runtime or patch build.

## Prerequisites

- Locally generated, verified Japanese `RecompiledFuncsJP/` output from pinned `N64Recomp`
- Pinned `N64Recomp/include/recomp.h` matching the host compiler revision
- The fork's `include/trace.h`
- Linux GCC or Clang, CMake 3.20+, Ninja or Make

The host compiler workflow `japan-port-recompilers.yml` now provides the pinned API header with the matching Linux compiler binary. It never includes ROMs or generated game C.

## Compile the private code to a static library

From the repository root:

```bash
cmake -S tools/japan_port/native_compile -B build-generated/japan-native-lib \
  -G Ninja \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_CXX_COMPILER=clang++ \
  -DCMAKE_BUILD_TYPE=Debug \
  -DGOEMON_JAPAN_GENERATED=/absolute/private/path/RecompiledFuncsJP \
  -DGOEMON_N64RECOMP_INCLUDE=/absolute/path/pinned/N64Recomp/include \
  -DGOEMON_PORT_INCLUDE="$PWD/include"

cmake --build build-generated/japan-native-lib \
  --target GoemonRecompiledJapan -j4
```

This compiles the local `funcs_*.c` and `lookup.cpp` into `libGoemonRecompiledJapan.a`. It does **not** compile or link `recomp_overlays.inl` into a PC runtime yet.

## Probe a guest CPU checkpoint

Add `-DGOEMON_JP_CHECKPOINT_PROBE_FUNCTION=func_80000450_1050` to the isolated CMake configuration to emit an experimental copy of that generated function. The original function stays registered with the runtime. A host harness can call the probe declared in `guest_checkpoint_probe.h` and copy a checkpoint after a call returns. It can then supply that checkpoint, a fresh CPU context, and the corresponding RDRAM contents to resume after the call.

Use `GOEMON_JP_CHECKPOINT_PROBE_CALLEES` to name additional directly called functions as a CMake list. The probe can resume the innermost captured frame and unwind back through its instrumented callers. Checkpoints are in memory and tied to the selected generated bodies. Indirect calls run normally and only expose a point after they return. Functions with extra C locals, including jump-table temporaries, are rejected. Capturing a full game state still requires broader code generation coverage, continuations for suspended native calls, and a consistent snapshot across workers.

Set `GOEMON_JP_CHECKPOINT_PROBE_MESSAGES=ON` to instrument direct calls to the integrated runtime's send, jam, and receive functions. Once a caller is parked, the runtime can copy its saved CPU frames together with the pending queue operation or the result of an operation that already completed. After the restored scheduler selects the thread, the probe finishes the operation if necessary, restores the return register, and resumes through the saved callers.

For the live Japanese preview, configure the archive with `GOEMON_JP_LIVE_THREAD_CHECKPOINTS=ON`. This instruments MAIN, GAME, SCHED, AUDIO, and IDLE in the pinned generated code. F5 reports live thread coverage and F7 reports restore availability for the selected slot. The individual probe options above can be used in a separate build directory.

IDLE publishes its checkpoint while waiting for an external message. It clears that record before dequeuing or delivering a message. A restored IDLE thread resumes the wait loop after setup. The scheduler fixture restores IDLE from copied RAM and verifies that a native producer can wake it and deliver a message to a blocked guest.

The external-message queue can copy its pending records in FIFO order and restore them into an empty queue. It rejects a capture while delivery holds a message or a batch outside the queue. F6 also tracks acknowledgements at the VI, RSP, graphics, and timer dispatch boundaries. The F5 log reports those acknowledgements and whether the external queue could be copied.

At its pause boundary the timer worker publishes its active guest timer addresses. The timer snapshot also keeps pending add and remove commands in order and saves logical clock values. Deadlines, intervals, and message targets stay in the RAM image. The test rearms the old timer generation, restores against copied RAM, and checks delivery order and cancellation after the new worker starts.

The video snapshot keeps both VI buffers, mode addresses, retrace phase, the scanout and presented registers, and SP, DP, AI, and SI message targets. Cold startup rebinds guest modes to the restored RAM and queues the saved presentation before retraces resume. The runtime fixture checks the VI handoff with a test renderer. RT64 capture waits for an idle graphics dispatcher and checks the completed workload and presentation IDs before copying the last presentation’s native pixels and registers. Early presentation can use a different buffer from the current VI registers. The restore path copies that saved scanout into fresh RAM for the renderer’s first frame. Textures, depth targets, and other internal RT64 state are outside this capture.

The audio snapshot preserves the guest AI sample rate, resampler history, and unread SDL playback samples. It is available while the output device is paused and native audio callbacks are idle. Restore replaces the old SDL queue, initializes the saved conversion settings, and holds playback for the restore owner. SDL samples already sent to the operating system audio driver are outside this queue.

A game-wide save state still needs a coordinator to hold those boundaries while capturing RAM, together with the device state owned by the workers and the renderer.

## Verified local build, 15 November 2026

With the Japanese ROM SHA-1 `6f8745fecc3a5e7d4918c7a73353eb0726116ded`, the pinned x86-64 `N64Recomp` emitted 145 C source units. The isolated build passed CMake configuration, Clang 17 compilation and static archiving with **exit code 0** (33 MiB library, debug/unoptimised build).

The generated C referenced **15 host-runtime functions** not declared by `funcs.h`; they are listed in `runtime_externs.h` and are provided as compile-time **prototypes only**. The original PC port defines some, including `osPiWriteIo_recomp`, in `src/game/unknown_symbols.cpp`. Missing implementations are **not** fabricated or stubbed out by this target.

An archive-wide `nm -g` audit found **82 unique unresolved external symbols** after removing functions resolved inside the archive: 71 `*_recomp` host-runtime functions and 11 dispatch/math/compiler/runtime symbols. The majority are expected native runtime imports, but all must be resolved or deliberately implemented before **linking an executable**. The Japanese ROM handling, graphics, input, sound, overlays, UI, saves, native patches and boot/gameplay remain to be integrated and tested.

Do not commit the compiled archive, `funcs_*.c`, generated headers or any ROM/asset bytes.

## Runtime compatibility audit: memory mapping and I/O

The pinned `klorfmorf/N64ModernRuntime` implementation in `librecomp/src/ultra_stubs.cpp` defines both `osMapTLB_recomp` and `osUnmapTLBAll_recomp`, but they are currently **no-op TODO stubs**. The generated Japanese functions `func_80001E50_2A50` and `func_80001F88_2B88` call them respectively. These routines belong to the game's data-loading code, and `wave_uty` contains a reference to `func_80001E50_2A50`. Actual runtime execution and the importance of the TLB mappings have not been established; the existing USA port may be able to execute the analogous paths without functional TLB emulation.

The existing Goemon PC code also defines `osPiWriteIo_recomp` as an empty project-specific stub in `src/game/unknown_symbols.cpp`. Five Japanese call sites were found in `func_800017B0_23B0` and `func_800019E0_25E0`. These imports can link, but their no-op behaviour must be verified against Japanese runtime execution rather than presumed correct.

**These are runtime validation risks, not native C compilation errors.** The static archive compiled without providing fabricated implementations. No Japanese gameplay success is claimed.
