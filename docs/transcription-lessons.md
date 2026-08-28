# What Manlio's review pass taught the transcriber

Manlio is the one song on *Tutti in Fila* that has been through a full review
pass, so its 207 notes and 205 promoted edits are the only ground truth in the
repo about where the drum pipeline is actually wrong. This is what came out of
scoring the pipeline against them: three rules that shipped, one that did not,
and a list of things not to try again.

Everything here is measured against the four tracked files:

| file | role |
|---|---|
| `songs/tutti-in-fila/09-manlio/midi/drums-quantized.mid` | input to `consolidate` |
| `songs/tutti-in-fila/09-manlio/midi/drums-consolidated.mid` | what the pipeline produced before this pass |
| `songs/tutti-in-fila/09-manlio/midi/drums-restored.mid` | **ground truth** — Paolo's promoted edits applied |
| `songs/tutti-in-fila/09-manlio/qa/review.yaml` | the notes, with `section`, `kind`, `instrument`, `swap_to` |

`tests/test_manlio_regression.py` is the harness, and every number below has an
assertion behind it. Dates: measured 28 Aug 2026.

## How the score works, and why the denominator matters

`(tp, missing, extra)` over the **whole song, all fifteen sections**, matching on
**placement class** at the song's own grid slot.

Class rather than instrument — the five hat-family instruments collapse to
`hat`, the crashes to `cymbal`, snare and side-stick to `snare`, the six toms to
`tom` — because placement and voicing are separate questions. Whether a hat
belongs at a slot is Stage 6's business; which hat it is is Stage 7's, and the
declarations answer that separately. A slot carrying both a closed and an open
hat is one hat as far as placement goes.

Grid slot rather than proximity in seconds, which is the same choice
`restore.missing_hits` already makes and for the same reason: both files were
quantised to the same grid. The two are **not** interchangeable — collapsing each
class at 60 ms instead scores 163 where the grid form scores 154. The nine are
hand-placed additions off the triplet grid (bar 15 beats 4.167, 4.5, 4.833 and
the bar 63 tom run) which are 167 ms from their neighbour: two events at 60 ms,
one slot on the grid.

Two cautions that come with scoring the whole song. The ground truth is
**partial** outside the sections Paolo has marked done, so the absolute figure is
a tripwire and the **deltas** are the evidence. And any narrower set of sections
gives different numbers for the same file, so a figure quoted without its
denominator means nothing.

Baseline, and where the three shipped rules land:

| | tp | missing | extra | errors |
|---|---:|---:|---:|---:|
| `drums-consolidated.mid` as committed | 961 | 126 | 28 | **154** |
| + the stop rule | 960 | 127 | 18 | **145** |
| + the phantom-snare gate | 959 | 128 | 12 | **140** |
| + `hats: run` on three sections (simulated) | 1040 | 47 | 31 | **78** |

The first three need no line of `song.yaml`. The fourth is a declaration Paolo
has to make by ear, and it is worth more than the other two put together.

## What the 207 notes are

Grouped by what would have to change to prevent them:

* **89 are a hat pattern the pipeline got wrong**, 66 of those holes in a
  continuous hi-hat run. The single largest cause, and not recoverable from the
  audio — see below. Now declarable as `sections[].hats`.
* **28 are extra hits**, of which a third are the pattern stamped over a bar
  where the band had stopped, and the densest cluster is velocity-floor snares
  in the breaks. Both now handled in Stage 6.
* **26 are in `break-1` and `break-3`**, the second-densest edit region, with 17
  in bar 63 alone. These are 2-bar sections, below `min_repeats`, so the vote
  never ran on them.
* **10 are an open hat or a foot splash near a section change.** Tried to
  automate; failed. See "measured and rejected".
* The rest are cymbal naming (`crash` vs `crash_2` vs open hat), tom pitch, and
  the finale's ride bell — all already on record as undetectable and all already
  declarable.

## Rule 1 — `consolidate` stops stamping over a bar that stopped

`quantize.consolidate` owns the fill problem in its own docstring: a fill is the
bar that does not repeat, so no threshold keeps it. The mirror case was
undocumented and cost a third of the extra-hit edits — the voted pattern was
stamped into **every** repetition including the last, which is the one least
likely to repeat, because it is where the band lifts off into the break.

