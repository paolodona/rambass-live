# Diversamente Giovani

15 tracks. **This album is a long way from a standing start** — see
[existing-work.yaml](existing-work.yaml) for the full inventory with Drive ids.
The point of that file is that none of the work below gets redone by accident.

## What already exists

| | |
|---|---|
| tempos | **known for 13 songs**, off the band's own production folders — `analyze` is done |
| drums | Aerodrums performance → BFD3 sounds, rendered to audio; an isolated drum stem exists for most songs |
| BFD3 presets | one `.bfd3` per song, recorded in each `song.yaml` as `drums.kit` |
| lyrics | full text in a Google Doc for 10 songs, with tempo, key, chords and section names |
| lyric cues | 6 songs already have hand-timed SRTs — **do not regenerate these** |
| lyric videos | the same 6 rendered, at 480p/720p/1080p and as YouTube exports |
| Reaper projects | 7 of these songs already built for the 2023 live set, with GX-100 patch changes |

## So the work here is

1. **Import the Reaper projects** rather than rebuilding them:

       rambass reaper import 02-formaygrana /path/to/4MyGrana.RPP --write

   That recovers the tempo, the section markers and the pedal changes. Those
   projects carry a couple of minutes of title card before the music, so the
   musical zero is the first audio item — which is what the importer assumes.

2. **Import the six SRTs**, and produce the other nine in the same format:

       rambass lyrics import 02-formaygrana "02 ForMayGrana.srt"

   See [../../docs/lyrics.md](../../docs/lyrics.md). The lyrics text already
   exists in the Google Docs, so only the *timing* has to be made.

3. **Rebuild the drums as MIDI.** The drums are audio, not MIDI, so they still
   need transcribing — but off a clean isolated stem, not a demucs separation,
   which makes it much more accurate than for Tutti in Fila:

       cp .../4MG_drum_complete.wav songs/.../02-formaygrana/stems/drums.wav
       rambass drums transcribe 02-formaygrana
       rambass drums clean 02-formaygrana

   **Check the `.aer` files first.** The performance was captured with Aerodrums,
   and the session files are sitting next to the drum stems. If Aerodrums can
   export MIDI from them, that beats transcribing audio and skips this whole
   step — worth ten minutes to find out.

## Getting the audio

`sources.yaml` lists every master with its Drive file id (masters are under
`MASTER/44100 24 bit PCM/`). Download each into its song's `source/` folder
keeping the filename `source.audio` names; `rambass check` reports what is
missing. Audio is gitignored, so this is per-machine.

## Caveats to sort out

* **Titles came from the master filenames**, not the sleeve. `ForMayGrana`,
  `Itturfiatrugoy` and `Mandami un Faxe` are presumably deliberate; check all
  fifteen.
* **Orologiaio is recorded as 240 BPM** because that is what the production
  folder says. Check whether it is really 120 counted double-time — it changes
  what the click feels like.
* **Track 13 has two masters** (`MST1`/`MST2`). `MST2` is referenced.
* **Tracks 1 (INTRO) and 15 (OUTRO)** have no production folder and no lyrics
  doc; the "Intermezzi / Sketches" doc covers the linking material. They most
  likely play as straight album audio — if so, mark their drum stages `n/a`.
* **`config/drum-maps/bfd3.yaml` is a stub.** The note numbers depend on the
  per-song BFD3 preset and have to be read off the plugin; until then it falls
  back to General MIDI.
