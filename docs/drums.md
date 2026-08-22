# Drums: transcription and cleanup

**This is a Tutti in Fila document.** Diversamente Giovani skips all of it: the
band mixed finished live backing tracks out of the album sessions with the drums
already in them, so there is nothing to separate, transcribe, quantise or voice.
Those songs are `drums.origin: backing-track` and their drum stages are `n/a`.
See `songs/diversamente-giovani/README.md`.

## What the transcriber actually does

`rambass drums transcribe` runs onset detection **independently in three
frequency bands** rather than detecting onsets once and guessing what each was:

| band | instrument | why |
|---|---|---|
| 25–120 Hz | kick | nothing else lives down there |
| 170–1200 Hz | snare | body of the drum |
| 6–16 kHz | hats and cymbals | above almost everything else |

A kick and a hi-hat played together are one onset in the full-band signal but
two clearly separate onsets in those bands — which is most of a rock drum part,
so this matters more than any amount of clever classification.

High-band hits are then split by **how long they ring**: still sounding 350 ms
later at 45% of the attack means a crash, 22% means an open hat, less means a
closed hat. It is a heuristic and it is honest about being one.

### What it gets right

Kick, snare and hat backbone in a normal groove. That is the boring 90% of a
song and it is exactly the part you do not want to enter by hand.

### What it gets wrong

* **Toms** — no dedicated band, so a floor tom reads as a kick or a snare.
* **Fills** — dense, fast, overlapping; expect mush.
* **Ghost notes** — usually below the detection threshold.
* **Ride patterns** — read as hats, since both are high-band and short.
* **Anything under a ringing crash** — the crash masks the band.

So: treat the output as a first pass that saves an hour of hand-entry per song.
Open it in Reaper against `stems/drums.wav`, and fix the fills by hand. Check
the hit counts in `--report` before you go anywhere near the DAW: 704 hats in a
three-minute song is plausible, 4000 is a broken detection and no amount of
quantising will save it.

### Measured on a real one: Tutti in Fila

The first transcription of Tutti in Fila (5:03, 116.03 BPM) came out wrong in
three separate ways, and all three are worth knowing because they are not
specific to that song. The test used throughout: **what fraction of hits land
within 25 ms of a sixteenth**, against the 39% you would get from hits scattered
at random.

| | hits on a sixteenth |
|---|---|
| chance | 39% |
| first run, fixed grid | 30% — *worse than random* |
| following the take (`dewander`) | 62% |
| plus the anchor nudge (`best_anchor_shift`) | 62%, with no hand-set `--offset` |

1. **The crash test does not survive a real cymbal player.** 598 crashes against
   126 kicks. `_classify_high_band` asks whether the high band is still ringing
   0.35 s after the attack; on dense material it never falls quiet, so 67% of
   high-band hits read as crashes. Measuring decay against the local floor rather
   than zero only got it to 54% — it is the wrong question, not a bad threshold.
   **Use `--no-cymbals` and place the crashes by hand.**