Bar 17, the last of `verse-1-lift`: the input's detections stop dead at beat 3
and the vote stamped 3.333, 3.667, 4.0 hat, 4.0 snare, 4.333 and 4.667 over the
silence. Bars 32 and 54 are the same bar of `verse-2-lift` and `verse-3-lift`.

`ConsolidateSettings.stop_beats = 1.25`. The sweep:

| `stop_beats` | dropped | phantom | real killed | errors |
|---:|---:|---:|---:|---:|
| 0.0 (off) | 0 | 0 | 0 | 154 |
| 0.5 | 29 | 10 | 19 | 163 |
| 1.0 | 27 | 10 | 17 | 161 |
| **1.25** | **11** | **10** | **1** | **145** |
| 1.5 | 11 | 10 | 1 | 145 |
| 2.0 | 11 | 10 | 1 | 145 |

Two things there matter more than the winning row. **Everything below 1.25 is
worse than not doing it at all**: it finds the same ten phantoms and takes 17 to
19 real hits with them, because at that width the rule stops reading "the band
stopped" and starts reading "this bar is missing a hit at the end", which is the
case the vote exists to repair. And **1.25 is a plateau, not a peak** — 1.25, 1.5
and 2.0 fire on the same three bars — so the constant is not balanced on a knife
edge between two songs.

A whole beat of silence inside a bar of continuous triplets is a stop, and a
drummer who stopped is information rather than a dropout: the same argument
`bars` makes for bounding the part.

The one thing the constant assumes is a **dense grid**. Manlio plays twelve hat
slots to the bar. On a pattern that only plays the four beats, a bar that merely
loses its beat-4 hit has two beats of silence behind it and reads as a stop.
`stop_beats=0` is the escape hatch for such a song; a smaller number is not, per
the sweep.

An **empty** bar is never a stop — there has to be playing for it to stop after,
and an empty bar is exactly the case the vote is for.

`quantize.stopped_bars` is the helper, shared with the hat fill, and it measures
inside a bar intersected with the section: `verse-2-lift`'s last bar is the two
beats it actually has, because `chorus-1` starts at bar 32 beat 3.

## Rule 2 — a velocity-floor snare in an unvoted section is a phantom

`min_repeats: 4` means a 2-bar break is passed through untouched, so every
detection artefact in it survives. Velocity-floor hits (v ≤ 50) in the six
sections `consolidate` skipped:

| instrument | hits | phantom | real | in the rule? |
|---|---:|---:|---:|---|
| snare | 7 | **6** | 1 | **yes** |
| hihat_closed | 11 | 0 | 11 | no |
| sidestick | 3 | 1 | 2 | **no** |
| hihat_open | 2 | 0 | 2 | no |
| tom_mid | 2 | 0 | 2 | no |
| kick | 1 | 0 | 1 | no |

The snares Paolo kept in those sections run a median of v107. The hats, kicks and
toms at the floor genuinely play that quietly.

**The asymmetry is the finding.** This is the existing "a hi-hat is never
loudness evidence" rule pointed the other way: **a snare is the one instrument
whose own quietness is evidence against it**, because this drummer's soft strokes
on that drum are side-sticks and ghost notes that land in other lanes. Applying
the rule to every instrument at the floor scores **155** — worse than not having
it. `quantize.PHANTOM_FLOOR_INSTRUMENTS` is therefore one entry long, and a
module constant rather than a setting, so it cannot be widened from a manifest.

`sidestick` is excluded and that was measured, not assumed: adding it drops three
more hits of which only one is a phantom, and the score gets **worse** — 141
against 140. A rim click is 25–30 dB below the same drummer's snare, so the
velocity floor is where a *real* side-stick lives. It is a quiet instrument, not
a quiet stroke, and folding the two together is the obvious "simplification" that
costs real hits.

50 is **the floor plus slack, not the best score on this song.**
`scale_velocities` puts Manlio's floor at v45, so "at or below 50" means *at the
floor* — a boundary with a reason. 55 would also catch the v53 phantom at bar 19
beat 4.809 and score 139. Deliberately not taken: one drummer and one kit means
the number has to hold for the other ten songs of the album, and above the floor
it starts deleting strokes that carry a measured level.

It applies **only** where the section was skipped. A voted section already has a
better instrument than level, namely agreement.

