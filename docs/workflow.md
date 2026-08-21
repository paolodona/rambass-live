# Workflow

## The one idea to keep in your head

**Everything is positioned in bars, not seconds.** A section marker, a lyric
cue, a patch change: all written as "bar 25". The bar-to-seconds conversion
happens once, in `rambass/timeline.py`, from the tempo in `song.yaml`.

That is what makes it survivable to decide, three songs in, that a track should
sit at 126 rather than 124 BPM: change one number, regenerate, and the click,
the drum grid, the Reaper markers, the lyric timings and the pedal changes all
move together. Anything written in seconds would silently drift out of place.

## The two albums need different work

| | Diversamente Giovani | Tutti in Fila |
|---|---|---|
| drums exist as | recorded tracks | only inside the stereo mix |
| `drums.origin` | `recorded` | `extracted` |
| demucs needed | no | yes |
| starting point | `rambass drums import` | `rambass stems` + `drums transcribe` |
| realistic effort | minutes per song | an hour or two per song |

## Per-song sequence

```
rambass new "Titolo" --album tutti-in-fila --track 4
```
Then drop the original mix into `songs/tutti-in-fila/04-titolo/source/`.

### 1. Find the tempo

```
rambass analyze 04-titolo --write
```

Read the output before moving on. It prints the detected tempo *and the drift*,
and the drift is the decision point:

* **drift under ~1 BPM** — the band played tight. Take the rounded tempo, and
  the original recording will still line up with the new fixed grid. Quantise
  hard and enjoy it.
* **drift of several BPM** — the take breathes. You now choose:
  * *follow the performance*: add `tempo.changes` entries so the grid bends with
    the recording. Keeps the original feel, but the click will bend too, which
    is harder to play to.
  * *re-programme to a fixed click*: keep one tempo, accept that the original
    audio will slide out of sync as a reference, and treat the transcription as
    raw material. Usually the right answer for a gig — it is why we are doing
    this at all.

`--write` marks the stage `wip` rather than `done` when the tempo drifts, so
`rambass status` keeps reminding you that a decision is outstanding.

### 2. Separate the drums (extracted songs only)

```
rambass stems 04-titolo --drums-only
```

`--drums-only` is two-stem mode: much faster, and it also produces
`no_drums.wav`, which is the band-minus-drums bed you will A/B the new
programmed part against. Use `--model htdemucs_ft` (the default) when quality
matters; it is roughly four times slower than `htdemucs` and noticeably cleaner
on cymbals. On Apple Silicon add `--device mps`.

### 3. Get the drums into MIDI

Extracted:
```
rambass drums transcribe 04-titolo
```
Recorded:
```
rambass drums import 04-titolo /path/to/drums.mid --channel 10
```

`transcribe` detects the first downbeat and shifts the result so MIDI bar 1 is
musical bar 1. If it guesses wrong, pass `--offset <seconds>` explicitly.

### 4. Clean it up

```
rambass drums clean 04-titolo --humanize 4 --accent-kick 112 --accent-snare 104
```

This is the step that turns a take into a backing track. It de-flams, quantises
and shapes velocities in one pass, and prints how far it had to move things —
a mean move over about 40 ms means the tempo is wrong, not the drummer. See
docs/drums.md.

### 5. Click, pedalboard, project

```
rambass click 04-titolo
rambass gx100 midi 04-titolo
rambass reaper build 04-titolo
```

Then in Reaper: **File > New Project**, **Actions > Show action list >
ReaScript: Load…** → `reaper/scripts/rambass_build_song.lua` → Run, and pick
the `.rbs` file. See docs/reaper.md.

### 6. Video

```
rambass video ass 04-titolo        # subtitles only, fast, proof-read this first
rambass video render 04-titolo     # the MP4
```

### 7. Track it

```
rambass mark 04-titolo kit done
rambass status
```

## Whole-show commands

```
rambass setlist gig                    # running order with timings
rambass reaper setlist gig             # one Reaper project, region per song
rambass gx100 sheet --setlist gig      # the sheet that gets taped to the pedal
rambass status --markdown              # progress board for the band chat
```

## What to commit

Commit `song.yaml`, `lyrics.md`, the `.mid` files, the `.ass` files, and any
config you changed. Audio and video are gitignored — they are rebuildable, and
the repository stays small enough to clone on a phone at the venue.
