# Practice tracks

A **side goal**, not part of the show. The gig deliverable is one Reaper session
with click-locked backing tracks; this is a set of MP3s each band member can
learn their part against, at the tempo they will actually play it.

Two deliverables, and they are independent of each other:

| | what it is | who it is for | needs the rebuilt drums? |
|---|---|---|---|
| **1. drums-in-the-mix** | the original album mix with its drums replaced by the new programmed ones | everyone, once per song | **yes** |
| **2. minus-one** | drums + everything except your own part | one per member — three of them | no — works today |

Deliverable 1 is a **QA instrument first and a practice track second.** A
programmed part soloed against `stems/no_drums.wav` (drums-rebuild.md Stage 10)
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

### Vikingo does not want a minus-guitar track

The guitars are layered on the recordings, and the extra layers will be **in the
gig backing track**. So dropping the `other` stem removes both the part he plays
live *and* the layers he will hear through the PA. That is wrong in the one way
that matters: it teaches an arrangement the gig will not have.

Separation cannot fix it. No model splits guitar layer one from guitar layer two
— same instrument, recorded twice.

What he actually wants is **the gig base plus the other two members' parts**, and
for any song that has a base that is not an approximation at all. The base *is*
"album mix minus what the band plays live", so `base + click` is literally what
he will hear on stage: perfectly aligned, no warping, no separation artefact
anywhere in it. It is **free for the seven Tier A songs whose base already
exists**, and it arrives with every later base for nothing.

Until a song has a base, `-full` is the right default — every layer present, his
own part audible as the reference, which is what a guitarist learning a layered
arrangement wants anyway. `-minus-other` stays available (`--parts guitar`) for
learning a part cold, labelled as what it really is: everything melodic gone,
keys included.

### The rule this is an instance of

A separation-based minus-one track is exactly right only for a part that appears
**once** in the recording and is played **entirely live**.

* **Bass** passes.
* **Guitar** fails, on the layers.
* **Lead vocal** passes only if nobody else sings. The `vocals` stem takes
  backing vocals with it, so if the base carries backing vocals live, then
  `-minus-vocals` has removed something Meco will hear on stage — the same
  mistake as Vikingo's, one layer thinner. **Open question: who sings backing
  vocals live?**

Where a part fails the test the answer is the same every time, and it is the
cheaper one: **use the base, not a separation.** Separation is the fallback for
songs that do not have a base yet.

### Remaining caveats on the separated stems

* `htdemucs_6s` splits `guitar` and `piano` out of `other`. It leaks noticeably
  more, and given the layering it does not rescue the guitar-minus track — worth
  a try per song if a hollow `-minus-other` is wanted, not worth defaulting to.
* Separation always leaves a ghost of the removed part. For practice that is
  mildly *useful*: a faint reference for where you are supposed to be.

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

The two albums differ here even more than they do for the gig.

| | Tutti in Fila | Diversamente Giovani |
|---|---|---|
| deliverable 1 | the whole job — separate, align, warp, sum | **not applicable** — the base *is* the drums; there are no rebuilt drums to swap in |
| deliverable 2 | separate the album mix, warp, sum | separate the **finished base** and re-sum |
| alignment needed | yes | **none** — the base is already the gig track, so the stems are aligned by construction |
| cost per song | ~30 min attended, plus an unattended demucs run | ~10 min |

Diversamente Giovani is therefore the cheap half and can go first: no alignment
code is needed for it at all.

One useful side effect for that album — a DG base gets a `click.wav` generated
from its fixed `song.yaml` BPM, and building the practice track is the moment you
find out whether the base is actually click-locked. That is a gig question the
practice pipeline surfaces early and for free.

**A cappella songs** (La Canzone Del Tonno, Se Sei Felice) get nothing but a
reference recording if one exists; there is no track to minus. **Excluded songs**
get nothing.

## Two passes, and why deliverable 2 can start now

Deliverable 2 does not need the rebuilt drums. Run it with the **original** drum
stem and the band can practise every song in the set before any drum work lands
— no alignment, no warping, no dependency on Lane A.

```
rambass practice build <song> --drums original   # pass 1: available today
rambass practice build <song>                    # pass 2: rebuilt drums, default when they exist
```

Pass 1 is the schedule win: 23 songs of minus-one tracks, this week, from the
MP3s that already exist. Pass 2 re-renders each song as its drums land. Nobody
waits for anybody.

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

Four phases. Phase 0 is answered, so Phase 1 can start now, and Phase 3
(alignment) runs in parallel with Phase 2 rather than gating it.

| phase | what | blocked by | cost |
|---|---|---|---|
| **0** | ~~`config/band.yaml`~~ **done** — Meco/vocals, Maf/bass, Vikingo/guitars | — | — |
| **1** | `practice.py` + CLI + tests: minus-one mixing, packs, **no alignment** | — | ~half a day |
| **2** | pass 1 across the whole set — DG bases and TIF album mixes, original drums | Phase 1 + demucs runs | ~10 min/song attended |
| **3** | alignment: `practice align`, the three tiers, the residual report, warping | — (parallel with 2) | ~half a day |
| **4** | deliverable 1 per Tier C song, as its drums land | Phase 3 + that song's drums | ~30 min/song |

**Gate P1 ✅** — `rambass practice build` produces a minus-one MP3 for one DG
song that starts at the same offset as its Reaper region, and `practice pack`
lays out a member folder in setlist order.

**Gate P2 ✅** — every song in the set has its three tracks (`-minus-vocals`,
`-minus-bass`, `-full`) in a per-member folder on Drive, built with whatever
drums exist today. Vikingo's folder prefers `base + click` for every song that
has a base.

**Gate P3 ✅** — `practice align` on one Tier C song reports max residual under
30 ms and the warped bed sits convincingly against the click by ear, end to end.

**Gate P4 ✅** — for each Tier C song whose drums are done: the drums-in-the-mix
track has been listened to end to end, and the drum problems it found are either
fixed or written into that song's `notes:`.

Phase 4 is the one that feeds back into the gig, so schedule it *before* Gate A
freezes a song's base filename, not after.

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
* **`-minus-guitar` is the wrong deliverable, not just an imperfect one** — the
  layered guitars belong to the backing track. Vikingo gets `-full`, and `base +
  click` wherever a base exists. See above.
* `-minus-vocals` takes the backing vocals with it. Whether that matters depends
  on who sings them live — still open.
* Separation bleed means no removal is complete. Fine here.
* **Demucs is the wall-clock cost of the whole side goal**, not the code:
  `htdemucs_ft` 4-stem is roughly four times slower than plain `htdemucs`, on
  ~20 songs. Batch it overnight; nothing else in this plan waits on it.