The seven dropped hits, named: bars 18.2.667, 19.3.667, 19.4.496, 20.1, 63.4 and
64.4.667 are phantoms; **41.2.181 is real** and is the one this rule costs.

## Rule 3 — the hat pattern has to be declared, not detected

89 notes are a hat pattern the pipeline got wrong, 66 of them holes in a
continuous run. **It is not recoverable from this audio**, and that is measured:

* the **threshold sweep** — 0.55/auto is the best of twenty `consolidate`
  settings, at 132 errors over the eight reviewed sections against 144–162 for
  the alternatives, and lowering the threshold trades missing for extra one for
  one;
* the **hat-stem flux probe** — in `chorus-1` the hat stem separates hat from
  no-hat by 2.5× on a median of 0.001, where every other section separates by
  20–50×, because the crashes on beats 1 and 3 leave nothing in that stem to
  find;
* **occupancy** cannot even rank the two cases correctly: `chorus-1` is a
  continuous triplet run that reads **6 of 12** slots, and `chorus-2` is a
  genuine shuffle that reads **9**.

So it is declared: `sections[].hats`, taking `run`, `shuffle`, or a mask of `x`
and `.` one character per slot in a bar. Same register as `backbeat:` and
`voicing:` — arrangement structure, settled by ear once, never re-derived from
audio.

**One field, not two.** An earlier draft proposed a separate `backbeat_hat` for
the hat that `drop_hats_on_sidesticks` removes under a side-stick. Unnecessary:
verse-1's and verse-2's ground truth is a hat on all twelve triplet slots, so
`hats: run` covers the side-stick holes as a side effect.

