# Lyrics

**Goal: every song has a lyric video running behind the band.** 26 songs; six
already have hand-timed cue files and finished videos. This is about producing
the other twenty in the same format.

## The format is SRT, and that is deliberate

Everything else in this repo is positioned in bars. Lyric cues are the one
exception, and they are stored as **`lyrics.srt`** in each song folder, in git.

The reason is what the existing files look like. `02 ForMayGrana.srt` is 126 cues
for a four-minute song — a short phrase at a time, timed to the millisecond, with
the shouted lines in caps (`CHE PALLE!`, `I RAMBAAAA!!!`), held notes trailing off
in ellipses, and asides in brackets (`(la terza??)`):

```
62
00:02:06,560 --> 00:02:06,989
Ad ogni

63
00:02:06,990 --> 00:02:07,279
donna

64
00:02:07,280 --> 00:02:07,929
che vedo
```

That is a performance transcription, not a musical anchor. Snapping it to a bar
grid would throw away the timing someone sat and tapped in, and gain nothing —
the video is rendered against the same audio the cues were written against. SRT
is also simply what subtitle software wants.

So the rule is: **sections, patch changes and tempo in bars; lyric cues in
seconds.** If a song's timing base moves, `rambass lyrics shift` moves the cues
with it rather than re-deriving them.

## Bringing in the six that exist

```
rambass lyrics import 02-formaygrana "02 ForMayGrana.srt"
```

Copies it to `songs/diversamente-giovani/02-formaygrana/lyrics.srt`, points
`video.lyrics` at it, prints the stats and flags anything a renderer would not
warn about. Existing files are the source of truth — do not regenerate them.

The Drive file ids for all six are recorded in
`songs/diversamente-giovani/existing-work.yaml`.

## Producing the missing twenty

The words already exist. Most songs in the recording backup have a Google Doc
with the **complete lyrics**, plus the tempo, key, chords and section names — see
`existing-work.yaml` for the doc id per song. What is missing is only the
*timing*.

That makes the job much easier than transcribing from scratch, and it changes the
right approach:

### 1. Get the words into a file

Paste the lyrics from the doc into `lyrics.txt` in the song folder. Strip the
chords and section headings; keep the lines as they are sung.

### 2. Draft the timing

```
rambass stems 04-bambolina                          # isolate the vocal
rambass lyrics transcribe 04-bambolina \
    --prompt-file songs/diversamente-giovani/04-bambolina/lyrics.txt
```

Whisper runs on `stems/vocals.wav` and writes `lyrics.draft.srt` — never over
`lyrics.srt`, so a hand-timed file cannot be clobbered.

Two things make a large difference and are worth doing:

* **Run it on the separated vocal, not the full mix.** `rambass stems` produces
  `vocals.wav`. The command warns you if it falls back to the mix.
* **Pass the real words with `--prompt-file`.** This biases the decoder towards
  the band's spellings — `Itturfiatrugoy`, `ForMayGrana`, `simpaticoni` — instead
  of the nearest dictionary word.

### 3. Fix it by hand

Sung Italian over a full band is close to the hardest case there is for speech
recognition. Expect it to get the shape and rough position of each phrase, and
to mangle invented words, dialect, shouted asides and anything buried in the mix
— which for this band is a lot of the funniest lines. The point of the draft is
to skip the *typing*, not the listening.

Open `lyrics.draft.srt` in a subtitle editor alongside the audio, fix it, save as
`lyrics.srt`, then:

```
rambass lyrics check 04-bambolina
```

### 4. Match the house style

The existing six set the conventions, and new files should match them:

| | |
|---|---|
| one short phrase per cue | two to five words, not whole verses |
| cues butt up against each other | `...,279 --> ...,280`, no gaps mid-phrase |
| caps for shouted words | `CHE PALLE!`, `una TETTA!` |
| ellipses for held or split lines | `E non ti ASS...` / `...omiglia neanche un po.` |
| brackets for asides | `(la terza??)` |
| real Italian orthography | `è`, `già`, `più` — not stripped to ASCII |
| CRLF line endings | what `rambass lyrics export` writes by default |

`rambass lyrics check` enforces the mechanical parts: overlaps, empty cues,
lines over 42 characters, more than three lines at once, cues under 0.2 s, and
cues running past the end of the song. The 0.2 s floor is deliberately low —
the existing files run rapid-fire syllable cues down to 0.29 s, and that is a
style choice, not a mistake.

## Exporting

```
rambass lyrics export 04-bambolina --format srt vtt lrc ass txt
```

| format | for |
|---|---|
| `srt` | subtitle software, video editors, YouTube |
| `vtt` | browser-based and online editors |
| `lrc` | karaoke players (no end times — a gap becomes a bare timestamp) |
| `ass` | styled burn-in; what `rambass video render` uses |
| `txt` | the words alone, for proof-reading or the sleeve |

`--count-in` shifts everything by the song's count-in, for cues that were timed
against the album master but will play against a backing track that starts
earlier. `--offset` shifts by an arbitrary amount.

## Rendering the video

```
rambass video ass 04-bambolina        # subtitles only — read this first
rambass video render 04-bambolina     # the MP4
```

`video render` burns the cues over a background, with image cues on top if a
`lyrics.md` exists alongside — **words from the SRT, pictures from the
markdown**. See [video.md](video.md).

## What made the existing videos?

Not determinable from what is in Drive. The evidence:

* output is QuickTime `.mov`, ~2.4 Mbps at 480p, no audio track
* each song has a large `Lyrics ORIGINAL.mp4` master plus resolution-labelled
  derivatives (`480p`/`720p`/`1080p` `.mov`, `YouTube 640x480`/`1920x1080` `.mp4`)
* the SRT files carry no styling tags, so the look was applied by the renderer
* other files in the same folders are macOS screenshots, so a Mac toolchain

That pattern — an "ORIGINAL" master plus preset-named transcodes — says authoring
happened in one tool and the variants came from a second, transcoding step. It
rules out HandBrake as the authoring tool (it cannot write `.mov`), and a Mac
`.mov` export with SRT import fits DaVinci Resolve, Final Cut or iMovie. That is
as far as filenames go.

**The file itself knows.** QuickTime and MP4 carry the writing application in
their metadata:

```
rambass video probe "02 For My Grana Lyrics ORIGINAL.mp4"
```

Look at `encoder`, `com.apple.quicktime.software` and `handler_name`:

| what you see | what wrote it |
|---|---|
| `handler_name: Core Media Video`, `com.apple.quicktime.software` | an Apple app (iMovie, Final Cut, Compressor, QuickTime) |
| `encoder: Lavf…` / `handler_name: VideoHandler` | ffmpeg, or anything wrapping it |
| `DaVinci Resolve` in the tags | Resolve |
| `Adobe` / `Premiere` | Premiere or Media Encoder |

Worth doing once: knowing the tool means the twenty new videos can be made the
same way as the six that exist, rather than in a second style.

That said, **you do not need the answer to continue.** The repo produces the same
kind of artifact from the same SRT — `rambass video render --width 854 --height
480` gives a silent 480p burn-in — so if the original tool turns out to be
inconvenient, nothing is blocked.
