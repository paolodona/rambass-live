# Practice tracks

A **side goal**, not part of the show. The gig deliverable is one Reaper session
with click-locked backing tracks; this is a set of MP3s each band member can
learn their part against, at the tempo they will actually play it.

Two deliverables, and they are independent of each other:

| | what it is | who it is for | needs the rebuilt drums? |
|---|---|---|---|
| **1. drums-in-the-mix** | the original album mix with its drums replaced by the new programmed ones | everyone, once per song | **yes** |
| **2. minus-one** | drums + everything except your own part | one per member; two on DG, three on TIF | no — works today |

Deliverable 1 is a **QA instrument first and a practice track second.** A
programmed part soloed against `stems/no_drums.wav` (drums-rebuild.md Stage 10).

**Deliverable 0, and it is built.** `rambass align <song> --fit --warp` fits
`practice/align.yaml` from detected beats and writes
`practice/no_drums-aligned.wav` — the band minus drums, resampled onto the fixed
grid so the new drums can be judged against it. `reaper build` puts it on a muted
**REF aligned** track at the count-in with no anchor shift, because the file is
already on the grid.

Three things that were measured rather than assumed, so nobody re-litigates them:

* **A single offset is not enough.** Manlio drifts -88 to +258 ms across the
  song, so one offset lines up at the start and flams by a quarter second by the
  end — which reads as "the click does not line up with the mix" exactly when
  somebody is trying to judge timing.
* **Anchor every beat, not every bar.** Warping the drum stem and measuring how
  far each backbeat lands from its grid line: p90 **107 ms** for one offset,
  **58 ms** per bar, **38 ms** per beat. A bar of that shuffle is four seconds
  long, so a linear segment across it cannot follow where the take put beats 2
  and 4.
* **The residual is leave-one-out.** Measured against the beats it was fitted
  from, a per-beat map reports 0 ms however bad it is — the first run duly said
  "0 ms worst / 0 ms mean" for 307 anchors. Held out, the same fit reports 70 ms
  worst / 13 ms mean, and the numbers become monotone in resolution: 70/13 per
  beat, 93/20 every two beats, 116/31 per bar.

Only the **source** side is stored, per CLAUDE.md: the target side comes from
`Timeline` at build time, so a BPM edit re-warps instead of silently stopping.
Linear interpolation resamples it, deliberately — the rates are within a few
percent of 1.0, this is a reference for judging timing rather than a deliverable,
and the alternative is a resampling dependency in the core tier.
hides a lot; the same part inside the real song, with the real vocal on top, does
not. Missing crashes, a fill that changed one bar early and a groove that is
subtly wrong in the second half all become obvious. So this is where drum
problems get found, and it should run *before* the base filename is frozen at
Gate A.

Deliverable 2 is the one the band will use every day.

## The rule that protects the gig

**Nothing in this document may become a prerequisite for the show.** Concretely:

* No `rambass practice` command writes to `render/`, or edits any musical field
  in `song.yaml`. Its only tracked output is `practice/align.yaml`.
* Practice stages are **not** added to `manifest.STAGES`. The status board's
  denominators measure the gig, and diluting them with side work makes the
  project look further from playable than it is. `rambass practice status` reads
  the practice directory and reports separately.
* Practice tracks are allowed to be imperfect. A 30 ms alignment error, an
  audible separation artefact and a guitar-minus track that also lost the keys
  are all acceptable here and none of them are acceptable in a base.

## The hard part: the two clocks disagree

The rebuilt drum track sits on a fixed grid — one BPM, from `song.yaml`. The
original mix breathes, because it was played in 2005 without a click. Sum the
two and they drift apart over three minutes.

Something has to bend, and it is **not the drums**: the whole point of the
exercise is to practise against the metronome the gig will use. So the *bed* —
the original mix minus its drums — gets time-warped onto the fixed grid.

### Offset-only alignment is almost never enough

The tempting cheap version is "find the first downbeat, slide the mix, done".
Run the numbers on it:

> A 0.5 BPM difference between the take and the chosen grid is 0.4 % of the
> song's length. Over a 3½-minute song that is **0.85 s** of accumulated
> slip — three and a half eighth notes at 124 BPM.

And 0.5 BPM is the *best* case: it is what `rambass analyze` calls steady. So a
constant rate correction is the floor, not the luxury option.

### Three tiers, and a number that says which one you needed