2. **The kick's `dominance` was throwing away 85% of the kicks** — 686 of 812
   peaks — because in a bright mix the 25-120 Hz band never holds 30% of the
   total energy. Now 0.18, which gives a kick line with roughly the density the
   snare implies (68/min against the snare's 59/min backbeat).
3. **A take that breathes cannot be read against a fixed grid.** This is the big
   one, and it is *not* a reason to put a tempo map in the manifest — the output
   stays metronomic, per the settled decision in [workflow.md](workflow.md).
   The band wandered up to ~250 ms from the fixed grid, which is nearly a whole
   beat and far more than the half-sixteenth budget, so hits were being read onto
   the wrong subdivision. `transcribe.dewander` corrects each detected time by
   the local wander *before* placing it, and alignment doubles.

The residue after all three: `drums clean` moved hits a mean of 18 ms, against
the ~40 ms that means "the tempo is wrong, not the drummer".

### The failure that hides from every timing check: whole-subdivision parity

Found on **09 Manlio** while shaking down the
[drums-rebuild.md](drums-rebuild.md) process, and it is the nastiest one here
because every measurement above reports it as *fine*.

`best_anchor_shift` is bounded to half a subdivision so it can never move a hit
onto a different note. That bound leaves a hole: if the anchor is out by a
**whole** subdivision, every hit is still exactly on the grid — just on the wrong
sixteenth — so hits-on-a-sixteenth, the quantise report and the mean move all
look healthy while the kick plays the "e" of every beat.

What exposes it is asking a *coarser* question. Alignment to the **eighth-note**
grid, which a whole-sixteenth shift does change:

| Manlio | on an 8th | vs chance |
|---|---|---|
| as transcribed | 1.9% | **0.19x** — actively avoiding the beat |
| anchor moved one sixteenth | 31.0% | **3.10x** |

`transcribe.beat_parity_shift` now tests every whole-subdivision shift and scores
it on kick and snare only, since those are the parts with a real prior: they land
on beats more often than between them. It applies a shift only when it wins by
3x, and otherwise reports the near miss rather than acting —
because a genuinely syncopated part would be *given* this fault by a shift, not
cured of it. Tutti in Fila lands in exactly that grey zone (2.3x) and is left
alone with a note, which is the honest answer: metal kicks do live on 16th
offbeats, and only ears can settle it.

The general lesson, worth keeping: **a timing check on the same grid you
quantised to cannot see an error that is a whole grid step.** Check one level
coarser.

## Cleanup, in the order it happens

### De-flam

```
rambass drums clean <song> --deflam-ms 25
```

Onset detection double-triggers constantly: a kick with a long tail reads as two
onsets, a rattling snare as three. Left alone those become machine-gun flams the
moment the MIDI hits a sampled kit. De-flam collapses duplicates of the *same*
instrument inside a window, keeping the earliest time (the real attack) and the
loudest velocity (the real hit). 25 ms is a good default; go to 40 ms for a
boomy kick, down to 15 ms if genuine fast doubles are being eaten.

### Quantise

```
rambass drums clean <song> --subdivision 4 --strength 1.0
```

* `--subdivision 4` is 16ths in 4/4. Cymbals are quantised to 8ths regardless,
  because snapping a crash tight sounds wrong.
* `--strength` is 0 to 1. `1.0` is dead on the grid — right for a backing track
  that has to be identical every night. `0.8` keeps a trace of the original feel.
* `--swing 0.33` pushes odd subdivisions late for a shuffle.
* Hits further than ~1/3 of a subdivision from any grid line are **left alone**,
  on the assumption that they are intentional — a drag, a push, a fill. The
  report line tells you how many. `--force-grid` overrides that.

Read the report:

```
quantise      1069 hits, 0 left alone, mean move 11.25 ms, max 24.71 ms
```

A mean move under ~15 ms means the tempo in `song.yaml` is right. A mean move
over 40 ms, or a large "left alone" count, means it is wrong — go back to
`rambass analyze` before quantising harder.

### Velocities

```
--accent-kick 112 --accent-snare 104 --downbeat-boost 6
```

Transcribed velocities come from onset strength, which is only loosely related
to how hard the drummer hit. Flattening per instrument and then accenting
musically usually beats trusting the analysis.

### Humanise

```
--humanize 4 --seed 0
```

Puts ±4 ms of jitter back after a hard quantise, because fully-quantised drums
with identical velocities read as a drum machine. **The kick is excluded by
default** — that is what the bass player locks to. The seed makes it
reproducible: re-running after changing one setting does not reshuffle the song.

## Kits and mapping

Transcription works in *names* (`kick`, `hihat_open`) and only turns them into
note numbers at write time, so a file can be retargeted without re-analysing:

```
rambass drums remap <song> my-kit --set-default
```

`config/drum-maps/general-midi.yaml` is the spec-defined GM map and the default.
For a commercial kit, copy `config/drum-maps/_custom-template.yaml` and read the
note numbers **off your plugin's own mapping page** — they differ per kit and per
articulation, and a table found on the internet will be wrong for your preset.

Keeping the transcription in GM and remapping at the end is the safer habit.
