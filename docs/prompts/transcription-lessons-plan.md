# Implementation plan: act on Manlio's review-pass findings

Paste target: Claude Code, working directory `C:\Users\paolo\prj\rambass-live`.
Intended to run unattended. Written 28 Aug 2026 from the measurements in
`docs/transcription-lessons.md` — read that file first; it is the evidence for
every number below.

---

Read `CLAUDE.md` and `docs/transcription-lessons.md` before writing any code.
Then work through the phases below in order. Commit after each phase, on `main`.

## Ground rules for this run

* **Test-first, every phase.** Write the failing test, watch it fail, then write
  the code. This is `CLAUDE.md`'s rule and it is the whole point here: these are
  constants arrived at by measurement, and a constant with a story and no test is
  one somebody will clean up.
* **Do not run `rambass drums transcribe`, `drums stems`, or anything touching
  demucs or librosa.** Minutes of CPU, and re-transcription can change the part
  under a review pass that is still in progress. Every fixture you need is
  already committed.
* **Do not edit any musical field of any `song.yaml`.** Not `sections`, not
  `drums.additions`, not `bars`. The new fields you are adding are Paolo's to
  fill in by ear. Where a phase would benefit from a worked example, print the
  proposal to stdout and leave it there.
* **Do not commit audio or video.** Check `git status` before `git add`.
* Run the suite with `pytest` directly (there is no `make` on this machine, and
  `.venv` is a `uv venv` with no pip — use `uv pip install` if you need an extra,
  though you should not need one).
* Keep the dependency tiers intact: everything in this plan is pure Python over
  `list[Hit]` / `DrumPerformance`. **Nothing here imports numpy, librosa,
  pedalboard or demucs.** If you find yourself wanting audio, you have taken a
  wrong turn — see "what not to build" at the bottom.
* If a phase turns out to be wrong, stop, leave the earlier phases committed,
  and write what you found at the end of `docs/transcription-lessons.md`. A
  negative result recorded is worth more than a rule forced through.

## Phase 0 — the regression harness

Nothing else can be judged without this, so it goes first.

Manlio's four tracked MIDI files are the fixture:

| file | role |
|---|---|
| `songs/tutti-in-fila/09-manlio/midi/drums-quantized.mid` | input to `consolidate` |
| `songs/tutti-in-fila/09-manlio/midi/drums-consolidated.mid` | what the pipeline produces today |
| `songs/tutti-in-fila/09-manlio/midi/drums-restored.mid` | **ground truth** — Paolo's 205 promoted edits applied |
| `songs/tutti-in-fila/09-manlio/qa/review.yaml` | the 207 notes, with `section`, `kind`, `instrument`, `swap_to` |

Build `tests/test_manlio_regression.py`:

1. A helper that loads the song via `manifest.load_song`, reads a MIDI file with
   `midiio.read_drum_midi`, and sets `performance.timeline = song.timeline()`.
2. A scorer: for a given performance, over a named set of sections, count
   `(tp, missing, extra)` against `drums-restored.mid`. Match on **instrument
   class**, not instrument — collapse `{hihat_closed, hihat_open, hihat_pedal,
   ride, ride_bell}` to `hat`, `{crash, crash_2, splash}` to `cymbal`,
   `{snare, sidestick}` to `snare`, the six toms to `tom`, `kick` to `kick`.
   Tolerance 60 ms. Voicing is a separate question from placement and the
   declarations in later phases are what answer it.
3. Score **all fifteen sections**, the whole song. Scoring a hand-picked subset
   invites cherry-picking and makes every number in this plan scope-dependent —
   which is exactly the trap noted below. Bear in mind the ground truth is
   *partial* outside the sections Paolo has marked done, so treat the absolute
   figure as a tripwire and the *deltas* as the evidence.
4. Skip the whole module with `pytest.skip` if any fixture is missing, so the
   suite still runs on a clean checkout.

Pin today's number as the baseline:

```
drums-consolidated.mid vs drums-restored.mid, all 15 sections:
    tp = 961, missing = 126, extra = 28   -> 154 errors
```

Scope warning: the threshold sweep table in `docs/transcription-lessons.md`
was scored over a narrower set of eight sections and so reports 132 for the same
file. Both are right; they are different denominators. Use the whole-song figure
throughout this plan and do not mix them.

Write that as a test asserting the **current** file scores exactly that. It is a
tripwire: if it changes, either a fixture was regenerated or the scorer drifted,
and either way the numbers in the phases below stop meaning anything.

