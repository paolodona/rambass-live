# Notes for Claude working in this repo

## What this is

Tooling to turn two albums by **Ramba S.S.** into live backing tracks: the
drummer cannot play the gig, so every drum part becomes a click-locked,
quantised backing track, with lyric/image video and automatic BOSS GX-100 patch
changes. The DAW is Reaper. Read `README.md` and `docs/workflow.md` first.

## The one invariant

**Positions are in bars, never seconds.** Sections, lyric cues, patch changes,
tempo changes — all anchored to bars, converted once in `src/rambass/timeline.py`
from the tempo in `song.yaml`. If you add a feature that stores a time in
seconds in a manifest, you have broken the ability to change a song's tempo, and
that will not show up until someone changes one.

`Timeline` distinguishes two clocks and so must you:

* `bar_beat_to_seconds()` — from the **musical** zero (bar 1 beat 1); the
  count-in is negative
* `audio_time()` — from the **first sample** of the rendered audio; this is what
  goes into Reaper, ffmpeg and subtitle files

Getting these two mixed up shifts everything by the count-in length.

## Architecture

Layered so the cheap deterministic parts have no heavy dependencies:

| module | job | deps |
|---|---|---|
| `project.py` | repo layout, song lookup, slugs | — |
| `timeline.py` | bars ↔ seconds ↔ quarter notes | — |
| `manifest.py` | `song.yaml` load/save/validate | pyyaml |
| `drummap.py` | canonical instrument names → note numbers | pyyaml |
| `midiio.py` | `Hit`/`DrumPerformance`, MIDI read/write with tempo map | mido |
| `quantize.py` | de-flam, quantise, humanise, velocities — pure functions | — |
| `reaper.py` | `.rbs` build-script generation | — |
| `gx100.py` | pedalboard MIDI, program map | mido |
| `video.py` | lyric cue parsing, ASS, ffmpeg render | — |
| `setlist.py`, `status.py`, `doctor.py` | running orders, progress, diagnostics | — |
| `audio.py` | ffmpeg decode/encode, WAV write, loudness | numpy |
| `analyze.py` | tempo/beat/drift detection | **librosa** |
| `transcribe.py` | drum stem → hits | **librosa** |
| `stems.py` | demucs wrapper | **demucs** |

Keep it that way: never import librosa, scipy or demucs at module top level in
anything outside `analyze.py` / `transcribe.py` / `stems.py`. Use
`audio.require_module()` so a missing extra produces an install hint rather than
an ImportError traceback.

## Conventions

* Drum data is carried as **instrument names** (`kick`, `hihat_open`), not note
  numbers. Note numbers only appear at MIDI write time via a `DrumMap`. That is
  what makes retargeting to a different kit free.
* `quantize.py` functions are pure: performance in, performance out, plus a
  report dict. Do not make them touch disk.
* Anything random takes a **seed** (see `humanize`). Re-running the pipeline
  after changing one setting must not reshuffle the song.
* CLI errors: raise `ProjectError` or `AudioError` with a message that says what
  to do next. `cli.main()` turns those into a one-line message and exit code 2;
  anything else becomes a traceback, which is a bug.
* No `.RPP` writing. Reaper projects are built by the ReaScript from a `.rbs`
  build script — see `docs/reaper.md` for why.

## Facts that were verified, don't re-guess them

The GX-100 numbers in `gx100.py` and `docs/gx100.md` come from the official
*GX-100 MIDI Implementation* ver 1.10 (2022-03-03): bank select is CC#0 with
values **0–2 only** followed by CC#32 = 0; program change is resolved through
the pedal's own `MENU:MIDI:PROGRAM MAP`, so **a PC number does not name a
memory**; memories are `U01-1`…`U50-4` then `P01-1`…`P25-4` (300 total); CC#1–31,
#33–63, #64–95 are assign sources; MIDI clock is followed when `SYNC CLOCK` ≠
`INTERNAL`.

Commercial drum-VST note maps were deliberately **not** shipped, because they
differ per kit and per articulation and a guessed table is worse than no table.
`config/drum-maps/_custom-template.yaml` exists for the user to fill from their
plugin. Don't replace it with invented numbers.

## Testing

`pytest` — `tests/` builds a throwaway project in a tmp dir and exercises
manifests, timeline maths, MIDI round-trips, quantising, build scripts, GX-100
mapping and lyric parsing. All of it runs without ffmpeg, librosa or demucs.
Anything needing those must be skipped, not required.

Run it: `pytest` (or `make test`).

## Don't

* Don't commit audio or video. `.gitignore` covers it; check before `git add -A`.
* Don't invent Reaper `.RPP` internals or drum-VST mappings.
* Don't add a dependency to the core tier without a real need — the point of the
  layering is that a laptop at a venue can run the core commands.
