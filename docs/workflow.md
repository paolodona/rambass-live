# Workflow

## The one idea to keep in your head

**Everything is positioned in bars, not seconds.** A section marker, a lyric
cue, a patch change: all written as "bar 25". The bar-to-seconds conversion
happens once, in `rambass/timeline.py`, from the tempo in `song.yaml`.

That is what makes it survivable to decide, three songs in, that a track should
sit at 126 rather than 124 BPM: change one number, regenerate, and the click,
the drum grid, the Reaper markers, the lyric timings and the pedal changes all
move together. Anything written in seconds would silently drift out of place.

## The two albums need different work

| | Diversamente Giovani | Tutti in Fila |
|---|---|---|
| drums arrive as | part of a finished backing track | only inside the stereo mix |
| `drums.origin` | `backing-track` | `extracted` |
| drum pipeline | **not needed** — `stems`/`drums_midi`/`quantize`/`kit` are `n/a` | the whole job |
| demucs needed | no | yes |
| tempo | **known** from the band's production folders | must be detected |
| lyric cues | 6 songs hand-timed already | none yet |
| Reaper projects | 7 already built — import them | none |
| starting point | `rambass reaper import`, `rambass countin` | `rambass stems` + `drums transcribe` |
| realistic effort | minutes per song | an hour or two per song |

**Diversamente Giovani does not go through the drum pipeline at all.** The band
mixed live bases straight out of the album sessions, drums included, for seven
songs; the other eight need the same mixing treatment, not a reconstruction. So
for that album the sequence below collapses to: import the Reaper project, fix
the count-in, do the lyrics. Steps 2, 3 and 4 are for Tutti in Fila.

`songs/diversamente-giovani/existing-work.yaml` is the inventory of what already
exists for the newer album — tempos, drum stems, BFD3 presets, lyrics documents,
SRT files, rendered videos, Reaper projects — with the Drive id of each. Read it
before starting on a Diversamente Giovani song; most of the early steps are
already done and the job is to import them, not to repeat them.

The end state for **both** albums is the same: every song has a click-locked
backing track, automatic GX-100 patch changes, and a lyric video running.

## Per-song sequence

```
rambass new "Titolo" --album tutti-in-fila --track 4
```
Then drop the original mix into `songs/tutti-in-fila/04-titolo/source/`.

### 1. Find the tempo

```
rambass analyze 04-titolo --write
```

**There is no decision to make here, and that is deliberate.** Every song is
re-programmed to **one fixed tempo**, always. The reason is what the drum track
*is*: it goes to the gig as the backing track and the band plays to it. The album
mix is only the source of the pattern — it is not mixed into anything, and after
the transcription it is never heard again. So a take that breathes is not
something to follow with a tempo map; the drums are metronomically correct and
the band follows them.

That closes the old "tempo map or fixed click?" fork, but it makes one number
critical, which is why `analyze` works the way it does:

* **the tempo has to be precise, not rounded.** Every hit is placed against it,
  so 1% out is three seconds of slip across a five-minute song and the
  transcription is scrap. `librosa` on its own cannot give you this: it reports
  tempo from tempogram bins at `60 * sr / (hop * k)`, so near 117 BPM the only
  values it can return are about 112.4, 117.5 and 123.1. A bin centre is not a
  measurement. `rambass analyze` refines it against the recording to about a
  hundredth of a BPM, and that refined value is what `--write` stores.
* **the wander is reported in milliseconds, and is only a warning.** It says how
  far the band sat from that fixed grid, window by window. Nothing needs doing
  about it — but once it passes half a sixteenth (`budget` in the output) the
  transcription will snap the occasional hit to the wrong subdivision, and you
  should read the `drums clean` report with that in mind.

```
tempo           116.03 BPM  (rounded: 116.03)
bars (4/4)          147
wander              110 ms peak-to-peak, 33 ms rms  (budget 65 ms)
verdict         wanders past half a sixteenth — expect a few hits snapped to the wrong subdivision
```

Round the tempo to something tidy at the end if you like — everything downstream
is anchored to bars, so re-rendering moves with it.

### 2. Separate the drums (extracted songs only)

```
rambass stems 04-titolo --drums-only
```