Then add a second test that runs `quantize.consolidate` on `drums-quantized.mid`
with today's settings and asserts it reproduces the committed
`drums-consolidated.mid` hit-for-hit (instrument, time to 1 ms, velocity). If it
does not, **stop and report** — the fixture and the code have already diverged
and every later measurement is against the wrong baseline.

Commit: `A regression harness that scores the drum part against Manlio's review pass`

## Phase 1 — `consolidate` stops stamping over a bar that stopped

`quantize.consolidate` owns the fill problem in its docstring ("this deliberately
removes the fills"). The mirror case is undocumented and costs a third of the
extra-hit edits: the voted pattern is stamped into **every** repetition including
the last, where the band lifts off into the break.

Measured: bar 17 (last of `verse-1-lift`) — the input's detections stop dead at
beat 3, and consolidate stamps 3.333, 3.667, 4.0 hat, 4.0 snare, 4.333, 4.667.
Truth is `hihat_open` at 3 and `hihat_pedal` at 4. Same in bar 32 (last of
`verse-2-lift`, stops at beat 1) and bar 54 (last of `verse-3-lift`, stops at
beat 3).

**The rule.** If a bar's own detections in the input performance stop at least
`stop_beats` before that bar ends, stamp nothing after them in that bar. It
applies only where consolidate actually stamps — a section it reported as
`skipped` is passed through untouched and there is nothing to withhold.

**The constant is 1.25 beats.** The sweep, over the reviewed sections:

| stop_beats | dropped | phantom | real killed |
|---:|---:|---:|---:|
| 0.5 | 24 | 10 | 14 |
| 1.0 | 22 | 10 | 12 |
| **1.25** | **11** | **10** | **1** |
| 2.0 | 11 | 10 | 1 |

Below a beat it starts eating the ordinary one-slot gaps consolidation exists to
fill. At 1.25 it fires on exactly three bars, all three the last bar of a
section. Add `stop_beats: float = 1.25` to `ConsolidateSettings` with a docstring
carrying that table and the reason the number is not near 1 by accident: a whole
beat of silence inside a bar of continuous triplets is a stop, and a drummer who
stopped is information, not a dropout — the same argument as `bars` bounding the
part.

Report it. Each affected bar goes in the section's report entry (bar, beat where
the detections stopped, how many hits were not stamped) so
`cli.cmd_drums_consolidate` can print it the way it already prints `demoted`.
A silent behaviour change here is exactly the kind of thing that reads as a bug
six months later.

Tests:

* Synthetic: a 5-bar section, four bars of a full pattern and a fifth that stops
  after beat 1. Assert nothing is stamped past beat 1 of bar 5, and that bars 1-4
  are untouched.
* A one-slot gap at the end of a bar is still filled (`stop_beats` not triggered).
* `stop_beats=0` restores the old behaviour exactly — assert the consolidated
  output equals the committed fixture. This is the escape hatch and it must work.
* Regression, verified before this plan was written: 11 hits are withheld and
  errors drop from **154 to 145** — `extra` 28 → 18, `missing` 126 → 127, i.e.
  ten phantoms removed for one real hit lost. Assert the exact numbers; if you
  get anything else, something upstream differs and you should stop and say so.

Commit: `Consolidate stops stamping a section's pattern over a bar that stopped`

## Phase 2 — a quiet snare in an unvoted section is a phantom

`min_repeats: 4` means a 2-bar break is passed through untouched, so every
detection artefact in it survives. `break-1` and `break-3` are the second-densest
edit region in the song — 26 notes, and bar 63 alone has 17.

Velocity-floor hits (v ≤ 50) in the six sections consolidate skipped:

| instrument | hits | phantom | real | in the rule? |
|---|---:|---:|---:|---|
| snare | 7 | **6** | 1 | **yes** |
| hihat_closed | 10 | 0 | 10 | no |
| hihat_open | 2 | 0 | 2 | no |
| tom_mid | 2 | 0 | 2 | no |
| kick | 1 | 0 | 1 | no |
| sidestick | 3 | 1 | 2 | **no** — see below |

The snares Paolo kept run a median of v108 with a p10 of v71. The hats, kicks and
toms genuinely play at the floor.

**The rule.** In a section `consolidate` reported as `skipped`, drop `snare` hits
at or below `phantom_snare_velocity: int = 50`.

