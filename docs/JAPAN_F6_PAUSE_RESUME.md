# Japanese native F6 pause/resume — experimental

In the unified launcher, **Controls → Pause / Resume** is shared by USA and
Japan. F6 is the default keyboard binding. It can be changed to another key
or a controller button. The host handles the binding while guest input is
paused, so the same binding also resumes play.

## Scope and controls

This branch adds an **in-process F6 pause toggle** to the Japanese
native recompilation. This is **not F8 reset** and **not a save state**:
the guest's current RDRAM and OSThread execution remain allocated
and the same SDL/RT64 window and PID remain alive.

**F6:** pause; press F6 again to resume. The SDL window title adds
`[PAUSED — F6]`, and the existing SDL audio device is paused without
clearing its queued samples. The window remains responsive to SDL
events. **F8 during a pause** explicitly releases the paused worker
gates and audio before invoking the previously Ubuntu-verified
integrated reset. Normal PC quit likewise releases worker gates first.

The feature flag is default OFF:

```text
GOEMON_JP_NATIVE_F6_PAUSE=ON
```

It requires `GOEMON_JP_INTEGRATED_F8_RESET_EXPERIMENTAL=ON`
and the existing JP Stage1–18 prerequisites. USA does not install
the F6 handler; ROMs and the pinned N64ModernRuntime stay unchanged.

## First Ubuntu pause test and deadlock fix — 17 November 2026

The first native test showed `[Goemon JP pause] state=PAUSED
generation=2 ticks=417972750 guest_time=417972750`. Gameplay
froze correctly, but pressing F6 again did not resume it.

**Cause:** the initial F6 toggle was inside `recomp::poll_inputs()`.
That callback depends on guest VI scheduling and stops being invoked
once the cooperative pause parks the VI worker. The host still has an
independent SDL event pump in `recomp::start()`, but the second F6
could not reach `poll_inputs()`. This was a **control-path
deadlock**, not a timer/worker join failure.

**Fix:** F6 keydown edges now toggle pause directly from
`sdl_event_filter()`, which is serviced by the SDL event loop
independently of guest VI and OSThread dispatch. The old
`poll_inputs()` F6 handler is removed. Held/repeated F6 keydown
events are ignored; unpause remains available even if game input
is disabled while paused. F8 pressed while paused also uses the
host SDL event pump, releases the pause and audio gates, and then
requests the existing integrated reset. Normal gameplay F8
bindings continue through their original input path.

**Validation:** The updated host/CI contracts explicitly prohibit
`SDL_SCANCODE_F6` keyboard polling for pause, require the direct
SDL keydown path, and verify a paused F8 reset path. This
does not establish live Ubuntu resume success until a new F6
→ resume → repeat gameplay observation.

**CI maintenance:** The legacy metadata suite now fetches the
pinned runtime, updates the JP version expectation and tests all
five mandatory ROM hook validators using synthetic fixtures.
The Stage16 snapshot test distinguishes the local epoch variable
declaration from the *actual locked assignment*. The heavyweight
USA cross-platform matrix runs on `dev`/`main` integration PRs,
rather than every experimental JP feature-on-feature PR. The
dedicated F6 workflow runs once per PR revision, not once each
for push and PR. No old failed history is erased.

## Confirmed Ubuntu F6 gameplay resume — 17 November 2026

**LIVE SUCCESS:** The user reports pause/resume now works perfectly
after the SDL-event-loop hotkey fix on `20ad0b39ab85`. Their native
JP session contains **12 consecutive `PAUSED → RUNNING` F6 pairs**
within guest **generation 2**, after a verified
`generation=2 result=REARMED pid=unchanged window=preserved
next_boot=1` reset and the repeated Japanese ROM decompression
and heap initialization.

The same game generation is recorded for every toggle;
**no extra process launch, RDRAM reallocation, or additional guest
generation** is evidenced between the reported pauses. For all 12
pairs, the running timestamp is only **188–516 N64 ticks** after the
paused timestamp, showing that the guest logical time is not
advancing by the whole real-world pause interval. User reports
normal gameplay resumes correctly on each F6 press. This
validates the functional F6 pause/resume path after an F8 reset,
including repeatability.

