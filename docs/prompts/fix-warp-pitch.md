# Task: the time-warped reference is pitch-shifted and unlistenable

## Symptom

`rambass align <song> --warp` produces `practice/<stem>-aligned.wav`. The
alignment is correct — it lands the reference on the fixed grid, and the Reaper
`REF aligned` track lines up with the drums. But the audio **wobbles in pitch,
up and down, once per beat**, which makes it unusable for the one job it has.

## Cause, measured

`rambass.align.warp_samples` implements the warp as **resampling** — it reads the
source at `np.interp(position * rate, ...)`. Resampling changes playback rate and
pitch *together*: a segment played at rate `r` is transposed by `12·log₂(r)`
semitones.

Manlio's fitted map has 308 per-beat segments with rates spanning **0.929–1.091**,
so the pitch moves every beat. Measured on a 440 Hz tone pushed through the real
plan:

| segment rate | 440 Hz reads as | shift |
|---|---|---|
| 0.929 | 408.0 Hz | **−1.31 semitones** |
| 1.000 | 440.0 Hz | 0.00 |
| 1.091 | 480.0 Hz | **+1.51 semitones** |

**2.78 semitones peak-to-peak, changing once per beat.** That is the warble.

## What has to change

Time must stretch and compress while **pitch is preserved**. Nothing else about
the alignment changes: `fit_anchors`, `AlignMap`, `warp_plan`, `plan_problem`,
`MAX_RATE` and the anchor semantics are all correct and must keep working.

The change is confined to how audio is rendered along the existing map.

## Read these first

* `src/rambass/align.py` — `warp_samples` is the function to replace. **Its
  docstring actively argues for the bug** ("Linear interpolation, deliberately…
  the rates here are within a few percent of 1.0"). That reasoning was about
  *timing* accuracy and never considered pitch; correct it rather than leaving it
  to mislead the next reader.
* `docs/practice-tracks.md` — "Linear interpolation resamples it, deliberately"
  says the same thing and needs the same correction.
* `CLAUDE.md` — the `practice/align.yaml` paragraph lists what is settled about
  alignment. Add what this task settles.
* `tests/test_align.py` — the existing warp tests must keep passing, in spirit if
  not to the millisecond (see *Tolerances* below).

## Constraints from CLAUDE.md, which apply

* **Build it test-first.** Write the failing test before the code, every time.
* **No new core-tier dependency without a real need.** `align.py` currently
  imports only numpy, and `tests/test_align.py` runs with no librosa, no ffmpeg
  and no audio file on disk. Keeping that is worth real effort: it is what makes
  the module testable at all.
* **A number that came from a measurement gets a test naming the measurement.**
* Pure functions where possible; anything random takes a seed.

## Approach — recommendation and the trade-offs

**Recommended: WSOLA (waveform-similarity overlap-add) in numpy.** Fixed
synthesis hop, analysis hop varying with the local rate, and a short
cross-correlation search around each analysis position to pick the offset that
best continues the previous output frame. Pitch-preserving, transient-tolerant,
~60–100 lines, no new dependency, and testable exactly as the module is now.
The easy case, too: rates within ±10% of 1.0 and no need for extreme stretch.

Rejected, with reasons — do not silently pick one of these instead:

* **Phase vocoder** (numpy, or `librosa.effects.time_stretch`). Pitch-preserving
  and handles a varying rate naturally, but it smears transients. This reference
  exists to judge whether programmed drums sit where the band played, so attack
  definition *is* the signal. Also pulls `align.py` into the librosa tier.
* **ffmpeg `atempo`**. Good quality and ships in every build, but it takes one
  fixed rate per instance, so 308 segments means 308 invocations and 308 joins to
  click at — and it moves the work out of the pure-numpy tier, so the tests would
  need ffmpeg.

### One important implementation note

Do **not** WSOLA each `WarpSegment` independently and concatenate — that is 308
boundaries to produce artefacts at. Run one continuous pass over the output
timeline and, for each output frame, ask the map where that moment is in the
recording. `AlignMap.source_at(target_seconds, timeline)` already answers exactly
that and is piecewise-linear across the anchors, so a continuous warp falls out
of it. `warp_samples` may need the map (or a callable) rather than the plan;
changing its signature is fine — keep `warp_plan` and `plan_problem`, which the
CLI and the guard both use.

## Verification bar

Tests (numpy only, no audio files):

1. **Pitch holds.** A 440 Hz sine warped at rates 0.9, 1.0 and 1.1 must still
   read 440 Hz within 1% (< 0.2 semitones). This is the regression test for the
   bug; name the measured before-numbers from the table above in the test.
2. **Timing still lands.** A click train through a drifting map still puts the
   clicks on the grid. The current test asserts within 20 ms; WSOLA can move a
   transient by up to its search window, so if that has to relax, **say by how
   much and why in the test**, and keep it well inside the 50 ms that
   `docs/practice-tracks.md` sets as the limit.
3. **Length** still matches what the grid says, as now.
4. **Edges**: reading past the end of the source yields silence, not a wrap; an
   empty plan returns the audio untouched; silence in, silence out.

Then on real data — report these numbers, do not just assert they are fine:

5. Re-render Manlio and re-measure the warped backbeat error against the grid.
   The resampling version scored **median 21 ms, p90 37 ms** (against 29/108 for
   a single offset). Do not regress p90 past ~50 ms.
6. Show the pitch wobble is gone: track a sustained bass note's fundamental
   across the warped file and compare with the same material in
   `stems/no_drums.wav`. It should not move where the source does not move.

## Follow-through

* Re-render: `rambass align manlio --warp`, then `rambass reaper build manlio`.
* `rambass stale manlio` will flag `practice/no_drums-aligned.wav` because the
  `align` module hash changes — that is correct, and it should come back green
  after the re-render. Check it does.
* Update `docs/practice-tracks.md`, the `warp_samples` docstring, and CLAUDE.md.
* Run the full suite (`pytest` — 680 tests green before this task) and commit.

## Environment

`RAMBASS_FFMPEG` must be set — ffmpeg is installed on this machine but winget
linked none of it. See `docs/setup.md`. The venv is at `.venv/`; `rambass` is a
shell function defined in `~/.bashrc`.