What three declarations are worth, simulated in memory (the field is unset in
`song.yaml` and is Paolo's to fill in): `hats: run` on `chorus-1`, `verse-1` and
`verse-2` recovers **all 81** hat holes in those sections and takes the whole
song from **140 to 78** errors.

It costs 19 hats the review pass does not have, and where they are matters: 16 in
`chorus-1`, 3 in `verse-2`, almost all in the **partial bars at either end of a
section**. Bar 20 beat 3.333 is two slots after `verse-2` begins at 20.3; bars
39–40 are where `chorus-1` runs out at 40.3. The mask tiles whole bars, so a
section starting or ending mid-bar gets the pattern across the part of the bar it
owns, and whether the band really plays the run from the section's very first
slot is an ear question. That is a caution for filling the field in, not a reason
to round the spans to bar lines — Stage 6's docstring says why rounding is worse.

`rambass sections <song> --hats` prints the occupancy per section beside the
nearest keyword. It names the variant it measured and marks the sections too
short for `consolidate` to vote on, where every slot is played in one bar or two
and the share says very little. From `drums-consolidated.mid`:

```
    verse-1         [0,1,2,4,5,6,7,8,10,11]       xxx.xxxxx.xx  (run, missing slots 3, 9)
    verse-1-lift    [0,1,2,3,4,5,6,7,8,9,10,11]   xxxxxxxxxxxx  (run)
   ?break-1         [0,3,6,9]                     x..x..x..x..
    verse-2         [0,1,2,4,5,6,8,10,11]         xxx.xxx.x.xx  (run, missing slots 3, 7, 9)
    chorus-1        [0,3,5,6,9,11]                x..x.xx..x.x
    verse-3         [0,2,4,5,6,7,8,10,11]         x.x.xxxxx.xx  (run, missing slots 1, 3, 9)
    verse-3-lift    [0,3,5,6,9]                   x..x.xx..x..
    chorus-2        [0,1,3,5,6,7,9,10,11]         xx.x.xxx.xxx  (run, missing slots 2, 4, 8)
   ?closing-fill    [1,6]                         .x....x.....
```

`?` marks the unvoted sections. `break-1` and `closing-fill` are cases where the
pipeline currently has **more** than the truth, so they want a listen rather than
a keyword.

## Measured and rejected — a boundary hi-hat rule

Ten of the leftover notes are an open hat or a foot splash near a section change:
`hihat_open` at bars 13.4, 17.3, 24.2.667, 32.1, 44.2.667 and 54.3, and
`hihat_pedal` at 17.4, 39.4, 40.1 and 40.2. The obvious move is to copy
`restore.crash_candidates`, which makes exactly this argument for cymbals — what
separates a boundary crash from a wash is musical position, not spectrum.

**It does not transfer.** Two of the ten (24.2.667 and 44.2.667) sit **16 and 26
beats** from the nearest change, so no boundary rule reaches them at all. For the
other eight, every formulation tried:

| rule | candidates | right |
|---|---:|---:|
| the last hat within 1 beat of the start | 9 | 0 |
| the last hat within 2 beats | 14 | 4 |
| every hat within 1 beat | 14 | 0 |
| every hat within 2 beats | 43 | 5 |
| every hat within 3 beats | 69 | 7 |
| every hat within 2 beats, v ≥ 83 | 18 | 5 |

The best is 18 candidates for 5 right — 28% — and that velocity gate is fitted to
five hits: the boundary hats that really are open run v83–97 against v45–121 for
the 38 that are not, so the distributions overlap almost completely.
`CRASH_CANDIDATE_VELOCITY` had a 23 dB decay separation behind it *and* nine of
ten at v83+. There is no acoustic story here at all.

And the cost of shipping it is known rather than hypothetical.
`restore.NOTICEABLE` exists because the first Manlio ledger listed 204 items of
which about 170 were single hi-hats, burying the 6 crashes and 25 toms a listener
actually notices. An 18-item hi-hat checklist at 28% precision puts them back.

What is real is the **observation**, not the rule: eight of the ten are in the
last bar before a change, so that bar is worth a listen. A hat somebody hears
there is an ordinary `swap-hit` note in the review console, which already handles
it well.

## Do not try these again

Each was measured. The numbers are above or in the named function's docstring.

* **Do not retune `consolidate.threshold` or `unit_bars`.** Twenty settings were
  swept; the shipping 0.55/auto is the best of them.
* **Do not lower the hi-hat detection delta** to recover the missing hats.
  `transcribe.bands_with_hat_delta` records that the marginal strokes arrive at
  about half the precision of the ones already found.
* **Do not add an audio pass that re-interrogates the empty grid slots.** The
  hat-stem flux probe above is that idea, and in `chorus-1` there is nothing in
  the stem to find.
* **Do not fill hat holes wherever occupancy looks high.** At ≥ 9 of 12 slots it
  is 47% precise — 88 hats added, 47 phantom. The only safe threshold is ≥ 10 of
  12, which fires on `verse-1` alone and is subsumed by `hats: run`. If it is
  ever wanted as a belt-and-braces default it must be ≥ 10 and off by default.
* **Do not add acoustic tom-pitch classification.** The fundamental of the toms
  stem at the declared `tom_low`, `tom_low_mid` and `tom_mid` hits is a median of
  106.7 Hz for all three, and the 53 Hz readings are bass bleed rather than a
  floor tom.
* **Do not try to detect crash vs open hat, or `crash` vs `crash_2`,
  spectrally.** Four attempts are on record in `transcribe.split_cymbal_runs` and
  `restore.crash_candidates`.
* **Do not build a boundary hi-hat candidate rule.** The table above.
* **Do not make `drop_hats_on_sidesticks` stop dropping.** The phantom v122
  accent it removes is real; `hats:` puts the hat back afterwards, deliberately,
  at a velocity somebody chose.

## Divergences from the plan this run was given

The plan in hand carried a few figures that did not reproduce. Recorded so the
next reader trusts the tests over the prose:

* The plan's `stop_beats` sweep gave 24/10/14 at 0.5 and 22/10/12 at 1.0, where
  measurement gives 29/10/19 and 27/10/17. The decisive rows (1.25 and 2.0 at
  11/10/1) reproduce exactly, and so does the conclusion — below 1.25 is worse
  than off — but the plan's table understated how much worse.
* The plan's velocity-floor table gave 10 `hihat_closed` hits where measurement
  gives 11. Every other row, and every phantom count, reproduces exactly.
* The plan expected `hat_candidates` to ship. It does not; see above.
* The plan described the scorer's matching as "tolerance 60 ms". Taken literally
  that scores 163, not the 154 the plan pins as the baseline; the grid-slot form
  reproduces 154 exactly and is what the harness uses.
* The plan's `sections --hats` expected output matched for `verse-1`, `verse-2`,
  `chorus-1` and `verse-3-lift` and differed for `break-1` and `closing-fill` —
  the two sections too short to vote on, where occupancy is a poor summary and
  the output now says so.