`--drums-only` is two-stem mode: much faster, and it also produces
`no_drums.wav`, which is the band-minus-drums bed you will A/B the new
programmed part against. Use `--model htdemucs_ft` (the default) when quality
matters; it is roughly four times slower than `htdemucs` and noticeably cleaner
on cymbals. On Apple Silicon add `--device mps`.

### 3. Get the drums into MIDI

Extracted:
```
rambass drums transcribe 04-titolo
```
Recorded:
```
rambass drums import 04-titolo /path/to/drums.mid --channel 10
```

`transcribe` detects the first downbeat and shifts the result so MIDI bar 1 is
musical bar 1. If it guesses wrong, pass `--offset <seconds>` explicitly — or
better, fit and store it with `rambass align` (step 6 below), which is where the
anchor lives.

### 4. Clean it up

```
rambass drums clean 04-titolo --humanize 4 --accent-kick 112 --accent-snare 104
```

This is the step that turns a take into a backing track. It de-flams, quantises
and shapes velocities in one pass, and prints how far it had to move things —
a mean move over about 40 ms means the tempo is wrong, not the drummer. See
docs/drums.md.

It also applies any **declared backbeat articulation** from the section list. If
a section says `backbeat: sidestick`, beats 2 and 4 of every bar in it come out
as a rim click at one even velocity, whichever stem the detector happened to find
them in. That exists because measurement cannot settle it — see Stage 5 of
docs/drums-rebuild.md — so it is set by ear, once, in `song.yaml`.

### 5. Sections, then the pattern vote

```
rambass section 04-titolo 22.3 verse-2      # Reaper bar.beat; it converts
rambass sections 04-titolo                  # review the list against the drums
rambass drums consolidate 04-titolo         # Stage 6
```

`consolidate` replaces each section with the pattern its own repetitions agree
on. It **removes the fills and the crashes by design** — a fill is the bar that
does not repeat, so no threshold keeps it — which is what step 6 is for.

### 6. Put back what was removed, and record it

```
rambass drums missing 04-titolo     # the checklist, in Reaper bar numbers
rambass drums restore 04-titolo     # apply drums.additions / drums.removals
```

`missing` writes `qa/missing-hits.md`: every accent and fill the vote dropped,
plus the section starts whose crash is hiding inside an open hi-hat. Work down
it by adding entries to `drums.additions` in `song.yaml` and re-running
`restore`. **Put the edits there rather than drawing them in Reaper** — the MIDI
item is regenerated by the next `transcribe`, and `song.yaml` is not.

### 7. Hear it against the original (optional, practice only)

```
rambass align 04-titolo --fit --warp
```

Fits `practice/align.yaml` from detected beats and writes
`practice/no_drums-aligned.wav`: the band minus drums, time-warped onto the fixed
grid. `reaper build` puts it on a muted **REF aligned** track. A single offset is
not good enough for this — see docs/practice-tracks.md.

### 8. Click, pedalboard, project

```
rambass click 04-titolo
rambass gx100 midi 04-titolo
rambass reaper build 04-titolo
```

Then in Reaper: **File > New Project**, **Actions > Show action list >
ReaScript: Run ReaScript (EEL2 or Lua)…** → `reaper/scripts/rambass_build_song.lua` → Run, and pick
the `.rbs` file. See docs/reaper.md.

### 9. Video

```
rambass video ass 04-titolo        # subtitles only, fast, proof-read this first
rambass video render 04-titolo     # the MP4
```

### 10. Track it

```
rambass mark 04-titolo kit done
rambass status
rambass stale 04-titolo            # is anything on disk out of date?
```

`stale` is the one to run when you come back to a song after a gap. It knows
three ways a derived file goes bad — an input changed, `song.yaml` changed, or
**the code that produced it changed** — and only the first is the one a timestamp
would catch. The third is real: a drum MIDI can sit there, newer than everything
it was built from, holding an articulation two commits of fixes have since
corrected. It reports and prints the commands; it never rebuilds on its own,
because a re-transcription can change the part under you.

## Whole-show commands

```
rambass setlist gig                    # running order with timings
rambass reaper setlist gig             # one Reaper project, region per song
rambass gx100 sheet --setlist gig      # the sheet that gets taped to the pedal
rambass status --markdown              # progress board for the band chat
```

## What to commit

Commit `song.yaml`, `lyrics.md`, the `.mid` files, the `.ass` files, and any
config you changed. Audio and video are gitignored — they are rebuildable, and
the repository stays small enough to clone on a phone at the venue.
