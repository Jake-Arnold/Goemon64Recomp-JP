# Integrated Japanese F8 reset — first end-to-end experimental branch

## Purpose

This is **not** Stage19 and not another read-only diagnostic. The default-off
`GOEMON_JP_INTEGRATED_F8_RESET_EXPERIMENTAL=ON` option attempts to perform a
**real in-process restart** of the Japanese recompiled guest while retaining
the **same Linux process and SDL_Window**. It is intentionally Linux-only
until RT64 window handle behaviour has been verified elsewhere.

The exact feature baseline is the Ubuntu-verified Stage18 Japanese branch:
Stage17 timer producer exit `timer_safe=1`, Stage12 normal exit
`known_joined=1`, Stage15 SDL closure `confirmed=1`, Stage16 no-dirty-save
`mutated=0 finalized=1`. These are genuine baseline results, but none
individually proved restarting a live guest.

## How the integrated backend works

The experimental F8 handler asks `recomp::jp_request_integrated_reset()`
to retain the current game ID and to end the **current guest-runner cycle**
via the already-tested `exited` flag. It **does not call**
`ultramodern::quit()`, which clears the game identity before all guest
threads have joined. The outer `recomp::start()` function and SDL window
remain alive; the process is not replaced, forked or restarted.

The current cycle goes through the proven ordered shutdown path:
guest entry joins, cooperative OSThread stop and join, timer producers close
and worker joins, native VI/GFX/SP workers join, abandoned graphics and
config actions drain, and Stage16 save mutations are finalized. Stage12's
combined `known_joined` and Stage17's timer proof are required.

An additional **external-message integrated runtime component** closes the producer
gate of the external N64 message queue, drains queued records referencing
old `OSMesgQueue` guest-memory addresses, and rearms the gate only after
all old workers have joined. The old guest RDRAM is released only if the
combined shutdown and message queue drain succeed. If any fence fails,
no new memory is allocated or guest entrypoint started.

Once the previous RDRAM mapping has actually been released, the reset
owner attempts all of these, in order:

1. Guest OSThread creation stop flag resets, only after joined registry is empty.
2. VI, graphics task and event message state resets; Stage14's config
   action publisher gate and Stage10's closed flag are reopened.
3. Stage17 timer producer gate is reopened, its worker-join markers are
   reset, and Stage18's clock origin/guest time are rebased atomically to
   the next generation.
4. Stage16's successful save-finalize state is carried through and the
   next saving worker's buffer epoch/notification semaphores reset.
5. The existing SDL audio device is paused, its queued old-generation
   audio cleared, and playback resumed. It is **not** closed or replaced.
6. The new iteration allocates fresh zeroed RDRAM, starts new native
   VI/GFX/SP, timer and cleaner workers and a game-entry thread; it
   automatically re-selects the saved Japanese game and calls the normal
   initialization/overlay and entrypoint path.

The **same SDL_Window pointer** is returned from `create_window()`
during reinitialization. RT64's GPU render context is currently shut down
and reconstructed for the same SDL window. This is the component most
likely to reveal previously untested lifetime assumptions.

A second F8 reset should repeat this and reach generation 3. If a barrier,
worker join, queue drain, save finalization, or gate rearm fails, the
experiment fails **closed**, logs `result=ABORT`, and performs final
process cleanup instead of returning a false-positive success.

## What is implemented and what is not yet proven

**Implemented on branch** `feature/japan-integrated-f8-reset-experimental`:
F8-to-reset ownership, same-process generation loop, game ID preservation,
guest/VI/event/timer/save rearm, an external-message publication barrier,
old RDRAM replacement only after proofs, SDL window/audio reuse, and
host-only double-generation producer/clock tests.

**NOT VERIFIED:** production CMake compile, actual RT64 reinitialization,
live title screen after first or second F8, save write path with real
mutations, absence of use-after-free under sanitizers. No claim that the
feature is working until those pass on native Ubuntu.

All previous Stage1–18 opt-in switches remain in the existing build
cache. This branch does not touch USA code paths, original ROMs, or pinned
runtime submodules.

