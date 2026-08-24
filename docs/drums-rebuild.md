# Rebuilding a drum part to professional standard

**This is a Tutti in Fila document**, and it is the long version of
[drums.md](drums.md). Diversamente Giovani skips all of it — those songs are
`drums.origin: backing-track` and their drums already exist inside a finished
base.

[drums.md](drums.md) documents what `rambass drums transcribe` and `rambass
drums clean` actually do today. This document is about the job those two
commands are one part of: turning a stereo mix from 2005 into a drum track that
sounds like it was programmed on purpose, locks to a click, and holds up through
a PA at a gig.

The two are not in conflict. Everything here still ends at `rambass drums
clean`, still stores positions in bars, and still writes GM-mapped MIDI. What it
adds is the three stages the current pipeline has no answer for: getting a
detector that can see toms, deciding the grid before quantising to it, and
making each section internally consistent.

## The goal, stated precisely

Not "a transcription of what the drummer played". The target is:

* the **same arrangement** — same section lengths, same fills in the same bars,
  same crashes on the same downbeats, because that is what the band has in
  muscle memory;
* the **same kick pattern**, because that is what locks to the bass and the
  riff;
* **consistent** within a section — bar 11 and bar 13 of the same verse are
  identical unless the difference was deliberate;
* **on a fixed grid**, because the click has to be playable;
* **dynamic**, because a flat part sounds like a drum machine and the audience
  can tell;
* **loud and legible on a bad PA**, which is a mixing problem, not a MIDI
  problem.

Fidelity to the original hi-hat pattern is worth very little. Fidelity to the
kick pattern and the crash placement is worth almost everything. Budget your
attention accordingly.

## What this adds to the pipeline in drums.md

| stage | drums.md today | here |
|---|---|---|
| separate | demucs, drums vs. no_drums | plus a second split into five part stems |
| tempo | pick one BPM, or bend the grid | bend the grid, quantise, *then* flatten it |
| transcribe | 3 frequency bands over the drum mix | a detector per part stem, or a learned model |
| clean | de-flam, quantise, velocities, humanise | unchanged — this part is good |
| sections | markers in Reaper | consolidate each section to one pattern |
| articulation | whatever the detector said | a section can *declare* its backbeat |
| fills | "fix them by hand" | list them, then declare them in `song.yaml` |
| kit | "load your drum VST" | which kit, and how it has to be mixed |
| render | render the base | plus a loudness target shared across the set |

Stages 2, 3, 6 and 7 are the ones that change the result. The rest is detail.

**Where the repo is, as of 2026-08-23.** Stages 0-7 are commands. Stage 2's split
itself is still an external tool, but the repo notices its output and reads it.
Stage 8 (kit) and 9 (render) are the open work.

| stage | command |
|---|---|
| 1 separate | `rambass stems` |
| 2 part stems | external, then read automatically from `stems/parts/` |
| 3 tempo | `rambass analyze`, `rambass align --fit` |
| 4 transcribe | `rambass drums transcribe` |
| 5 clean | `rambass drums clean` |
| 6 consolidate | `rambass section`, `rambass sections`, `rambass drums consolidate` |
| 7 restore | `rambass drums missing`, `rambass drums restore` |
| — check | `rambass stale` — is anything on disk out of date? |

## Stage 0 — get the right source file

From `songs/tutti-in-fila/sources.yaml`, download the **WAV**, not the MP3, into
the song's `source/` folder under the filename `source.audio` names. Separation
quality sets a hard ceiling on everything downstream and it is measurably worse
off a 2005 MP3. `rambass check` reports what is still missing.

Track 10 is the exception — it is MP3-only in Drive. Expect the worst result of
the album there and budget accordingly.

## Stage 1 — separate the kit out of the mix

```
rambass stems <song> --drums-only
```

`--drums-only` is two-stem mode: faster, and it also writes `no_drums.wav`,
which is the band-minus-drums bed. **Keep it.** It is the single most useful
file in the whole process and Stage 10 is built on it.

`--model htdemucs_ft` is the default and is worth its extra time on cymbals. On
Apple Silicon add `--device mps`. If the drum stem still sounds smeared —
cymbals swimming, kick hollow — a BS-Roformer-class model through MVSEP or
Moises will beat htdemucs_ft on drums specifically. That is a manual detour, not
something the CLI does.

## Stage 2 — split the kit into part stems

This stage does not exist in the repo yet and it is the highest-value addition
to the whole pipeline.

`rambass drums transcribe` runs onset detection in three fixed frequency bands
over the whole drum mix. [drums.md](drums.md) is honest about the consequences:
no band exists for toms, so a floor tom reads as a kick or a snare; ride and hat
are both short and high, so they are indistinguishable; anything under a ringing
crash is invisible. Those are not tuning problems, they are consequences of the
input being a mixture.

Give the detector one instrument per file and they mostly evaporate. Tools that
do it:

| tool | notes |
|---|---|
| [LarsNet](https://github.com/polimi-ispl/larsnet) | open source, local, faster than realtime; kick / snare / toms / hi-hat / cymbals |
| `drumsep` | demucs-based, same five-way split |
| Moises Pro | hosted, drum-part stems on the paid tier; easiest if you do not want a Python environment |

This is not a hunch. "Enhanced Automatic Drum Transcription via Drum Stem Source
Separation" ([arXiv 2509.24853](https://arxiv.org/html/2509.24853)) measures
exactly this pipeline and reports 8-class F-measure of 0.84 against a 0.72
baseline on MDB, and 0.76 against 0.65 on ENST.

Two things worth noticing in that paper, because they say something about this
repo: its cymbal handling is a refraction period after a crash, and its
open-versus-closed hat test is loudness decay over a ~150 ms window. Those are
`CRASH_SUSTAIN_RATIO` and `OPEN_HAT_SUSTAIN_RATIO` in `transcribe.py`. The
heuristics here are the right ones. They are just pointed at a mixture instead
of a stem.

Until there is a `rambass` stage for this, run the separator by hand and feed
each stem in with `rambass drums transcribe --file <stem>`, then merge. Tune the
band for each: a kick stem wants the kick band and nothing else.

## Stage 3 — tempo: follow the take, then flatten it

[workflow.md](workflow.md) presents this as a choice — bend the grid with a
tempo map to keep the feel, or fix one BPM and accept that the original audio
slides out of sync as a reference. For a gig the second is obviously right, and
`--strength 1.0` is the correct quantise setting.

But the choice is a false one, and the repo's own invariant is what dissolves
it. Positions are stored in **bars**, so a grid can be changed after the fact
without moving anything musically. That means:

1. **Follow.** Build a tempo map from the beat times `rambass analyze` detects,
   so the grid bends with the take.
2. **Quantise against the bending grid.** Every hit is now within a few
   milliseconds of a line, so quantisation is unambiguous — a hit lands in the
   slot it was played in, not the neighbouring one the band drifted into.
3. **Flatten.** Discard the tempo map and set one fixed BPM. Because positions
   are musical, everything re-renders perfectly straight.

Quantising straight to a fixed grid, without step 1, silently mis-slots hits
wherever the take has drifted more than half a subdivision — and a three-minute
take from 2005 will have. That failure does not show up in the report as a large
mean move, because each individual hit was snapped to *something* nearby. It
shows up as a groove that is subtly wrong in the second half of the song.

`rambass drums import --retempo` already performs step 3's motion. What is
missing is a command to write a tempo map from detected beat times — until then,
enter the tempo changes in `song.yaml` by hand from the `analyze` output, which
prints a BPM per 20-second window.

**Side benefit worth the effort on its own:** that same bending map, used in a
throwaway Reaper project, lets you time-warp the drum stem onto the straight
grid. Without it, the reference audio and the MIDI grid disagree more and more
as the song goes on, and hand-editing fills past the first thirty seconds
becomes guesswork.

## Stage 4 — transcribe

In order of preference:

* **Superior Drummer 3 Tracker**, if SD3 is available. It analyses audio to MIDI
  natively, reads dynamics into velocity, and detects ghost notes. One box, and
  it removes Stages 2 and 4 both.
* **[ADTOF](https://github.com/MZehren/ADTOF)** — a learned drum transcriber
  trained on real rock, MIDI out with velocity. `ADTOF-pytorch` needs only
  torch, no TensorFlow or madmom.
* **`rambass drums transcribe`**, run per part stem from Stage 2.

**EZdrummer 2 and 3 cannot do this.** EZdrummer 3's Bandmate matches *library
grooves* to an audio file you drop in; it does not transcribe what is in the
file. That is a genuinely useful feature — see the alternative at the end — but
it is not audio-to-MIDI and it will not give you the band's part.

Check the hit counts in `--report` before going near the DAW. The rule from
[drums.md](drums.md) stands: 704 hats in a three-minute song is plausible, 4000
is broken detection.

## Stage 5 — clean

```
rambass drums clean <song> --strength 1.0 --deflam-ms 25
```

This part of the pipeline is good and does not need changing. Two cautions.

**Do not use `--accent-kick` / `--accent-snare` as a matter of course.** They
replace every hit's velocity with one fixed number.
[drums.md](drums.md) justifies that because band flux over a drum *mixture* is
only loosely related to how hard the drummer hit — which is true. But onset
energy measured on an isolated snare stem is a decent proxy, and after Stage 2
you have one. Flattening is a workaround for a bad measurement; once the
measurement is good, flattening throws away the ghost notes, the hat accent
pattern and the push into the chorus. Those are the dynamics you are trying to
keep. Normalise per instrument, shape the curve, but keep the contour.

**`--humanize` is solving a smaller problem than it looks.** ±4 ms of timing
jitter is close to inaudible. What actually makes a sampled kit sound fake is:

* **identical velocities**, because velocity is what selects EZdrummer's
  round-robins and layers — repeat the same number and you re-trigger the same
  sample, which is the machine-gun effect people blame on quantising;
* **wrong articulations** — everything on a closed hat, the hat never opening
  under a crash, no pedal hat;
* **physically impossible parts** — two toms 30 ms apart that need three hands,
  hats continuing through a crash on the same arm. Nothing in the repo detects
  this and a listener catches it immediately.

Velocity spread is the humaniser that matters. Check limb plausibility by eye in
the MIDI editor.

### The articulation a detector cannot hear

`drums clean` also applies each section's declared `backbeat`, and this is the
one place the pipeline is told something rather than measuring it.

Manlio's three verses play beats 2 and 4 **side-stick** — the stick laid across
the rim, tip on the head — and the lifts and choruses play them on the head. The
detector gets verse-1 and verse-3 on its own: a rim click never reaches the
shell, so it has no body, and because it is nothing but a high-frequency
transient the separator hands most of it to the hi-hat. Measured on the kit mix,
the 180-500 Hz share is 0.08-0.10 at a verse backbeat against 0.40-0.48 at a real
one, with the snare stem 14-19 dB *quieter* than the hat stem instead of 16-31 dB
louder.

It does not get verse-2, and the reason is not a badly tuned constant. Across all
941 hi-hat and cymbal stem detections the hat stem's 5-11 kHz share is 0.17 at a
verse backbeat and 0.85-0.96 everywhere else — a clean split. In verse-2 the same
clicks read **0.84-1.00**, which is a hi-hat's own spectrum. Paolo: *"some sound
different than others but they are all the same concept: side stick rather than
standard snares."* A quiet cross-stick under a hi-hat leaves the separator
nothing to hand over, and no threshold recovers it without eating the real hat
part.

So it is declared:

```yaml
sections:
- name: verse-2
  bar: 20
  beat: 3.0
  backbeat: sidestick
```

That is **arrangement structure, not a tuning knob** — the same category as the
section list itself, or `drums.subdivision: 3`. Settled by ear once, written
down, never re-derived from audio. The distinction matters: CLAUDE.md warns that
a per-song *threshold* override is a smell, and this is not one.

The declared backbeat comes out **even**, at one velocity. The first run showed
why: verse-2's side-sticks were `[45, 45, 45, 60, 118, 118, 121, 122 x9]`, which
is not dynamics but two scales in one section — `scale_velocities` works per
instrument, so a hit detected in the hat stem carries a number meaning "loud for
a hi-hat", and renaming it to a rim click makes that number meaningless. The
reference is the median of the population measured on the right instrument, which
on Manlio is v45. That is the floor and it is correct on this scale: a rim click
here is 25-30 dB below the same drummer's snare and the whole velocity range only
spans 30 dB. How loud it should *sound* is a Stage 8 question about the kit's
rim-click samples, and the answer belongs in the manifest: **`drums.backbeat_velocity`**.
Paolo's verdict on the first pass was "the cross-stick/side-stick are too low in
volume (eg: 24.4) and are barely audible", which is what v45 does under hi-hats at
75-98 and snares at 109. Manlio declares `backbeat_velocity: 96` and its 48
declared clicks are stamped there; `drums clean --backbeat-velocity` overrides it
for one listen, but only the manifest value survives a re-run, and `rambass stale`
flags `drums-quantized.mid` when it changes.

Two rim clicks on Manlio stay at v45 after this, at Reaper 4.3.67 and 66.3 — the
detector found them in `theme-intro` and `break-3`, which declare no backbeat, so
nothing renamed or re-levelled them. If they need to be heard, the honest fixes
are to declare those sections or to put the hit in `drums.additions`; quietly
re-levelling every hit that happens to share the name would be a hand edit
pretending to be a rule.

## Stage 6 — sections, and consolidating each one to a pattern

This is the stage that decides whether the result sounds programmed or
transcribed. It is `rambass drums consolidate`, and `quantize.consolidate` is the
pure function behind it.

Without this stage, two things leak through into the final track. Every
unintended inconsistency in the original take survives quantising, *and* every
transcription error is independent per bar. So bar 11 of the verse loses a hat
and bar 13 gains a phantom tom. On the grid, but incoherent — which is precisely
the texture that reads as a bad transcription.

Mark the sections first:

```
rambass section <song> <bar> <name>
```

(`<song> <bar> <name>`, in that order — there is no `add` subcommand.) Then for
each section:

1. Find the repeating unit — one bar or two.
2. Overlay every repetition of that unit in the section.
3. Keep a hit only where it appears in **at least 50–60%** of the repetitions,
   placed at the modal grid slot, with the median velocity of the group.
4. Stamp the result across the whole section.

What that threshold throws away is exactly the two things you do not want:
unintended variation from the drummer, and transcription errors — neither of
which repeats reliably. What survives is the groove the section is actually
made of.

Then put back, by hand, the events that were deliberate: the crash on the
section's first downbeat, the kick variation in the last bar before the chorus,
the fill at the end of each four- or eight-bar phrase. Those are the things a
listener notices, and they should be there because you decided so, not because
a detector happened to catch them.

No off-the-shelf tool does this, which is why it is here.

Two rules the implementation settled, both from Paolo and both load-bearing:

* **Identical names are one part.** `consolidate` pools every section sharing a
  name into a single vote and stamps the result across all of them, so they come
  out bit-identical. A name *family* is not a pooling key: `verse-1`, `verse-2`
  and `verse-3` vote separately, because they are structurally alike and the
  later ones add hits. Never pool on a prefix.
* **Four repetitions is the minimum.** At n=2 a hit must appear in *both* to
  clear 0.55, which is unanimity rather than agreement — measured on Manlio it
  removed a third to a half of every 2-bar break. Below the minimum a section is
  passed through untouched and reported, which is the right outcome for a break.

Sections need not start on a bar line, and `consolidate` handles that itself.
Manlio's verse-2 runs from bar 20 beat 3 to bar 32 beat 3; the repetitions still
tile the bar grid, but each slot is judged against the repetitions it *could*
have appeared in, so the half-bars at either end are voted on like everything
else.

## Stage 7 — fills, crashes, articulation

```
rambass drums missing <song>     # the checklist
rambass drums restore <song>     # apply what you decided
```

**Do not try to repair transcribed fills.** A three-to-five minute song has
maybe eight to fifteen of them; dense, fast and overlapping is the one case the
detector is worst at, and salvaging its output takes longer than starting over
and sounds worse.

### The checklist

Every omission upstream of here is deliberate, and therefore knowable.
`drums missing` writes `qa/missing-hits.md` from two sources, both computed from
the MIDI and the section list with no audio at all:

* **the consolidate diff** — exact, matched per `(instrument, grid slot)` so that
  a survivor moved to its group's modal slot is not reported as a deletion;
* **section-boundary crash candidates** — see below.

It reports **accents and fills, not the groove**. The first version listed
everything and came out at 204 items for Manlio, about 170 of them single
hi-hats: a hat the vote dropped is Stage 6 working, and putting it on a checklist
is asking somebody to undo it. Filtered and grouped per bar, the same song
reports **35 things to put back**. `--everything` for the full diff.

Bar numbers in the report are **Reaper's**, because the document exists to be
read next to the ruler.

### Why the crashes have to be listed rather than detected

`split_cymbal_runs` calls an ambiguous cymbal an open hi-hat on purpose, so the
crashes that mark section boundaries arrive labelled `hihat_open`. Four separate
attempts to undo that acoustically have failed and are recorded so nobody repeats
them: decay at 200/500/900 ms, spectral centroid of the attack, coincidence with
a hi-hat strike, the cymbal-to-hat level ratio, long sustain at 0.6-1.2 s, and
decay at +250 ms. That last one, measured across the 243 measurable cymbal-stem
hits on Manlio: median **-16.8 dB**, p90 **+3.0 dB**, and **114 of 243** ringing
at or above -14 dB. Long ring is the *normal* case in that stem, because an open
hat's wash lives there too. No threshold separates them.

What does separate is musical position. The ten cymbal hits within 0.6 beat of a
section start decay at a median **+3.4 dB** against **-19.2 dB** for the other
233 — 23 dB apart — and nine of the ten are v83 or louder. So `drums missing`
reports those as crash candidates, and reports the section starts with no cymbal
on them at all (six of Manlio's fifteen, mostly the breaks, where a drummer drops
out rather than accents).

What it will *not* find is a crash played *into* a change rather than on it —
Reaper 19.3 on Manlio is two beats before `break-1`. Widening the window triples
the candidates and starts eating the hat part, so those stay a listening job.

### Record the edits, do not draw them

```yaml
drums:
  additions:
  - {bar: 32, beat: 3.0, instrument: crash, velocity: 105, note: into the chorus}
  removals:
  - {bar: 53, beat: 4.32, instrument: sidestick, note: ghost in the stem}
```

**This is the part that matters.** A crash drawn into the Reaper MIDI item is
gone the next time `drums transcribe` runs, so it gets drawn again every time —
and because it is always redone from scratch there is never a record of how much
of Stage 7 is finished. In `song.yaml` it is bar-anchored, it is in git,
`drums restore` reapplies it, and `drums missing` ticks it off the checklist.

Removals first, so replacing a hit is two lines rather than a puzzle about
whether the addition survived its own removal — and that is the commonest edit
there is, since the open hi-hat on a section start comes out and a crash goes in.
An addition already present is counted, not doubled, so running it twice cannot
build a flam. A removal that matches nothing is *reported*: it means the hit it
named was deleted upstream, and swallowing that hides the drift.

While you are here, fix the other articulations the detector cannot see: open the
hat where the part opens up, put the ride where the ride is, add the pedal hat.
A fill is best played in on pads, drawn in EZdrummer 3 Grid Editor, or pulled out
of the library browser — twenty minutes for the song, and it sounds like a
session player.

## Stage 8 — the kit, and the mix

**Check the kit against the song's `character` block.** Tutti in Fila is tagged
`genre: metal, energy: 5, heaviness: 5`. EZdrummer's core library is a pop/rock
kit and will vanish under distorted guitars. Budget for a heavy EZX. The sample
library matters more to "sounds legit at the gig" than any of the MIDI work
above.

### Kit shortlist — decision still open

An EZX loads in EZdrummer 2 and 3 and in Superior Drummer, so this purchase is
safe whichever way the plugin question goes. Browse the heavy end of the
catalogue at
<https://www.toontrack.com/product-category/ezdrummerline/ezx/>. Candidates
worth auditioning, with what each is:

| EZX | who / where | shape |
|---|---|---|
| [Rock Solid](https://www.toontrack.com/product/rock-solid-ezx/) | Randy Staub, Warehouse Studio Vancouver; 3 kits | classic rock through modern metal — the widest range, so the best single-purchase bet across an album that spans metal to pop |
| [Metal Machine](https://www.toontrack.com/product/metal-machine-ezx/) | John Tempesta, Andy Sneap, Henson; Tama Starclassic Bubinga ×2, Ludwig Stainless Steel | the classic Sneap metal sound; the safe choice for track 03 alone |
| [Metal Mania](https://www.toontrack.com/product/metal-mania-ezx/) | Dirk Verbeuren, Chris Rakestraw, One On One LA; 3 kits, 30+ cymbals | cymbal-rich, useful when the part is busy |
| [Metal!](https://www.toontrack.com/product/metal-ezx/) | Audio Hammer / Daniel Bergstrand; 9 kits, DW Collector + Ludwig Quadra Plus | most kits per euro, so most range within one pack |
| [Modern Metal](https://www.toontrack.com/product/modern-metal-ezx/) | MIDI by Kyle Brownlee | heavy rock through hardcore and death metal |
| [Heavy Rock](https://www.toontrack.com/product/heavy-rock-ezx/) | Jay Ruston, Jeff Friedl; 4 kits plus snares | the least extreme of the group |
| [Post-Metal](https://www.toontrack.com/product/post-metal-ezx/) | Thomas Hedlund, Cult of Luna; 4 kits | atmospheric and slow — wrong for 03, possibly right for 06 |

The thing to weigh: **only one of these six songs is actually metal.** A
single-genre metal EZX nails track 03 and is useless on Manlio. Rock Solid is
the one that claims the whole span, and Metal! buys nine kits inside one pack —
either is a better first purchase than a specialist, unless you are happy buying
two.

Decide it by ear, not by description, and decide it on **Tutti in Fila and
Manlio together** — the two extremes. A kit that handles both handles the album.

**Build a real drum map.** There is no EZdrummer entry in `config/drum-maps/`.
Toontrack's GM Extended map shares its *core* notes with GM, but the extended
articulations do not match, and EZdrummer's core kits usually carry three toms
where `drummap.py` emits five names — `tom_low_mid` (43) will hit the wrong
thing or nothing at all, silently. Read the numbers off EZdrummer's own
Interactive MIDI Layout document (Help menu) into
`config/drum-maps/ezdrummer3.yaml`, the same discipline the `bfd3.yaml` comment
argues for. Then:

```
rambass drums remap <song> ezdrummer3 --set-default
```

Send a single kick and watch which pad lights up before trusting a whole song.
Turn EZdrummer's own quantise and humanise **off** — they will fight the
quantising already in the MIDI.

**Mix it for a stage, not for headphones.** Enable Multi-Out in EZdrummer's
Settings → General; Reaper will create the aux tracks for you, and EZdrummer 3
offers up to 16 stereo outputs. Then:

* cymbals **down** — well below where an album mix would put them. There is no
  drummer on stage making them make sense, and they mask everything through a
  cheap PA;
* kick with an audible beater click, or it disappears in a wedge;
* snare cutting around 200 Hz and 4 kHz;
* **check the whole thing in mono.** Wide stereo overheads and room mics
  partially cancel on a mono PA, which is what a lot of small venues run. This
  is the failure that only shows up at soundcheck.

## Stage 9 — render

Render **DRUMS MIDI alone**, from the `BAR 1` marker, to
`songs/<album>/<slug>/render/<slug>.wav`.

Two traps:

* **Not from 0:00.** `reaper.py` places the base at `timeline.count_in_seconds`,
  so the rendered file must not contain the count-in — otherwise it is offset by
  the count-in twice. If the drum part has a pickup before bar 1 it falls inside
  the count-in and gets cut; decide that deliberately rather than discovering it.
* **Never with the click.** `click.py` opens by saying a click baked into a base
  is unremovable, and it is right. Note that
  [reaper.md](reaper.md) currently contradicts itself here: line 74 says to
  render CLICK + DRUMS MIDI together, line 81 says the click is never mixed into
  the base. Line 81 is correct. Line 74 needs fixing.

Then match loudness across the set. `audio.py` has `measure_loudness` (EBU R128
integrated plus true peak) and nothing calls it. Every backing track in the
running order should land at the same integrated LUFS with the same true-peak
ceiling — −1 dBTP is a safe one — or FOH is riding gain between songs all
night.

## Stage 10 — QA against the band

Play the programmed drums against `stems/no_drums.wav`.

This is what that file is for and it is worth more than any amount of soloed
listening. Missing crashes, wrong fills, a dropped kick variation and a section
that changed one bar early are all inaudible in solo and glaring against the
rest of the band. Do it before every commit of the MIDI, and once more at final
tempo after Stage 3's flatten.

Better still, put the new part inside the **real** song rather than the bed:
[practice-tracks.md](practice-tracks.md) deliverable 1 is this step automated —
the album mix with its drums replaced by yours, which needs the mix time-warped
onto the fixed grid first. Same purpose, with the original vocal and guitars on
top, which is where the remaining problems actually show themselves. Do it
before Gate A freezes the base filename, while fixes are still cheap.

## The faster alternative: map it, don't transcribe it

Worth considering seriously, because it may be both quicker and better.

The transcription's real value is the **arrangement map**, not the note data.
For hats, ride, ghost notes and fills, a professionally played library groove in
the right style — stamped consistently on a fixed grid — will beat a cleaned-up
transcription of a take that was never played to a click.

So: run Stages 0–5 far enough to extract the map (section boundaries in bars,
the kick and snare patterns, where every crash and fill lands), then build the
part in EZdrummer against that map using the groove browser, Bandmate and Song
Creator.

What you get is "same song, same arrangement, same hits in the same places,
played better", which is what a gig needs. Nobody in the audience will notice a
different ride pattern. Everybody notices a fill that lands in the wrong bar.

### Which parts have to be exact

Fidelity is not uniform across the kit, and the line falls in a very convenient
place. **Kick, snare and crash are what the rest of the band plays against** —
the bass locks to the kick and the backbeat, and the crashes are the arrangement
everyone has memorised. Get those three right and nobody can tell the hats came
from a library. Get them wrong and the bass player is fighting the track all
night.

Those are also the three the pipeline extracts *best*: after the Stage 2 split,
kick and snare come off their own stems nearly perfectly, and crashes are loud
and sparse. The parts that are hard to transcribe are exactly the parts where
fidelity does not matter.

| part | fidelity | source | automatable |
|---|---|---|---|
| kick | exact | kick stem | **yes** — highest confidence in the pipeline |
| snare backbeat | exact | snare stem | **yes** |
| snare ghost notes | texture | library groove or hand | no — below detection anyway |
| crash | exact | cymbal stem | **yes** — loud and sparse |
| crash choke / stopped | exact | cymbal stem, short-decay test | **yes** — see below |
| hi-hat / ride pattern | texture | library groove per section | partly — grid occupancy voting |
| toms | texture | library or hand | no |
| fills | position exact, content free | library fill at the mapped bar | position only |

### Stopped crashes need a canonical name that does not exist

`CANONICAL` in `drummap.py` has `crash`, `crash_2`, `china` and `splash`, and no
choke. Toontrack maps cymbal chokes as their own MIDI notes, so without a
`crash_choke` (and probably `china_choke`) in the canonical list *and* in the
EZdrummer map, every choke silently becomes a normal ringing crash. In a metal
arrangement that is the difference between a stab and a wash.

Detecting one needs no new machinery. `_classify_high_band` already measures how
much a high-band hit is still ringing 350 ms after the attack; a choke is
precisely a crash whose decay is abnormally *short*. One more threshold below
`OPEN_HAT_SUSTAIN_RATIO`. Reliable on an isolated cymbal stem, hopeless on a mix.

### The library is plain MIDI on disk, and that changes what can be automated

Toontrack's grooves are ordinary `.mid` files in a folder that EZdrummer 2 and 3
both read:

* macOS — `/Library/Application Support/EZdrummer/Midi`
* Windows — `C:\Program Files (x86)\Common Files\Toontrack\EZDrummer\Midi`

EZdrummer 3 will also browse third-party MIDI from a **linked folder** of plain
`.mid` files. That makes this a two-way street, and it is the whole reason the
alternative can be automated at all.

**Read direction** — the library is a searchable corpus. Score every library
groove against the kick and snare pattern extracted from the stems, per section,
and rank them. `mido` is already a core dependency; this is a scoring function
over note positions, not a research project. The output is "the five library
grooves closest to what the drummer actually played in this verse".

**Write direction** — write the generated per-section patterns into a linked
folder and they appear *inside EZdrummer's own browser*, auditionable against the
kit and draggable onto the Song Track. The pipeline's output arrives where the
work is already happening.

Which splits the job cleanly:

**Automatable** — kick, snare, crash and choke extraction; section boundaries and
the Stage 6 consensus vote; hi-hat grid occupancy (is this section 8ths or 16ths,
does it open on the "and" of 4); library groove matching and ranking per section;
and the hybrid assembly below.

**Not automatable, and should not be** — which fill goes where (the position is
automatic, the choice is yours and costs twenty minutes a song), kit and EZX
choice, the mix, and whether a variation was deliberate.

### The hybrid assembly

This is the move that makes the whole alternative work:

> Take the library groove's hats, ride and ghost notes, then **overwrite its kick
> and snare with the transcribed ones**.

You get the band's actual rhythmic content on the parts the bass locks to, and a
session player's feel on the parts nobody is listening to that closely.

### Doing it in EZdrummer, step by step

The shape is the same in 2 and 3; step 3 is where they diverge.

1. **Load the kit** — the EZX, not the core kit (see Stage 8).
2. **Get the map in.** Drag the generated per-section MIDI onto the **Song
   Track** at the bar positions from `song.yaml`, or drop the files into a linked
   MIDI folder and pull them from the browser.
3. **Find the texture per section.**
   * *EZdrummer 2* — **Tap2Find**: tap the groove's rhythm and it lists the
     closest library matches. Plus the browser's genre and intensity filters.
   * *EZdrummer 3* — drop the **drum stem itself** into **Bandmate** and it
     suggests matching library grooves directly. This is exactly this workflow,
     built into the plugin.
4. **Overwrite kick and snare** with the transcribed ones. EZdrummer 3 does it in
   the **Grid Editor** without leaving the plugin; EZdrummer 2 means dragging the
   block out to Reaper and merging there.
5. **Fills at the mapped bars.** The browser filters for fills; drop one at each
   position the map identified.
6. **Song Creator** assembles the sections into the full arrangement. Both
   versions have it.
7. **Drag the finished Song Track out to Reaper** onto the DRUMS MIDI track.
   Everything downstream in this document is unchanged.

### EZdrummer 2 or 3

**The starting point is EZdrummer 2, owned.** BFD3 — the sampler the band used
for Diversamente Giovani — is not available here; it may be sourceable from
Marzio at some point. If it ever is it reopens a different comparison, because
BFD3 is in Superior Drummer's class rather than EZdrummer's, but it offers
nothing like Bandmate or the Grid Editor and so makes the per-section workflow
below slower, not faster. Do not wait for it.

**EZdrummer 2 is end-of-life.** Toontrack permanently discontinued it on
3 May 2022 at version 2.2.2 and it will not be updated again, so it is VST2, AU,
AAX, RTAS and standalone and **there will never be a VST3**. 2.2.2 did add native
Apple silicon for the VST, AU and standalone; only the AAX is Intel-only. None of
that costs anything on this job: Reaper loads VST2 natively on both platforms,
and [live-playback.md](live-playback.md) requires the show session to hold no
plugins at all, so no drum instrument is ever loaded at the gig. The missing
VST3 only bites in a VST3-only host — Cubase 14 dropped VST2 — which is a
portability argument for another day, not a gig risk.

**What 3 buys that matters here** is two features landing on steps 3 and 4
above, which are the two steps you repeat per section:

| step | on 2 | on 3 |
|---|---|---|
| 3. find the texture | Tap2Find — tap the groove in by hand, once per section | **Bandmate** — drop `stems/drums.wav` in and it suggests matching library grooves |
| 4. overwrite kick and snare | drag the block out to Reaper, merge there, drag back | **Grid Editor** — inside the plugin, no round trip |

Five backing tracks and roughly forty sections, so that is around forty
hand-tapped searches and forty Reaper round trips removed. Plus a newer
2,500-groove library and a step sequencer behind Tap2Find. The upgrade is USD 99
from EZdrummer 2 against USD 179 new, and at any plausible value of a Saturday
it clears.

Bandmate is also the only thing in the EZdrummer line that recovers part of what
SD3's Tracker would have done. It does not transcribe — see Stage 4 — but the
*matching* it does is exactly what step 3 asks for.

**What it does not buy:** audio-to-MIDI — *neither* version has it, only Superior
Drummer 3's Tracker does; multi-out — EZdrummer 2 already does 16 channels, so
Stage 8 is fine as it stands; and a heavier sound — 3's core kits are Hansa
Studios rock kits and will still disappear under the guitars on a
`heaviness: 5` song.

**One thing to switch off rather than count as a benefit:** 3's velocity and
microtiming humanisation. `quantize.py` already does both from a seed, so that
re-running the pipeline after changing one setting does not reshuffle the song.
The plugin doing it again on top is the fight Stage 8 warns about — turn it off
along with the quantise.

So, in order:

1. **A heavy EZX first.** EZX libraries load in EZdrummer 2 *and* 3 *and* in
   Superior Drummer 3, so it is the one purchase here that cannot be stranded,
   and it is the single biggest jump in how professional the result sounds. On
   this album, a metal EZX on EZdrummer 2 beats EZdrummer 3's core kit.
2. **Then the EZdrummer 3 upgrade**, USD 99 — **on trial now**, see below.
   Bandmate and the Grid Editor fit this workflow, and across five backing tracks
   they should pay for themselves in hours. It will not change the sound — that
   is the EZX's job.
3. **Superior Drummer 3 only if** you would rather buy your way out of Stages 2
   and 4 entirely. More than this job needs if the hybrid works, and it should.
   Deferring it is nearly free: the crossgrade is USD 319 against USD 399 direct,
   so upgrading to 3 now and crossgrading later costs USD 19 more in total.

Formats, discontinuation date and prices above are off Toontrack's own product
pages and support forum, checked August 2026. The drum map is a separate cost
either way and does not transfer between them — see Stage 8.

### The 10-day trial, and what it has to answer

**EZdrummer 3 is installed as Toontrack's free 10-day trial** (Product Manager,
Free Trials category), started 23 August 2026 — so the buy-or-lapse date is
around 2 September. Check Product Manager for the real one. The USD 99 upgrade is
not spent yet and this trial is what decides it.

Ten days is about one song's worth of the Budget below, so treat it as a single
shake-down run rather than an album attempt, and do it on **09 Manlio** for
exactly the reasons in "Order to do them in": sparse, energy 1, errors obvious,
iterations fast.

**The trial cannot answer the sound question, so do not ask it.** No heavy EZX is
bought yet, so everything you hear is 3's core Hansa kit — which this section
already predicts will vanish under the guitars on a `heaviness: 5` song. Judging
the core kit and concluding "3 is not worth it" would be measuring the EZX
decision, not this one. The trial is a **workflow** test.

What it does have to answer, all of it on steps 3 and 4 above:

1. **Does Bandmate return usable grooves from `stems/drums.wav`?** Try two
   contrasting sections — a Manlio verse and one Tutti In Fila verse — and see
   whether the suggestions come back in the right style at the right intensity,
   or whether you end up filtering the browser by hand anyway.
2. **Does the Grid Editor really remove the round trip?** Paste the transcribed
   kick and snare over a library groove inside the plugin, and check the result
   survives being dragged out to Reaper intact.
3. **Time one section each way** — Tap2Find plus a Reaper merge on 2, against
   Bandmate plus the Grid Editor on 3.

Then the arithmetic is easy. USD 99 across roughly forty sections is **USD 2.50 a
section**, so a saving of more than a couple of minutes per section pays for the
upgrade. If it does not save that, "stay on 2" is a real result of the trial, not
a failure of it — 2 does every step in this document, just more slowly.

**Nothing done during the trial may be allowed to die with it.** End every
session at step 7: drag the Song Track out to Reaper and commit the MIDI. Once it
is in the repo it is instrument names in a `.mid` rather than plugin state, so it
opens in EZdrummer 2 if the trial lapses and `rambass drums remap` retargets it
to whatever kit ships. What *would* be stranded is an arrangement left living
inside an EZdrummer 3 project, or a Grid Editor part that was never exported. Do
the Stage 8 drum map on day one for the same reason — half an hour, and every
test above depends on it.

## Across the album

The scope is smaller than "every Tutti in Fila song". Eleven tracks, but:

* **02 I Puffi** already has a finished base, a Reaper project and a lyric video
  — `drums_origin: backing-track` in `existing-work.yaml`. Skip it.
* **07 La Canzone Del Tonno** is `drums.origin: a-cappella`. It needs a title
  card and nothing else. That is a finished state, not a to-do.
* **01 Intro Portamistica Z.Z.I.** and **10 La Canzone Del Solero** are
  `excluded.from_set: true` — cut. Do not spend a Saturday on the MP3-only one
  for a song that is not in the running order.

Which leaves **six songs and five backing tracks**:

| song | genre | e/h | notes |
|---|---|---|---|
| 03 Tutti In Fila | metal | 5/5 | hardest part, set position 3 |
| 04 Ampiamente Contestabile | rock, fun | 4/2 | |
| 05 Intro Vibratore | linking | 2/1 | **one track with 06** |
| 06 La Vera Storia Del Vibratore | rock / ambient, offbeat | 3/3 | **one track with 05** |
| 08 Skizzo Sonovabic | jazzy rock, epic | 3/3 | longest — most Stage 6 work |
| 09 Manlio | ballad | 1/1 | sparsest part |
| 11 L'Esercito Del Surf | pop / light rock | 3/1 | |

`gig.yaml` is explicit that 05 and 06 run together as one backing track and must
not be separated. They are two `song.yaml` files with two tempi and two
timelines, so decide early how that is modelled — one continuous click across
both, or a join that happens only in the show project.

### What is reusable, and what is not

Reusable once, across all five: the EZdrummer drum map
(`config/drum-maps/ezdrummer3.yaml`), the Reaper template with multi-out routing
and the bus chain, the loudness target, and the Stage 6 method.

**Not reusable: the kit.** That genre column spans metal, jazzy rock, a ballad
and light pop. One EZX across all of them will be wrong four times out of five —
a metal kit on Manlio sounds absurd, and the core pop/rock kit on Tutti In Fila
disappears. Expect roughly three kit choices: something heavy for 03, something
neutral and dry for 04/11, something soft or brushed for 09, and 08 is the
judgement call. Auditioning a kit per song costs ten minutes and is the highest
return per minute in this whole document.

Also not reusable: tempo, sections, and every velocity decision. Those are the
work.

### Order to do them in

Shake the toolchain down on **09 Manlio** first. It is a sparse ballad at energy
1: fewest hits, errors are obvious, iterations are fast, and you will discover
every rough edge in the separation-to-Reaper path on a song where mistakes are
cheap. Then do **03 Tutti In Fila** while patience is highest — it is the
hardest part on the album and it sits at position 3 in the set. Then 04, 11, 06
with 05, and 08 last, since it is the longest and by then the method is routine.

Most `rambass` commands take `--album tutti-in-fila` or `--all` instead of a
song, which is useful for the deterministic stages — `analyze`, `stems`,
`transcribe`. Do not batch `drums clean`: the quantise settings and the tempo
decision are per-song, and running one set of flags across six songs is how you
get five songs quantised against the wrong grid.

## Budget

Roughly **3–6 hours** for one song done properly, of which about one hour is
unattended compute. Most of the human time is Stages 6 and 7. The first song
costs more while the kit, the drum map and the mix template are established;
after that they are reusable across the album.

For the album that is a realistic **two to four full days** of work for the five
backing tracks — front-loaded, because Manlio and Tutti In Fila between them
will establish almost everything the other three reuse.

## Gaps in the repo this document assumes away

**Closed since this was written** (kept here so the list is honest about its own
age): the drum sub-separation stage is read automatically from `stems/parts/`;
`rambass drums consolidate` exists and Stage 6 is a command; `rambass drums
missing` and `rambass drums restore` make Stage 7 one; `rambass align --fit
--warp` replaced the hand-entered offset with a fitted piecewise map; and
`rambass stale` answers whether any of it is out of date.

Still open, ranked by value:

1. **A groove matcher over the Toontrack MIDI folder** — score every library
   `.mid` against a song's extracted kick and snare pattern per section, rank
   the candidates, and emit the hybrid. `mido` is already a dependency. This is
   what makes "map it, don't transcribe it" a process rather than an afternoon of
   auditioning.
2. **`config/drum-maps/ezdrummer.yaml`**, read off the plugin — including the
   cymbal choke notes. Note that `sidestick` now matters: the verses of Manlio
   are built on it, so the kit's rim-click articulation has to be mapped and its
   level chosen (see Stage 5).
3. **`crash_choke` and `china_choke` in `drummap.py`'s `CANONICAL`**, plus the
   short-decay test in `_classify_high_band` that detects them. Without the
   names there is nowhere for a choke to go.
4. **A set-level loudness match** using the `measure_loudness` already in
   `audio.py`.
5. **The [reaper.md](reaper.md) line 74 contradiction**, which as written bakes
   a click into the base.
6. **A tempo map written from detected beat times.** Deliberately *not* done for
   the drums — the fixed click is settled (CLAUDE.md) — but the machinery now
   exists in `align.py`, so if a song ever genuinely needs one, that is where it
   would come from.
