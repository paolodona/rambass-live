# Tutti in Fila

11 tracks. **The original drum tracks are gone**, so the drums have to be
extracted from the stereo mix and rebuilt:

    rambass analyze <song> --write     # what tempo did they actually play?
    rambass stems <song> --drums-only  # demucs pulls the drums out
    rambass drums transcribe <song>    # onsets -> MIDI, a first pass
    rambass drums clean <song>         # de-flam, quantise, shape velocities

Then open it in Reaper and fix the fills by hand against `stems/drums.wav`.
Expect the transcription to get the kick/snare/hat backbone right and the fills
wrong — see [../../docs/drums.md](../../docs/drums.md) for what fails and why.

`drums.origin: extracted` is already set in every `song.yaml`.

## Getting the audio

`sources.yaml` lists every track with its Drive file id, WAV and MP3. Download
each into its song's `source/` folder keeping the filename that `source.audio`
names; `rambass check` reports what is still missing. Prefer the WAV — demucs
separation off a 2005 MP3 is measurably worse, and separation artefacts are what
the transcriber then has to work with.

## Caveats to sort out

* **Track 10, "La Canzone Del Solero", is MP3 only** — no WAV anywhere in the
  Drive folder. Look for the WAV before starting on it; if it does not exist,
  expect the worst separation and transcription of the album and budget more
  hand-editing.
* **Track 02's master is named `02 I PoohFFI`** — confirm the real title.
* **Track 05 "Intro Vibratore" runs into track 06.** Consider treating the two
  as a single backing track with one continuous click rather than two regions
  the band has to hit separately.
* **Track 07 is about a minute long.** Check whether it is even in the set.
