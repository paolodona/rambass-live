# songs/

One folder per song, grouped by album:

    songs/<album>/<NN>-<slug>/
      song.yaml      the manifest — the only file you edit by hand
      lyrics.md      lyric and image cues for the video
      source/        the original mix (MP3/WAV) — not in git
      stems/         demucs output: drums.wav, bass.wav, ... — not in git
      midi/          drums-raw.mid, drums-quantized.mid, gx100.mid — IN git
      render/        click.wav and the bounced backing track — not in git
      video/         the .ass subtitles and the rendered MP4 — .ass is in git

Audio and video are gitignored on purpose: they are large, they are binary, and
they can always be rebuilt from `song.yaml` plus the source mix. The MIDI and
the manifests are the actual work, and those are tracked.

Create a song with `rambass new "Titolo" --album <album>`; `songs/_template/`
is the fully commented reference for what ends up in `song.yaml`.