## First live restart failure and corrected causes (17 Nov 2026)

The first Ubuntu **full native build and link passed** on
`84d101f424`. F8 then completed guest/native teardown and RDRAM
replacement, reported `generation=2 result=REARMED`, recreated
RT64 (GPU enumerated again), loaded RmlUi fonts and initialized the
recompiled heap a second time. **The screen remained black** rather
than showing the title; the first reset was not an end-to-end pass.

Review of the exact pinned C++ source identified two concrete
generation-local defects; both are now corrected in the **same**
experimental branch:

1. `recomp::load_stored_rom()` mutates the registered
   `GameEntry.has_compressed_code` to `false` on initial decompression.
   Since this flag persists in host memory, a second boot previously
   loaded the original *compressed* stored Japanese ROM directly
   without its `decompress_mnsg_japan` routine. The integrated
   overlay now restores `has_compressed_code=true` for
   `game_id==u8"mnsg.jp"` before **every** stored-ROM load. The
   runtime still resets the flag to false after actual decompression
   for compatibility with the existing mod-loading path. On replay,
   expect `[Goemon JP integrated reset] restoring compressed game ROM
   before entrypoint replay.`.
2. `ultramodern/src/threadqueue.cpp` keeps a native
   `running_queue_impl` pointer outside RDRAM; old guest OSThread
   pointers can therefore survive `munmap` and zero-filled
   reallocation. A seventh generated overlay, now
   `threadqueue_integrated.cpp`, clears that pointer **only after**
   Stage12's complete guest/native join proof and before the old
   RDRAM can be unmapped. Failed proof blocks unmap and restart.
   Expect `[Goemon JP integrated reset]
   native_running_queue_cleared=1 stale_head=N`.

Both corrections preserve normal USA builds and the default-off
experimental flag. The second-generation black screen must be
retested in Ubuntu; no claim of title-screen recovery until observed.
The new source test checks both pinned-code modifications, and CI
compiles all **seven** generated C++ translation units.

## Live Ubuntu breakthrough — three consecutive F8 restarts (17 Nov 2026)

**Observed:** User ran native JP build `v0.2.25-dev`, commit
`d6e0deff92`, session log
`session-20261117-181400-pid54625-cVwAXE.log`. User reports
the game now works correctly after reset and the external RAM toolkit
remains connected. This is **the first confirmed successful live
multi-generation in-process reset** (previous generation-2 black screen
is resolved on this build).

The same log shows the complete sequence:

| Guest generation | F8 old→new | Old OSThread cleanup (cumulative) | Graphics jobs completed (cumulative) | New boot |
|---|---|---|---|---|
| 1 → 2 | `result=REARMED` | created=5 finished=5 joined=5 | 312/312 | RT64 GPU, fonts, ROM decompress, heap |
| 2 → 3 | `result=REARMED` | created=10 finished=10 joined=10 | 693/693 | RT64 GPU, fonts, ROM decompress, heap |
| 3 → 4 | `result=REARMED` | created=15 finished=15 joined=15 | 1085/1085 | RT64 GPU, fonts, ROM decompress, heap |

All three cycles report Stage12 `known_joined=1`, Stage17
`timer_safe=1`, joined VI/GFX/SP workers, `audio_reused=1
queue_cleared=1`, `external_queue_drained=1`, and no
`result=ABORT` or crash marker. The second shutdown explicitly
reported `native_running_queue_cleared=1 stale_head=1`, demonstrating
that the new after-join scheduler-head clearing had an actual stale
pointer to remove. JP ROM restoration was logged on generations
2, 3 and 4 before each heap initialization.

**Process identity:** the logger started with PID `54625` and
recorded all three resets within that single session. The
`pid=unchanged window=preserved` lines report the intent of the
in-process owner; the continuous same-PID session and preserved
window ownership corroborate it. There is no evidence of `exec`
or a second process launch.

