# rambass-live

Tooling to build the backing tracks for **Ramba S.S.** live shows.

The band is playing both albums live and the drummer cannot make the gig, so
every drum part becomes a backing track — played to a click, quantised, with a
better drum sound than the originals, plus lyric/visual video on the screen and
automatic patch changes on the guitarist's BOSS GX-100.

This repository holds the recipes for all of that. The DAW is **Reaper**.

```
rambass doctor                                  # is the toolchain usable?
rambass new "Titolo" --album tutti-in-fila      # scaffold a song
rambass analyze 04-titolo --write               # what tempo did they play at?
rambass stems 04-titolo --drums-only            # pull the drums out of the mix
rambass drums transcribe 04-titolo              # onsets -> MIDI
rambass drums clean 04-titolo --humanize 4      # de-flam, quantise, shape
rambass click 04-titolo                         # click + count-in
rambass gx100 midi 04-titolo                    # pedalboard patch changes
rambass reaper build 04-titolo                  # Reaper project
rambass video ass 04-titolo                     # lyric timings
rambass video card 11-se-sei-felice             # a title card for the screen
rambass arc gig                                  # the set's energy shape + what it breaks
rambass status                                  # what is left to do
```

## Install

```
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[audio]'
rambass doctor
```

Core commands need nothing heavy. `analyze` and `drums transcribe` need
`[audio]` (librosa); `stems` needs `[separate]` (demucs + torch); reading MP3s
and rendering video needs `ffmpeg` on PATH. See [docs/setup.md](docs/setup.md).

## The two albums, two different jobs

| | Diversamente Giovani | Tutti in Fila |
|---|---|---|
| drums arrive as | part of a finished backing track | only inside the stereo mix |
| drum pipeline | **not needed** | the whole job |
| demucs needed | no | yes |
| tempos | **known** — off the band's production folders | must be detected |
| lyric cues | 6 songs hand-timed already | none yet |
| Reaper projects | 8 songs already built (importable) | none |

The two albums are at very different starting points.
`songs/diversamente-giovani/existing-work.yaml` records exactly what already
exists for the newer one — finished backing tracks, tempos, lyrics documents,
SRT files, rendered videos, Reaper projects and the band's own review notes on
each base — so none of it gets rebuilt by accident.

## How it fits together

Everything is positioned in **bars**, never seconds. A section marker, a lyric
cue, a patch change: all "bar 25". One tempo map in `song.yaml` converts bars to
time, so changing a song's tempo moves the click, the drum grid, the Reaper
markers, the lyric timings and the pedal changes together instead of silently
desynchronising them.

```
song.yaml ──┬─→ click.wav            (render/)
            ├─→ drums-quantized.mid  (midi/)
            ├─→ gx100.mid            (midi/)
            ├─→ <song>.rbs           (reaper/build/ → ReaScript → Reaper project)
            └─→ <song>.ass / .mp4    (video/)
```

## Layout

```
songs/<album>/<NN>-<slug>/    one folder per song — song.yaml is the source of truth
  source/ stems/ midi/ render/ video/
setlists/                     running orders
config/
  gx100.yaml                  pedalboard MIDI + program map
  drum-maps/                  canonical instrument -> MIDI note, per kit
reaper/
  scripts/                    ReaScripts: build a song project, live transport
  build/                      generated build scripts (disposable)
video/assets/                 images shared between songs
docs/                         see below
src/rambass/                  the CLI
```

Audio and video are gitignored: large, binary, and rebuildable from `song.yaml`
plus the source mix. The manifests, the MIDI and the subtitle files are the real
work and those are tracked.

## Docs

| | |
|---|---|
| **[plan.md](docs/plan.md)** | **the phased plan, gates, and what runs in parallel** |
| **[setlist.md](docs/setlist.md)** | **how the running order is built, and how to check one** |
| **[live-playback.md](docs/live-playback.md)** | **how the show is driven on stage, and why it never stutters** |
| [setup.md](docs/setup.md) | installing, and what each dependency tier buys |
| [workflow.md](docs/workflow.md) | the per-song sequence, and the tempo-drift decision |
| [drums.md](docs/drums.md) | what the transcriber does well and badly; cleanup settings |
| [reaper.md](docs/reaper.md) | building projects, where bar 1 is, live transport |
| [gx100.md](docs/gx100.md) | GX-100 MIDI, the program-map gotcha, tempo sync |
| [lyrics.md](docs/lyrics.md) | the SRT cue format, house style, drafting the missing songs |
| [video.md](docs/video.md) | image cues, rendering, what reads from the back of the room |
| [live-runbook.md](docs/live-runbook.md) | gig-day checklist, and what to do when the laptop dies |

## Honest limits

* **Drum transcription is a first pass.** It gets the kick/snare/hat backbone;
  it gets toms, fills, ghost notes and ride patterns wrong. Budget hand-editing
  time per song. [docs/drums.md](docs/drums.md) says exactly what fails and why.
* **The GX-100 program map has to be verified against the pedal.** A program
  change names a slot, not a memory, and the pedal decides what that slot means.
  Send one and watch the display before trusting the whole set.
* **The Reaper ReaScript was written against the Reaper 7 API** and builds the
  project through Reaper itself rather than writing `.RPP` files, but it has not
  been run on your specific Reaper install. Run it on a fresh project first.