**Not yet separately demonstrated by this excerpt:** full native
exit while F6-paused; F8 pressed *while still paused* (rather than
F6 used after an ordinary F8); exact audio continuity and real
guest timer delivery after long pauses; proof that every native
instruction path is quiescent for arbitrary save-state capture.
Keep the feature experimental and PR draft until those distinct
cases are checked. Do not conflate source/CI tests with live
save-state readiness.

**Relevant CI:** All checks passed on `20ad0b39ab85`: F6
contract run `44158323044`, F8 contract run `44158323081`,
and Japan metadata run `44158323027` (402 tests, 2 skipped).
No cross-platform integration build is claimed.

## What is frozen

- N64 counter and guest time share a synchronized paused clock. While
  paused, both return their saved logical timestamps; on resume,
  the timer origin shifts to remove the time spent paused. There
  should be **no VI/timer catch-up burst**.
- The native VI worker pauses before a new frame and after its
  host sleep. This prevents VI/AI notifications and screen updates
  while paused.
- Graphics action and RSP task dispatchers park at their safe
  boundaries, preserving the last completed image.
- The timer worker checks pause state before dispatching and
  revalidates the N64 timestamp after resume to avoid prematurely
  firing a timer whose host wall-clock timeout occurred during pause.
- Guest OSThread scheduler wakeups and external-message delivery
  park at existing shutdown-safe checkpoints; pending messages
  remain queued rather than being dropped.
- The SDL queued audio device is paused, then resumed.

**Limitation:** this is a *cooperative* host-runtime pause. It does
not forcibly suspend arbitrary native CPU execution or inspect/capture
host C++ stacks. A CPU-bound recompiled guest function that never
calls a host scheduling/timer/message API is not preempted in this
first implementation. Do not describe the current mechanism as
a quiescent arbitrary-instruction snapshot barrier suitable for
full save states. Implementing that requires explicit execution
checkpoints and verified barrier acknowledgements for **all**
guest and native workers.

## Ubuntu build and test

Use a disposable save profile while testing this stacked
experimental branch:

```bash
cd ~/Goemon64Recomp-JP
git fetch origin feature/japan-native-f6-pause-resume
git switch feature/japan-native-f6-pause-resume 2>/dev/null ||
  git switch --track origin/feature/japan-native-f6-pause-resume
git pull --ff-only

python3 tools/japan_port/test_integrated_reset_experiment.py -v
python3 tools/japan_port/test_native_pause_resume.py -v
cmake -S . -B build-generated/japan-native-game \
  -DGOEMON_JP_INTEGRATED_F8_RESET_EXPERIMENTAL=ON \
  -DGOEMON_JP_NATIVE_F6_PAUSE=ON &&
cmake --build build-generated/japan-native-game \
  --target Goemon64RecompiledJP --parallel 4 &&
~/Desktop/rundecomp.sh
```

Log lines on toggles:

```text
[Goemon JP pause] state=PAUSED generation=1 ticks=N guest_time=N pid=unchanged window=preserved; F6 toggles.
[Goemon JP pause] state=RUNNING generation=1 ticks=N guest_time=N pid=unchanged window=preserved; F6 toggles.
```

### Live acceptance

In active Japanese gameplay, trigger F6, confirm gameplay animation,
position/health/timers and music freeze while the SDL window remains
responsive; leave it paused for 10–30 seconds and confirm that F6
resumes normal graphics, controls, audio and game timing without
accumulated rapid-frame catch-up. Repeat at least ten cycles.

Also test normal window close while paused, plus **F8 while paused**
followed by the returning title and another F6 pause/resume in
generation 2 (then again in generation 3 if stable). The toolkit
should still see current RAM, without reconnecting.

The native source compiler/host tests confirm only checkpoint
plumbing and clock arithmetic; Ubuntu gameplay testing is required
to mark the feature operational. Save-state capture/restore is
outside this branch.

## Rollback

```bash
cmake -S . -B build-generated/japan-native-game \
  -DGOEMON_JP_NATIVE_F6_PAUSE=OFF
cmake --build build-generated/japan-native-game \
  --target Goemon64RecompiledJP --parallel 4
```