**External toolkit — live read test confirmed by user:** after F8,
the toolkit's displayed **health value drops to 0 during guest
initialization**, then returns to its correct, nonzero gameplay value
once the restarted game loads. The toolkit does this **without
reconnecting**. This is evidence of changing live game state across
a successful in-process reset, not merely retaining the original PID
or displaying a frozen snapshot. Since `munmap` and `mmap`
replace guest RDRAM, the observation does **not** by itself prove
that the host RDRAM base address remains identical, nor has
**post-reset write functionality** been independently verified.
Read-path continuity is observed; pointer identity and write-path
correctness remain distinct checks.

**Still unverified:** the observed save-finalize path was always
`mutated=0 final_attempt=0`; no dirty-save persistence/durability
test has been performed. Timer publishers showed
`accepted=0 rejected=0`; no real timer API publication path was
exercised. The log alone does not establish extended gameplay
stability, audio playback quality, or repeated debugger memory
reads/writes (user says gameplay/toolkit appear correct). Do not
claim leak-free or sanitizer-proven graphics lifecycle.

**Disposition:** the feature has achieved its primary live
multi-reset functionality on Ubuntu. Keep default OFF and PR
draft until explicit save persistence, toolkit live-memory
rebinding and longer-session regression checks pass. No additional
diagnostic-only staging is needed.

## Ubuntu test (disposable saves only)

Backup saves first. This feature is **experimental** and may crash.
Use a disposable Japanese profile, not a valuable existing save.

```bash
cd ~/Goemon64Recomp-JP

git fetch origin feature/japan-integrated-f8-reset-experimental
git switch feature/japan-integrated-f8-reset-experimental 2>/dev/null ||
  git switch --track origin/feature/japan-integrated-f8-reset-experimental
git pull --ff-only

cmake -S . -B build-generated/japan-native-game \
  -DGOEMON_JP_INTEGRATED_F8_RESET_EXPERIMENTAL=ON

python3 tools/japan_port/test_integrated_reset_experiment.py

cmake --build build-generated/japan-native-game \
  --target Goemon64RecompiledJP --parallel 4

~/Desktop/rundecomp.sh
```

The Stage1–18 cache should still be enabled. The experimental option is
default OFF so a clean checkout does not enable this without explicit opt-in.

### Live acceptance

- Start normal Japanese gameplay, let it stabilize, and press/release F8.
- The expected new F8 message is
  `[Goemon JP integrated reset] request=1 generation=1 stopping=1`.
  Previous Stage13 `backend=0` output is deliberately omitted in
  experimental mode.
- Old guest and native workers should join. Stage12 must report
  `known_joined=1` and Stage17 `timer_safe=1`.
- The external queue must report `external_queue_drained=1`.
- Next, `[Goemon JP integrated reset] generation=2 result=REARMED`,
  `window=preserved next_boot=1`, and the standard recomp-heap initialization
  line should appear **again**. The existing `recomp::start()` instance
  retains its window handle outside the guest-generation loop, so
  `create_window()` is not normally called a second time. The original SDL window must not be
  destroyed and the PID must remain unchanged.
- Gameplay should return to the title screen with working graphics,
  controls and audio. Press/release F8 **again**, repeating generation
  2→3, same PID and window, no hang/crash/save loss.
- On any `result=ABORT`, unexpected window recreation, freeze,
  launcher death, missing audio or corrupted save, classify the
  end-to-end smoke as **FAILED** and keep the feature opt-in. The
  game may exit if the reset fails its safety checks.

### Acceptance criteria

`PASS` requires two consecutive F8 restarts from gameplay back to
the Japanese title, same process ID, same SDL window pointer, graphics
and audio restored, no unresolved old guest worker, no stale external
message, unchanged persistent save when relaunched, and clean normal
exit. A source-only contract/host-unit pass is **not sufficient**.

## Rollback

```bash
cmake -S . -B build-generated/japan-native-game \
  -DGOEMON_JP_INTEGRATED_F8_RESET_EXPERIMENTAL=OFF
cmake --build build-generated/japan-native-game \
  --target Goemon64RecompiledJP --parallel 4
```

This retains the previously Ubuntu-verified Stage18 F8 diagnostics.
