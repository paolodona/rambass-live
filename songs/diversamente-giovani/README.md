# Diversamente Giovani

15 tracks. **The drum tracks were recorded**, so they can be imported rather
than reconstructed:

* if a MIDI drum performance exists, `rambass drums import <song> <file.mid>`;
* if only the recorded drum *audio* exists, put it in `stems/drums.wav` and go
  straight to `rambass drums transcribe` — no demucs needed, and transcription
  from an isolated close-miked track is far more accurate than from a separated
  stem.

`drums.origin: recorded` and `status.stems: n/a` are already set in every
`song.yaml`.

## Getting the audio

`sources.yaml` lists every master with its Drive file id. Masters are in
Drive under `MASTER/44100 24 bit PCM/` (MP3 and 16-bit versions are there too).
Download each one into its song's `source/` folder, keeping the filename that
`source.audio` names — `rambass check` will tell you which are still missing.

Audio is gitignored, so this is a per-machine step.

## Caveats to sort out

* **Titles came from the master filenames**, not the sleeve. `ForMayGrana`,
  `Itturfiatrugoy` and `Mandami un Faxe` are presumably deliberate puns, but
  check all fifteen and fix `sources.yaml` plus the `song.yaml` where wrong.
* **Track 13 has two masters** (`MST1` and `MST2`). `MST2` is referenced;
  confirm which one was released.
* **Tracks 01 (INTRO) and 15 (OUTRO)** are short pieces that most likely play as
  straight album audio. If so, mark their drum stages `n/a` rather than building
  parts for them.
