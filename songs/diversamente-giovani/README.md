# Diversamente Giovani

15 tracks. **The drums do not need rebuilding for this album.** Seven songs
already have a finished live backing track — a "BASE" mixed straight out of the
album sessions with the drums in it — and the remaining songs need the same
treatment, not a drum reconstruction.

That makes this album a completely different job from Tutti in Fila. See
[existing-work.yaml](existing-work.yaml) for the inventory with Drive ids; the
point of that file is that none of the finished work gets redone by accident.

## What already exists

| | |
|---|---|
| backing tracks | **6 songs here + I Poohffi** — finished, signed off, in `BASI LIVE` and in each live project folder |
| tempos | **known for all 13 real songs**, off the band's own production folders — `analyze` is done |
| lyric cues | 6 songs hand-timed as SRT — **do not regenerate these** |
| lyric videos | the same 6 rendered, at 480p/720p/1080p and as YouTube exports |
| Reaper projects | 7 songs already built, with GX-100 patch changes on hardware MIDI out |
| review notes | the band's own sign-off and punch-list per base, in "Note Basi Live" |

Every song is `drums.origin: backing-track`, so the whole drum pipeline —
`stems`, `drums_midi`, `quantize`, `kit` — is marked `n/a`. `rambass status`
shows that as `-`.

## The work that is actually left

### 1. Fix the punch-list on the seven finished bases

The band's review signs all seven off but lists outstanding mix notes, carried
into each song's `notes:` field so `rambass show` surfaces them. One recurs on
four songs: **the count-in is missing**. That does not need a remix:

```
rambass countin 02-formaygrana
```

prepends a click count-in at the song's known tempo and writes
`render/<base>-countin.wav`. The rest of the notes (a ride slightly too loud, a
rhythm guitar too far forward, Il Phurgone's truncated first hits) are remix
work in the album project, not something this repo can do.

### 2. Mix a base for the remaining eight songs

Bambolina, Orologiaio, Il Cellulare, Per Niente Stanca, Superman, Se Sei Felice,
Mandami un Faxe — and decide about INTRO and OUTRO, which are currently marked
`n/a` on the assumption they play as straight album audio.

This is a mixing job in the album session: take the album mix, pull out whatever
the band plays live, render a BASE. The tempos are already known, so once a base
exists, `rambass countin` and the Reaper build are immediate.

### 3. Lyric cues for the missing nine

The words already exist — 10 songs have a Google Doc with the full text plus
tempo, key, chords and section names. Only the *timing* has to be made. See
[../../docs/lyrics.md](../../docs/lyrics.md).

```
rambass lyrics import 02-formaygrana "02 ForMayGrana.srt"   # the six that exist
rambass lyrics transcribe 04-bambolina --prompt-file lyrics.txt
```

### 4. Import the seven Reaper projects

```
rambass reaper import 02-formaygrana /path/to/4MyGrana.RPP --write
```

Recovers the tempo, the section markers and the pedal changes. Those projects
carry a couple of minutes of title card before the music, so the musical zero is
the first audio item — which is what the importer assumes.

## Getting the audio

`sources.yaml` lists the album masters; `existing-work.yaml` lists the finished
bases. For live use you want the **bases**, in `render/`; the masters are only
needed if a base has to be remixed. Audio is gitignored, so this is per-machine.

## Caveats to sort out

* **Titles came from the master filenames**, not the sleeve — check all fifteen.
* **Orologiaio is recorded as 240 BPM** because that is what the production
  folder says. Check whether it is really 120 counted double-time.
* **Track 13 has two masters** (`MST1`/`MST2`). `MST2` is referenced.
* **The 480p `.mov` lyric videos are downscales** made for the 2023 live rig.
  For a projector use the `ORIGINAL.mp4` or 1080p versions.
* Isolated drum stems and Aerodrums `.aer` session files still exist per song and
  are recorded in `existing-work.yaml`. They are **provenance, not the plan** —
  only relevant if a base ever has to be rebuilt from scratch.
