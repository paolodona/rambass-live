# Drums: transcription and cleanup

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