**`sidestick` is deliberately excluded, and this was measured, not assumed.**
Adding it drops three more hits of which only one is a phantom, and the score gets
*worse*: 140 errors with snare alone against 141 with both. A rim click is 25-30 dB
below the same drummer's snare (see `transcribe.SIDESTICK_BODY_SHARE` and
`drums.backbeat_velocity`), so the velocity floor is where a *real* side-stick
lives — it is a quiet instrument, not a quiet stroke. Put that in the docstring;
folding the two together is the obvious "simplification" and it costs real hits.

The docstring must say *why it is only the snare*, because that asymmetry is the
whole finding and a later reader will "generalise" it to every instrument and
delete 15 real hits: this is the existing "a hi-hat is never loudness evidence"
rule pointed the other way — **a snare is the one instrument whose own quietness
is evidence against it**, because this drummer's soft strokes on that drum are
side-sticks and ghost notes that land in other lanes. Cross-reference
`transcribe.suppress_cross_stem_bleed`.

Two constraints:

* It applies **only** where the section was skipped. A voted section already has
  a better instrument than level, namely agreement, and must not be second-guessed.
* `0` disables it, and that is not velocity 0 — same convention as
  `drums.backbeat_velocity`.

Tests:

* Synthetic: a 2-bar section below `min_repeats` holding a v45 snare, a v45
  hi-hat and a v45 side-stick. Assert the snare goes and the other two stay.
* The same v45 snare inside a voted section survives.
* Regression, verified: 7 hits dropped, errors **145 → 140**, `extra` 18 → 12,
  `missing` 127 → 128. Assert exactly.
* A test that pins the exclusion: with `sidestick` added to the rule the score is
  141, i.e. worse. Assert the snare-only variant scores better than the
  snare-plus-sidestick variant, so the reason survives as executable evidence
  rather than a comment.

Commit: `A velocity-floor snare in a section too short to vote on is a phantom`

## Phase 3 — `sections[].hats`, a declared hi-hat pattern

The largest single cause: 66 notes are holes in a continuous hi-hat run, and 89
notes in total are a hat pattern the pipeline got wrong. `docs/transcription-lessons.md`
records that this is **not recoverable from the audio** — the threshold sweep and
the hat-stem flux probe both fail, and the occupancy count cannot even tell
`chorus-1` (a continuous triplet run reading 6 of 12 slots) from `chorus-2` (a
genuine shuffle reading 9). So it gets declared, in the register `voicing:` and
`backbeat:` already established.

**This one field replaces two.** An earlier draft proposed a separate
`backbeat_hat` key for the hat that `transcribe.drop_hats_on_sidesticks` removes
under a side-stick. It is unnecessary: verse-1's and verse-2's ground truth is a
hat on **all twelve** triplet slots, so `hats: run` on those sections covers the
side-stick holes as a side effect. Build one field.

### The field

On `manifest.Section`, beside `backbeat` and `voicing`:

```yaml
- name: chorus-1
  bar: 32
  beat: 3.0
  hats: run                       # every slot of drums.subdivision
- name: verse-1
  bar: 6
  backbeat: sidestick
  hats: run                       # the hat plays under the click too
- name: verse-3-lift
  bar: 51
  hats: "x.xx.xx.xx.x"            # explicit, one char per slot in a bar
```

* `run` — every subdivision slot.
* `shuffle` — the first and last slot of each beat (requires `subdivision: 3`;
  raise `ProjectError` naming the song if not).
* An explicit mask of `x` and `.`, one character per slot in a bar, length
  `subdivision * beats_per_bar`. Whitespace and `|` ignored so a reader can group
  it by beat.
* Absent or `""` — do nothing, which is today's behaviour.

Validate in `Section.problems`/`Song.problems` the way `voicing` is validated:
an unknown keyword or a wrong-length mask must be an error, because a typo here
fills nothing and looks exactly like a no-op.

Round-trip through `to_dict`/`from_dict`, and add it to the provenance fields for
the restore step next to `sections` — a `hats:` change that `rambass stale` does
not notice is a change that silently never gets applied.

### The pass

New pure function in `restore.py`, beside `revoice_sections`:

```python
def fill_hat_runs(performance, sections, *, end_bar, subdivision,
                  stop_beats=1.25, instrument="hihat_closed") -> tuple[DrumPerformance, dict]
```

* Fill only slots where **no hat-family instrument** already sounds
  (`hihat_closed`, `hihat_open`, `hihat_pedal`, `ride`, `ride_bell`) within the
  usual 30 ms window. Never move, revoice or overwrite an existing hit — the hats
  the transcriber did find in `chorus-1` carry real velocities of 86-113 and
  flattening them would delete the part while claiming to complete it. Same
  argument as `revoice_sections` keeping velocities.
