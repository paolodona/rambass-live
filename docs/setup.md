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

## ffmpeg is installed but the command still says it is not

The commonest Windows case, and it is not an installation problem. `winget
install Gyan.FFmpeg` puts the binaries in

```
%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_...\ffmpeg-9.0-full_build\bin
```

and does not always link them, so `where ffmpeg` finds nothing and every audio
command reports an install hint for something already present.

Point `RAMBASS_FFMPEG` at that folder — or at the binary itself — rather than
editing the system PATH. Set it **persistently**, at User scope, so every
process started afterwards inherits it — a `$env:`/`export` assignment dies with
the shell, which is how the same error comes back tomorrow:

```powershell
$bin = (Resolve-Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*-full_build\bin").Path
[Environment]::SetEnvironmentVariable('RAMBASS_FFMPEG', $bin, 'User')
$env:RAMBASS_FFMPEG = $bin   # and for the shell you are in now
```

An already-running process — a terminal left open, Reaper, an editor — keeps the
environment block it was launched with, so restart *that* process rather than
hunting for a second bug. In Git Bash the value must stay in **Windows** form:
it is handed to Python, and Python on Windows cannot open a `/c/...` path.

A `winget upgrade` renames the version-bearing folder, which invalidates the
variable — re-run the two lines above after one.

A set-but-wrong value is an error rather than a silent fall-through — to PATH or
to the search below — because otherwise a typo in the variable reports "ffmpeg is
not on PATH" and sends you to check the one thing that was never the problem.

`rambass doctor` resolves ffmpeg exactly as the audio code does — the override,
then PATH, then a search of the two winget portable roots
(`%LOCALAPPDATA%\Microsoft\WinGet`, `%PROGRAMFILES%\WinGet`) — and says which
route found it. So `[ok ] ffmpeg ... (via RAMBASS_FFMPEG)` means the override is
doing the work, and `(found in the winget package folder...)` means nothing is
set and discovery caught it. That last case works, but pin it with the variable:
the search is a safety net for a machine nobody has configured, not the intended
route, and it is one `winget upgrade` away from looking at a stale build folder.

Discovery runs only when the override is unset *and* PATH has nothing. It cannot
outrank either, deliberately: a linked ffmpeg is a decision, a package folder
left on disk is not, so an ffmpeg installed by hand is never quietly replaced by
the winget copy.
