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

**The one deliberate exception is lyric cues.** They live in `lyrics.srt`, in
seconds, because the band's existing hand-timed files are 100+ millisecond-timed
phrase cues per song authored against a specific master — a performance
transcription, not a musical anchor. See `docs/lyrics.md`. Do not "fix" this by
moving them onto bars; `lyrics.shift`/`scale` exist for when the timing base
moves. Bar-anchored `lyrics.md` still exists for songs written from scratch, and
`lyrics.from_bar_cues`/`to_bar_cues` bridge the two.

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
| `reaper.py` | `.rbs` build-script generation, **and reading existing `.RPP`** | — |
| `lyrics.py` | SRT/VTT/LRC/ASS cues, editing, checking, Whisper drafting | — |
| `gx100.py` | pedalboard MIDI, program map | mido |
| `video.py` | lyric cue parsing, ASS, ffmpeg render | — |
| `setlist.py`, `status.py`, `doctor.py` | running orders, progress, diagnostics | — |
| `arrange.py` | reviews a running order against set-list practice | — |
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
* No `.RPP` **writing**. Reaper projects are built by the ReaScript from a `.rbs`
  build script — see `docs/reaper.md` for why. `.RPP` *reading* is fine and is
  implemented in `reaper.parse_rpp`, validated against the band's real Reaper
  6.82 projects (`tests/fixtures/live-project.RPP` mirrors one).

## Facts that were verified, don't re-guess them

The GX-100 numbers in `gx100.py` and `docs/gx100.md` come from the official
*GX-100 MIDI Implementation* ver 1.10 (2022-03-03): bank select is CC#0 with
values **0–2 only** followed by CC#32 = 0; program change is resolved through
the pedal's own `MENU:MIDI:PROGRAM MAP`, so **a PC number does not name a
memory**; memories are `U01-1`…`U50-4` then `P01-1`…`P25-4` (300 total); CC#1–31,
#33–63, #64–95 are assign sources; MIDI clock is followed when `SYNC CLOCK` ≠
`INTERNAL`.

**Diversamente Giovani does not use the drum pipeline at all.** The band mixed
finished live backing tracks ("BASE") straight out of the album sessions with the
drums already in them. Every song on that album is `drums.origin:
backing-track`, which marks `stems`/`drums_midi`/`quantize`/`kit` as `n/a`. Do
not propose separating, transcribing or voicing drums for it — the remaining
songs need a base *mixed* in the album session, which is not a job for this repo.
Isolated drum stems, Aerodrums `.aer` files and per-song BFD3 presets are
recorded in `existing-work.yaml` as **provenance only**, for the case where a
base has to be rebuilt from scratch.

`config/drum-maps/bfd3.yaml` is a **stub with blank note numbers** that falls
back to General MIDI — the real numbers depend on the per-song preset and must be
read off the plugin. Don't fill it in with invented values; same for
`_custom-template.yaml`.

Tempos for Diversamente Giovani in `songs/diversamente-giovani/existing-work.yaml`
came off the band's own production folder names and were cross-checked against
their lyrics docs. They are better than anything `rambass analyze` would produce
— don't overwrite them with detected values. The same file carries the band's own
review punch-list per base; `rambass countin` exists because four of the seven
finished bases are missing their count-in.

`arrange.py` deliberately **does not generate** a running order. Sequencing is a
musical judgement; what a tool can usefully do is catch what a human misses in
their own list. Don't add an auto-sequencer — add checks.

Each song's `character` block (genre, energy, heaviness, standing, role) is the
band's own read of itself, not anything derived from audio. Energy and heaviness
are separate axes on purpose: Il Phurgone is high energy and not heavy at all.
Never collapse them.

## Testing

`pytest` — `tests/` builds a throwaway project in a tmp dir and exercises
manifests, timeline maths, MIDI round-trips, quantising, build scripts, GX-100
mapping and lyric parsing. All of it runs without ffmpeg, librosa or demucs.
Anything needing those must be skipped, not required.

Run it: `pytest` (or `make test`).

## Git workflow

**Work directly on `main`.** Paolo is the only maintainer, so there is no reason
for feature branches or pull requests here — commit to `main` and push. Don't
create a branch unless explicitly asked to.

History note: the repo was created empty and the first push landed on a
`claude/...` branch, which GitHub then made the default. `main` was created from
that history, so the two are identical up to commit `0041f9a`. The
`claude/ramba-backing-track-tools-wx0lam` branch is dead — if it still exists,
it can be deleted once GitHub's default branch is `main`.

## Don't

* Don't commit audio or video. `.gitignore` covers it; check before `git add -A`.
* Don't invent Reaper `.RPP` internals or drum-VST mappings.
* Don't add a dependency to the core tier without a real need — the point of the
  layering is that a laptop at a venue can run the core commands.
