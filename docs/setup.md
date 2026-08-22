# Setup

## Install

```
git clone <this repo>
cd rambass-live
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[audio]'
rambass doctor
```

`rambass doctor` tells you what is missing and how to get it. Three tiers:

| tier | install | needed for |
|---|---|---|
| core | `pip install -e .` | manifests, MIDI, quantise, click, Reaper scripts, GX-100, subtitles |
| audio | `pip install -e '.[audio]'` | `analyze`, `drums transcribe` — pulls in librosa and scipy |
| separate | `pip install -e '.[separate]'` | `stems` — pulls in demucs and torch, a large download |

`uv` works too and is much faster: `uv pip install -e '.[audio]'`.

## ffmpeg

Needed to read MP3s and to render video.

```
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian/Ubuntu
winget install Gyan.FFmpeg   # Windows
```

## Demucs, and how long separation takes

Only needed for *Tutti in Fila*. On CPU, expect several minutes per song in
two-stem mode and considerably longer for four stems with `htdemucs_ft`. On
Apple Silicon, `--device mps` is a large speedup; on an NVIDIA card,
`--device cuda`.

`rambass doctor` reports which of those is available.

## Reaper

Not installed by this repo — install it yourself. The scripts under `reaper/`
are ReaScripts, run from inside Reaper (**Actions > Show action list >
ReaScript: Run ReaScript (EEL2 or Lua)…**), and were written against the
Reaper 7 API. Verified against Reaper 7.78.

## Drum VST

Not included and not scriptable from here. Anything that reads General MIDI will
work. Once you have picked one, read its note mapping off its own key-map page
and put it in `config/drum-maps/` — see docs/drums.md.