* Velocity: the median of the section's own existing hat hits; the song's median
  hat if the section has none; `45` if there are none at all. Report which of the
  three was used.
* Fill `hihat_closed` and let `voicing:` retarget it. Do not add an instrument
  option — `theme-finale` already turns hats into a ride bell that way, and two
  mechanisms for one decision is the thing this field exists to avoid.
* **Respect Phase 1's stop rule.** A section's mask must not fill the tail of a
  bar whose own hits stop `stop_beats` or more before the bar ends, or Phase 3
  silently undoes Phase 1 and puts twelve hats back into bar 17. Factor the
  "where does this bar's playing stop" helper out of Phase 1 and share it. There
  must be a test for exactly this interaction.
* A section start on a fractional beat is normal here (`chorus-1` starts at
  32.3). The mask tiles the **bar** grid, not the section — a one-bar hat figure
  repeats every bar whichever beat the section began on. Reuse the reasoning, and
  ideally the helpers, from `quantize._repetitions`.

Wire it into `cli.cmd_drums_restore` **before** `revoice_sections`, which is
before `apply_edits`. Order matters and is worth a comment: fill, then re-voice
what was filled, then let declared additions win over both.

### Reporting

Extend `rambass drums missing` (or add `rambass sections --hats`) to **propose** a
mask per section from the consolidated part's own occupancy: for each section,
print the slots currently occupied in ≥50% of the bars they could appear in,
alongside the nearest named keyword. On Manlio it should print, and these are the
six sections whose pattern is wrong today:

```
verse-1        [0,1,2,4,5,6,7,8,10,11]     (run, missing slots 3 and 9)
break-1        [0,3,6,8,9,10,11]
verse-2        [0,1,2,4,5,6,8,10,11]       (run, missing slots 3, 7 and 9)
chorus-1       [0,3,5,6,9,11]
verse-3-lift   [0,3,5,6,9]
closing-fill   [0,1,2,4,5,6]
```

Print it; **do not write it into `song.yaml`.** Which of those is a detection hole
and which is the part is a question for Paolo's ear, and `break-1` and
`closing-fill` are cases where the pipeline currently has *more* than the truth.

Tests:

* Each of `run`, `shuffle` and an explicit mask, on a synthetic section.
* An existing `hihat_open` at a masked slot is left alone, not doubled and not
  renamed.
* Velocity comes from the section's median, and the fallback chain works.
* A section starting on beat 3 still tiles the bar grid correctly.
* The Phase 1 interaction: a masked section whose last bar stops early gains no
  hats in that tail.
* Validation: bad keyword, wrong-length mask, `shuffle` with `subdivision: 4`.
* Provenance: changing `hats` on a section makes `rambass stale` report the
  restore step.

Do **not** add a regression assertion for this phase — `hats:` is unset on every
section, so the fixture score cannot move. Instead add a test that *simulates*
the declaration: load the song, set `hats: run` on `chorus-1`, `verse-1` and
`verse-2` in memory, run the pass, and assert the missing-hat count in those
three sections drops by at least 80. That is the phase's real evidence and it
does not need a manifest edit.

Commit: `A section can declare its hi-hat pattern, in one line rather than 89 edits`

## Phase 4 — boundary hat candidates

Ten of the leftover notes are one recurring thing: an open hat or a foot splash on
the **last beat before a section boundary** — `hihat_closed → hihat_open` at bars
13.4, 17.3, 24.2.667, 32.1, 44.2.667 and 54.3, and `hihat_pedal` at 17.4, 39.4,
40.1 and 40.2.

`restore.crash_candidates` already makes this argument for cymbals: what separates
a boundary crash from a wash is musical position, not spectrum. Add
`restore.hat_candidates` with the same shape — same `MissingHit` return, same
"candidates, not verdicts" framing — reporting the last hat before each section
start as a candidate for `hihat_open` or `hihat_pedal`, and fold it into
`restore.checklist` so it reaches `qa/missing-hits.md`.

This is a **checklist, not a rule**: it must not edit the part. A drummer opening
the hat into a change is a musical choice, and so is not doing it.

Tests: the candidate is reported at a boundary and not mid-section; a boundary
that already has an `hihat_open` is not reported; the checklist renders it in
Reaper bar numbers (`bar + count_in.bars`) like everything else there.