| tier | correction | when it is enough |
|---|---|---|
| **T0 offset** | slide the bed so its first downbeat lands on bar 1 | essentially never — see above; kept because it is the fallback when anchor detection fails |
| **T1 linear** | offset **plus one constant rate** | the take held a steady tempo that simply was not the round number we picked. Most songs. |
| **T2 piecewise** | a rate per anchor span | the take genuinely breathes — `analyze` reported several BPM of drift |

`rambass practice align` picks a tier, and prints the **residual**: the error in
milliseconds at each anchor after warping.

* under **30 ms** — good; nobody will feel it
* 30–50 ms — playable, sounds slightly loose
* over **50 ms** — do not ship it; go up a tier, or fix the anchors

**Why piecewise does not accumulate.** This is the whole point of T2 and it is
worth stating as a property rather than a hope: each span is pinned at *both*
ends, so the error is **reset to zero at every anchor**. It is bounded by one
span's worth of wobble, not by the song's length. A T1 linear fit lets a small
rate error integrate over three minutes into most of an eighth note; T2 cannot
do that, because bar 97 is nailed to a measured time in the original file no
matter what happened in bars 1–96. Drift stops being a function of duration.

Anchor spacing for T2 follows from the same arithmetic. Anchors pin both ends of
each span, so the worst error is in the middle of a span and is a fraction of
what the span accumulates: at 124 BPM, 1 BPM of local wobble costs ~60 ms over
eight bars and ~30 ms over four. **Four bars for a drifting song, eight for a
steady one.** Per-bar anchoring is not more accurate in any way that matters and
it multiplies the number of seams.

**Put an anchor on every section boundary**, on top of the regular spacing. A
band without a click pushes or drops time exactly where the arrangement changes —
into the chorus, out of the bridge — so a boundary is both the most likely place
for a step change in tempo and the place a fixed 4-bar grid is most likely to
straddle one. The sections are already in `song.yaml`, in bars, so this costs
nothing: it is a set union with the regular anchors, and a duplicate collapses.
It also gives the detector a free sanity check — a section boundary whose
detected time is wildly off the interpolated one usually means a miscounted beat
upstream, which is the failure mode worth catching.

### The alignment map, and why it is in seconds

`practice/align.yaml` maps **bars to seconds in the original recording**:

```yaml
# 03-tutti-in-fila — anchors measured against source/03 Tutti In Fila.wav
source: "03 Tutti In Fila.wav"
detected_bpm: 123.4
mode: piecewise          # offset | linear | piecewise
anchors:                 # bar -> seconds into the ORIGINAL file
  - {bar: 1,  at: 0.482}
  - {bar: 9,  at: 16.031}
  - {bar: 17, at: 31.598}
residual_ms: {max: 21, mean: 9}
```

This is the project's **second deliberate exception to "positions are in bars,
never seconds"**, and it is the same exception as `lyrics.srt`: the seconds
describe a fixed, immutable audio file — a performance transcription — not a
position in the song's musical grid.

The invariant is still intact, and this is the part an implementation must not
get wrong: **only the source side of the map is in seconds.** The target side is
never stored. It is computed from `Timeline` at build time, so changing a song's
BPM re-warps the practice tracks correctly along with everything else. Store
target times in this file and you have broken tempo changes in a way that will
not surface until someone edits a BPM.

### Bonus: this map is the tempo map the drum rebuild is missing

`docs/drums-rebuild.md` Stage 3 wants to *follow* the take with a tempo map,
quantise against the bending grid, then *flatten* it — and says the missing piece
is "a command to write a tempo map from detected beat times". Bar-to-seconds
anchors are exactly that input.