Commit: `Report the open hat and foot splash at a section boundary as candidates`

## Phase 5 — write down what happened

Update `docs/transcription-lessons.md` with the numbers you actually measured
(they should match the tables above; if any differs, say so and say by how much).
Add a short section to `docs/drums-rebuild.md` Stage 6 for the stop rule and the
phantom-snare gate, and to Stage 7 for `sections[].hats`. Add `hats:` to whatever
`song.yaml` reference exists.

Then add to `CLAUDE.md`, under "Facts that were verified, don't re-guess them",
in the compressed style of the entries around it, three things:

1. A continuous hi-hat run is not recoverable from this audio — the threshold
   sweep (0.55 is the best of twenty settings) and the hat-stem flux probe
   (2.5× separation on a median of 0.001 in `chorus-1`, against 20-50× elsewhere)
   both fail, and occupancy cannot tell a run from a shuffle. It is declared with
   `sections[].hats`, and `backbeat_hat` was folded into it rather than shipped.
2. The last bar of a section is the one least likely to repeat, so `consolidate`
   stops at a stop — with the 1.25-beat sweep.
3. A quiet snare is a phantom and a quiet anything else is not, with the 6-of-7
   measurement and the reason.

Commit: `Write down what Manlio's review pass taught the transcriber`

## What not to build — measured, and on the record

Do not attempt any of these. Each one was tried and the numbers are in
`docs/transcription-lessons.md`.

* **Do not retune `consolidate.threshold` or `unit_bars`.** Twenty settings were
  swept; the shipping 0.55/auto is the best of them at 132 errors against
  144-162. Lowering the threshold trades missing for extra one for one.
* **Do not lower the hi-hat detection delta** to recover the missing hats.
  `transcribe.bands_with_hat_delta` already records that the marginal strokes
  arrive at about half the precision of the ones already found.
* **Do not add an audio pass that re-interrogates the empty grid slots.** In
  `chorus-1` the hat-stem flux separates hat from no-hat by 2.5× on a median of
  0.001, where every other section separates by 20-50×; the crashes on beats 1
  and 3 leave nothing in that stem to find.
* **Do not fill hat holes wherever occupancy looks high.** At ≥9 of 12 slots it is
  47% precise — 88 hats added, 47 phantom. The only safe threshold is ≥10 of 12,
  which fires on verse-1 alone and is subsumed by `hats: run`. If you want it as a
  belt-and-braces default it must be ≥10 and it must be off by default.
* **Do not add acoustic tom-pitch classification.** The fundamental of the toms
  stem at the declared `tom_low`, `tom_low_mid` and `tom_mid` hits is a median of
  106.7 Hz for all three, and the 53 Hz readings are bass bleed rather than a
  floor tom.
* **Do not try to detect crash vs open hat, or `crash` vs `crash_2`,
  spectrally.** Four attempts are already on record in
  `transcribe.split_cymbal_runs` and `restore.crash_candidates`.
* **Do not make `drop_hats_on_sidesticks` stop dropping.** The phantom v122
  accent it removes is real; `hats:` puts the hat back afterwards, deliberately,
  at a velocity somebody chose.

## Done looks like

* `pytest` green, and the new tests fail if their constants are changed.
* The Manlio regression score has gone **154 → 140 errors** with no declaration
  written (Phase 1 −9, Phase 2 −5), and the simulated-`hats:` test shows a
  further ~89 notes' worth available once Paolo fills the field in.
* `git log` shows one commit per phase on `main`, no audio, no `song.yaml`
  musical fields touched.
* A short summary at the end of the run saying what moved, what did not, and
  anything you found that contradicts the tables above.

## For Paolo, after the run

The mechanism will be in but every `hats:` field will be empty. The proposal
output from Phase 3 lists the six sections whose pattern is wrong; the ones the
measurements already settled are `chorus-1: run`, `verse-1: run`, `verse-2: run`,
plus `voicing: {hihat_open: hihat_closed}` on `chorus-1`. Those four lines are
worth about 89 notes on their own. `break-1` and `closing-fill` want a mask that is
*smaller* than what the pipeline produced, so they are worth a listen rather than
a keyword.

One caution when you do fill them in: `chorus-1` already has 25 promoted
`swap-hit` edits and 43 promoted `missing-hit` edits in `drums.additions`. Adding
`hats: run` and `voicing:` on top of those will double up. `rambass review demote`
is the inverse — take those 68 notes back out first, then declare, then rebuild,
and the ledger and the manifest stay in agreement.