Keep the bridge **explicit and opt-in** (a separate command, later — writing
`tempo.changes` is editing the gig's musical truth from a side-goal tool). But it
means the alignment work pays a dividend back to the main job rather than only
consuming time.

## Deliverable 1 — drums in the mix

```
new drums (render, fixed grid)  +  warp(album mix − album drums)  =  practice/<slug>-full.mp3
```

Notes that matter:

* **Same head as the gig track.** The practice file starts with the same
  count-in (`render/sticks.wav`) at the same offset as the Reaper region, so a
  player who learns "the vocal enters four bars in" is right on stage. Getting
  this wrong is the `bar_beat_to_seconds` / `audio_time` confusion from
  CLAUDE.md, arriving through a new door.
* **Click optional** (`--click`), mixed low, never baked in by default.
* The new drums go in at the level they sit at in the base — this is a rehearsal
  reference, not a mix.

### When it cannot be made to work

If a song's residual stays over 50 ms at T2, stop rather than shipping a smeared
mix. The fallback is not "ship it un-warped" — an un-warped sum is worse than
useless, it teaches the wrong groove. The fallback is the **gig track plus
click**: the actual backing track, the actual count-in, a click, and no original
mix at all. It needs zero alignment, and for a Tier C song it is arguably the
more honest practice track anyway.

## Deliverable 2 — minus-one tracks

Demucs 4-stem gives `drums`, `bass`, `other`, `vocals`. Because every stem is
warped with the **same** map they stay phase-coherent, so a mix is a plain sum:
one demucs run per song, and then the mixes are cheap.

### The band is three people, and that decides the track list

| member | plays | default track | built from |
|---|---|---|---|
| Domenico "Meco" | vocals | `-minus-vocals` | drums(new) + bass + other |
| Marzio "Maf" | bass guitar | `-minus-bass` | drums(new) + other + vocals |
| Paolo "Vikingo" | guitars | **`-full`** — see below | drums(new) + bass + other + vocals |

Three mixes per song, not five. **Nobody needs `-minus-drums`**: the drums *are*
the backing track, so the nearest thing to a drummer's practice track is the gig
base, which already exists as a deliverable.

`-minus-bass` is the cleanest of the three. Bass is the stem demucs separates
best, and Maf's part appears exactly once in the recording and is played entirely
live — that combination is what makes a separation-based minus-one track
*correct*, not merely useful.

### Vikingo's track is the backing track, and that is the end of it

The album's guitars never have to be preserved for practice — for a different
reason on each album, and neither reason needs any cleverness:

* **Diversamente Giovani** — the backing tracks from the previous gig already
  exist. He practises against one directly. Nothing to separate, nothing to
  reconstruct.
* **Tutti in Fila** — the gig backing track will be **new drums plus guitar
  layers he re-records himself**. The album's guitars are raw material being
  replaced, not something to protect, so removing all of them is fine. Once he
  has recorded the layers, that base is the practice track for everyone.

So `-minus-guitar` (drop the `other` stem) is a perfectly good interim track for
a Tutti in Fila song, and Diversamente Giovani never needs one. There is nothing
to solve here.

### Remaining caveats on the separated stems

* `htdemucs_6s` splits `guitar` and `piano` out of `other`. It leaks noticeably
  more, and given the layering it does not rescue the guitar-minus track — worth
  a try per song if a hollow `-minus-other` is wanted, not worth defaulting to.
* Separation always leaves a ghost of the removed part. For practice that is
  mildly *useful*: a faint reference for where you are supposed to be.

### Alignment is only needed when the drums are swapped

Worth stating loudly, because it takes the hard part off most of the work: a
minus-one track built with the album's **own** drums is a plain re-sum of stems
in their native timing. No grid, no anchors, no warping, nothing from the
alignment machinery above.

Everything in "the two clocks disagree" applies to exactly two things:

1. deliverable 1, the album mix with the rebuilt drums swapped in; and
2. later, a Tutti in Fila practice track that puts the album's **vocal or bass**
   against the new fixed-grid base — those two stems still come from the old
   recording, so they still have to be warped onto the grid.

Nothing else. Diversamente Giovani never needs it at all.

### Run demucs once, in 4-stem mode

The gig pipeline uses `rambass stems --drums-only` because two-stem mode is much
faster and the base only needs `drums` and `no_drums`. Practice needs all four.

A 4-stem run is a strict superset: `no_drums = bass + other + vocals`. So for any
song where practice tracks are wanted, **run 4-stem and derive the gig's
`no_drums.wav` from it** rather than running demucs twice. `htdemucs_ft` stays
the model of choice; the drums stem is identical quality either way.

### The roster lives in config

Which parts exist, and which stems each one is made of, is band configuration
rather than something to hard-code:

```yaml
# config/band.yaml
members:
  - {name: "Domenico",  nickname: "Meco",    part: vocals, track: minus-vocals}
  - {name: "Marzio",    nickname: "Maf",     part: bass,   track: minus-bass}
  - {name: "Paolo",     nickname: "Vikingo", part: guitar, track: full}
```

`members` is what `rambass practice pack` uses to build one folder per person,
and `track` is the default for that member rather than a fixed rule — Vikingo
gets `full` for the reason above, and switches to `base` per song as each base
lands.

## What each album needs

The two albums are barely the same job.

### Diversamente Giovani — separate two stems, warp nothing

Both the **master mixes** (all instruments) and the **backing tracks from the
previous gig** already exist. That is enough on its own:

| member | track | how it is built |
|---|---|---|
| Meco | `-minus-vocals` | master mix − `vocals` stem |
| Maf | `-minus-bass` | master mix − `bass` stem |
| Vikingo | the previous gig's backing track | nothing to build |

**Only `vocals` and `bass` ever need separating**, the album's own drums stay
where they are, and no file is time-warped. One demucs pass over the masters and
this album is done — an afternoon, not a phase.

> **Scope note:** this covers the **seven** DG songs that have a base from the
> previous gig. The six Tier B songs do not have one, so Vikingo has nothing to
> practise against there until their base is mixed — `-full` off the master mix
> is the interim. Meco's and Maf's tracks work on all thirteen either way, since
> they come from the master mix and not from the base.

### Tutti in Fila — two stages

**Stage 1, available as soon as the stems exist.** The drum pipeline runs a
4-stem separation anyway, so all three tracks come free, built with the album's
own drums and therefore needing no alignment: `-minus-vocals`, `-minus-bass`,
`-minus-guitar`. Good enough to learn parts from on day one.

**Stage 2, once the drums are rebuilt and the guitar layers re-recorded.** The
gig base now exists, so Vikingo practises against it like on the other album.
Meco and Maf are the only remaining warp: their parts still come from the album
recording and have to be pulled onto the fixed grid.

### The two files a Diversamente Giovani song has to keep apart

Two different mixes now live under one song, and `song.yaml` already has a slot
for each: `source.audio` is the full master mix in `source/`, `source.backing_track`
is the base. Fill in **both**, explicitly.

That last word matters. `Song.source_path()` falls back to globbing `source/` and
taking the first audio file alphabetically when `source.audio` is empty — which
is harmless with one file in there and silently wrong with two. Separate the gig
base when you meant the master and you get a minus-vocals track with no guitars
in it, and nothing anywhere will complain.

Cheap fix, worth doing as part of Phase 1: have `rambass check` fail a song that
has more than one audio file in `source/` and no explicit `source.audio`. That is
a check, not a schema change — the manifest already models both files.

**A cappella songs** (La Canzone Del Tonno, Se Sei Felice) get nothing but a
reference recording if one exists; there is no track to minus. **Excluded songs**
get nothing.

## Why deliverable 2 can start now

Nothing in deliverable 2 waits on the drum work. Built with the album's own
drums it is a plain re-sum of stems, so the band can be practising every song in
the set before a single drum part is rebuilt.

```
rambass practice build <song> --drums original   # available today, no alignment
rambass practice build <song>                    # rebuilt drums, when they exist
```

Diversamente Giovani never needs the second line — its drums are not being
rebuilt, so the first pass *is* the finished track. Tutti in Fila gets re-rendered
per song as its drums and re-recorded guitars land.

## Where it goes in the code

One new module, `practice.py`, at the cheap tier:

| module | job | deps |
|---|---|---|
| `practice.py` | alignment maps, warp plans, minus-one mixing, packs | numpy + ffmpeg (via `audio.py`) |

Anchor *detection* needs beat times, so it calls into `analyze.py` (librosa,
optional extra) — but only in `practice align`. Once `align.yaml` exists,
building and rebuilding every practice track needs nothing heavier than ffmpeg.
Same layering rule as everywhere else: no librosa import at module top level.

Time-warping goes through ffmpeg. `rubberband` handles transients better and is
the first choice; `atempo` is the fallback and is fine at these ratios. Neither
changes pitch — `asetrate` would, and must not be used. `rambass doctor` should
probe for the rubberband filter (`ffmpeg -filters`) since it depends on how
ffmpeg was built. Put T2's seams **on downbeats**, where the transient masks the
splice, and crossfade them.

### CLI surface

```
rambass practice align <song>                     # detect anchors -> practice/align.yaml + residual report
rambass practice align <song> --anchor 1=0.482 --anchor 33=61.9 --spacing 4
rambass practice build <song> [--for meco,maf,vikingo] [--drums original|rebuilt] [--click]
rambass practice build <song> --from base       # base + click: no separation, no warping
rambass practice pack gig --for maf                # numbered MP3s across a setlist, one folder per member
rambass practice status
```

### Files

```
songs/<album>/<NN>-<slug>/
  practice/
    align.yaml                  ← tracked. the only tracked output.
    <slug>-full.mp3             ← gitignored (*.mp3 already covers it)
    <slug>-minus-guitar.mp3
    ...
practice/pack/<member>/01-<slug>.mp3   ← gitignored; this is what goes to Drive
```

`practice` joins `SONG_SUBDIRS`. MP3s are already ignored by extension, so a
tracked `align.yaml` can sit in the same folder without a `.gitignore` change.

MP3 for phones; one loudness target for the whole set (`loudnorm=I=-16:TP=-1.5`,
single pass — it is practice), which is deliberately *not* the gig's target.

**Disk:** four 48 kHz stereo stems of a 3½-minute song is roughly 240 MB, so a
20-song album of 4-stem separations is ~5 GB. Delete the stems once the packs are
built, or keep them as FLAC.

### Testing

Everything worth testing is arithmetic and runs without ffmpeg, librosa or
demucs:

* anchors → warp plan: segment boundaries, rates, monotonicity
* residual computation, and the tier verdict at the 30/50 ms thresholds
* target times come from `Timeline`, so changing BPM changes the plan — the
  regression test for the invariant
* the stem → part matrix resolved from `config/band.yaml`
* pack naming and setlist ordering

The ffmpeg render itself gets skipped, like every other audio test here.

## Plan

The roster question is answered and alignment is no longer on the critical path,
so the only ordering that matters is Phase 1 first.

| phase | what | blocked by | cost |
|---|---|---|---|
| **0** | ~~roster~~ **done** — Meco/vocals, Maf/bass, Vikingo/guitars | — | — |
| **1** | `practice.py` + CLI + tests: stem mixing, packs, the `source/` check. **No alignment.** | — | ~half a day |
| **2** | **Diversamente Giovani, all of it** — separate `vocals` + `bass` from the masters, build two tracks per song, point Vikingo at the existing bases | Phase 1 | an afternoon |
| **3** | **Tutti in Fila stage 1** — three tracks per song off the 4-stem run the drum pipeline needs anyway, album drums, no warping | Phase 1 + demucs | ~10 min/song |
| **4** | alignment: `practice align`, the three tiers, the residual report, warping | — (parallel with 2 and 3) | ~half a day |
| **5** | deliverable 1 per Tier C song as its drums land; then TIF stage 2 once the guitar layers are recorded | Phase 4 + that song's drums | ~30 min/song |

**Gate P1 ✅** — `rambass practice build` produces a minus-one MP3 for one DG
song that starts at the same offset as its Reaper region, `practice pack` lays
out a member folder in setlist order, and `rambass check` catches a song with two
audio files in `source/` and no explicit `source.audio`.

**Gate P2 ✅** — every Diversamente Giovani song in the set has Meco's and Maf's
tracks, and Vikingo has a folder of the existing bases. This album is finished.

**Gate P3 ✅** — every Tutti in Fila song in the set has all three tracks, built
with album drums. The band can now rehearse the whole set.

**Gate P4 ✅** — `practice align` on one Tier C song reports max residual under
30 ms and the warped bed sits convincingly against the click by ear, end to end.

**Gate P5 ✅** — for each Tier C song whose drums are done: the drums-in-the-mix
track has been listened to end to end, and the drum problems it found are either
fixed or written into that song's `notes:`.

Phase 5 is the one that feeds back into the gig, so schedule it *before* Gate A
freezes a song's base filename, not after. Phases 2 and 3 are the ones the band
actually feels, and neither needs a line of alignment code.

## Honest limits

* **A miscounted beat is the failure mode to fear.** A beat tracker that drops or
  doubles one beat shifts every later anchor by a whole beat, and the result is
  confidently, catastrophically wrong rather than slightly off. Mitigations:
  sparse anchors, a plausibility check that rejects a local tempo outside about
  ±15 % of nominal, `--anchor` to pin one by hand, and always audition before
  shipping. Do not trust the residual number on its own — it can be small and
  wrong.
* **Piecewise stretching leaves seams.** On downbeats, crossfaded, at these
  ratios they are hard to hear. On a sparse intro they will not be.
* **Alignment is the only genuinely hard part, and it now touches two things
  only:** deliverable 1, and Tutti in Fila's vocal and bass stems once the base
  is on the fixed grid. If it turns out badly, everything in Phases 2 and 3
  still stands.
* `-minus-vocals` takes the backing vocals with it, since the `vocals` stem does
  not distinguish lead from backing. Minor here; it matters only if Meco is
  relying on hearing them.
* `-minus-guitar` also removes keys and anything else melodic — it is
  `-minus-other` wearing a friendlier name. Only Tutti in Fila uses it, and only
  until the re-recorded guitar layers exist.
* Separation bleed means no removal is complete. Fine here.
* **Demucs is the wall-clock cost of the whole side goal**, not the code:
  `htdemucs_ft` 4-stem is roughly four times slower than plain `htdemucs`, on
  ~20 songs. Batch it overnight; nothing else in this plan waits on it.
