# Notes for Claude working in this repo

## What this is

Tooling to turn two albums by **Ramba S.S.** into live backing tracks: the
drummer cannot play the gig, so every drum part becomes a click-locked,
quantised backing track, with lyric/image video and automatic BOSS GX-100 patch
changes. The DAW is Reaper. Read `README.md` and `docs/workflow.md` first.

The band's own site is **<https://rambass.com/>** — Italian, sections
`Musica / Foto / Band / Storia / Video / Contatti`. Useful for band photos
(`Foto`) when a song wants an image behind its lyrics or a title card, for the
discography, and for the live footage on their YouTube channel. What it does
**not** have: lyrics pages, a press kit or any download section — so it is not a
source for the missing Tutti in Fila words, which still have to be transcribed by
ear. Anything taken from it belongs in `video/assets/` (gitignored) with the
source noted in the song's `notes:`.

## The one invariant

**Positions are in bars, never seconds.** Sections, lyric cues, patch changes,
tempo changes — all anchored to bars, converted once in `src/rambass/timeline.py`
from the tempo in `song.yaml`. If you add a feature that stores a time in
seconds in a manifest, you have broken the ability to change a song's tempo, and
that will not show up until someone changes one.

**The one deliberate exception is lyric cues.** They live in `lyrics.srt`, in
seconds, because the band's existing hand-timed files are 100+ millisecond-timed
phrase cues per song authored against a specific master — a performance
transcription, not a musical anchor. See `docs/lyrics.md`. Do not "fix" this by
moving them onto bars; `lyrics.shift`/`scale` exist for when the timing base
moves. Bar-anchored `lyrics.md` still exists for songs written from scratch, and
`lyrics.from_bar_cues`/`to_bar_cues` bridge the two.

The **second** such exception is `practice/align.yaml`, which maps bars (and
beats) to seconds *in the original album recording* so a reference can be
time-warped onto the fixed grid. Same justification as `lyrics.srt` — the seconds
describe an immutable audio file, not a position in the musical grid. It is built
(`align.py`, `rambass align --fit --warp`) and it stores **only the source side**;
the target side is computed from `Timeline` at build time, or a BPM edit silently
stops re-warping. Three things there were measured and are settled: a single
offset is not enough (Manlio drifts −88..+258 ms), anchoring every *beat* beats
every bar (p90 38 ms against 58 ms and 107 ms), and the residual has to be
leave-one-out or a per-beat fit reports a meaningless 0 ms. A fourth is settled
now too: **the warp preserves pitch and must never resample.** `warp_samples`
originally read the source at `position * rate`, which moves rate and pitch
together by `12*log2(rate)` semitones — over Manlio's 308 per-beat segments
(rates 0.929–1.091) that is 2.78 semitones peak to peak wobbling once per beat,
which made the file unusable for the one job it has. It is WSOLA in numpy now,
and the trade is measured: the bass fundamental sits 1.2 cents off the source at
the median (37.2 before), and the warped backbeats land at p90 34.7 ms against
30.4 ms, because WSOLA can displace a transient by up to its 10 ms search
window. Do not "simplify" it back to interpolation, and do not reach for a phase
vocoder (smears the transients that are the whole signal here) or ffmpeg
`atempo` (one fixed rate per instance, so 308 invocations, and it drags the
render out of the pure-numpy tier). Checked on a second song of the album, as
the rule below requires: Tutti in Fila stretches three times as hard (rates
0.808-1.213, 7.02 semitones of swing if resampled) and lands 0.3 cents / p90
2.3 cents off the source with backbeats at p90 23.0 ms against 19.1.
See `docs/practice-tracks.md`.

**Bar numbers spoken out loud are Reaper ruler readings; bar numbers in
`song.yaml` are musical.** Paolo works from Reaper's ruler, where bar 1 is the
first count-in bar, so when he says a section starts at `22.3` he means Reaper
bar 22 beat 3 — musical bar 20 with a 2-bar count-in. Subtract
`count_in.bars` before writing anything down, echo both numbers back when
confirming, and never store the ruler number: `count_in.bars` is an editable
setting, so a stored screen position slides every section the day it changes.
The one place the ruler number belongs is a **Reaper marker label**, which is
numbered `section.bar + count_in.bars` so the marker list matches the ruler
underneath it. Same split as everything below — one clock for the music, one
for the screen, converted at the boundary and nowhere else.

`Timeline` distinguishes two clocks and so must you:

* `bar_beat_to_seconds()` — from the **musical** zero (bar 1 beat 1); the
  count-in is negative
* `audio_time()` — from the **first sample** of the rendered audio; this is what
  goes into Reaper, ffmpeg and subtitle files

Getting these two mixed up shifts everything by the count-in length.

## Architecture

Layered so the cheap deterministic parts have no heavy dependencies:

| module | job | deps |
|---|---|---|
| `project.py` | repo layout, song lookup, slugs | — |
| `timeline.py` | bars ↔ seconds ↔ quarter notes | — |
| `manifest.py` | `song.yaml` load/save/validate | pyyaml |
| `drummap.py` | canonical instrument names → note numbers | pyyaml |
| `midiio.py` | `Hit`/`DrumPerformance`, MIDI read/write with tempo map | mido |
| `quantize.py` | de-flam, quantise, humanise, velocities — pure functions | — |
| `reaper.py` | `.rbs` build-script generation, **and reading existing `.RPP`** | — |
| `lyrics.py` | SRT/VTT/LRC/ASS cues, editing, checking, Whisper drafting | — |
| `gx100.py` | pedalboard MIDI, program map | mido |
| `video.py` | lyric cue parsing, ASS, ffmpeg render | — |
| `setlist.py`, `status.py`, `doctor.py` | running orders, progress, diagnostics | — |
| `arrange.py` | reviews a running order against set-list practice | — |
| `restore.py` | Stage 7: declared articulation, the missing-hits ledger, hand edits | — |
| `provenance.py` | which derived files are stale, and which of three reasons | pyyaml |
| `align.py` | bars ↔ seconds *in the recording*, and warping onto the grid | numpy |
| `audio.py` | ffmpeg decode/encode, WAV write, loudness | numpy |
| `analyze.py` | tempo/beat/drift detection | **librosa** |
| `transcribe.py` | drum stem → hits | **librosa** |
| `stems.py` | demucs wrapper | **demucs** |

Keep it that way: never import librosa, scipy or demucs at module top level in
anything outside `analyze.py` / `transcribe.py` / `stems.py`. Use
`audio.require_module()` so a missing extra produces an install hint rather than
an ImportError traceback.

## Conventions

* Drum data is carried as **instrument names** (`kick`, `hihat_open`), not note
  numbers. Note numbers only appear at MIDI write time via a `DrumMap`. That is
  what makes retargeting to a different kit free.
* `quantize.py` functions are pure: performance in, performance out, plus a
  report dict. Do not make them touch disk.
* Anything random takes a **seed** (see `humanize`). Re-running the pipeline
  after changing one setting must not reshuffle the song.
* CLI errors: raise `ProjectError` or `AudioError` with a message that says what
  to do next. `cli.main()` turns those into a one-line message and exit code 2;
  anything else becomes a traceback, which is a bug.
* No `.RPP` **writing**. Reaper projects are built by the ReaScript from a `.rbs`
  build script — see `docs/reaper.md` for why. `.RPP` *reading* is fine and is
  implemented in `reaper.parse_rpp`, validated against the band's real Reaper
  6.82 projects (`tests/fixtures/live-project.RPP` mirrors one).

## Facts that were verified, don't re-guess them

**The programmed drums are metronomically correct, at one fixed tempo per song.
This is settled — don't reopen it.** Paolo's reasoning: the drum track *is* the
live backing track, the band plays to it, and the album mix is only the source of
the pattern — it is never mixed into anything and is never heard after the
transcription. So an original take that breathes is not a problem to be followed
with a tempo map, and `tempo.changes` is not the answer to a wandering take. Do
not offer the "tempo map or fixed click?" choice again; `docs/workflow.md` step 1
records why it is closed.

The consequence is that **the tempo value has to be precise**, because every hit
is placed against it and 1% is three seconds of slip over five minutes. Verified
the hard way on Tutti in Fila: `librosa` reports tempo from tempogram bins at
`60 * sr / (hop * k)`, so near 117 BPM the only values it can *return* are about
112.4, 117.5 and 123.1 — a bin centre, not a measurement, and it moves when you
change `hop`. The real tempo of that song is 116.03. `analyze.refine_tempo`
sharpens the coarse estimate with a comb/DFT fit against the recording, and
`analyze.pulse_wander` reports how far the band sat from the fixed grid in
milliseconds. Both are pure numpy so they are tested without librosa. The old
per-chunk `drift_bpm` metric was removed: its resolution near 117 BPM was 5 BPM,
so it could never have answered the question the docs asked of it.

The GX-100 numbers in `gx100.py` and `docs/gx100.md` come from the official
*GX-100 MIDI Implementation* ver 1.10 (2022-03-03): bank select is CC#0 with
values **0–2 only** followed by CC#32 = 0; program change is resolved through
the pedal's own `MENU:MIDI:PROGRAM MAP`, so **a PC number does not name a
memory**; memories are `U01-1`…`U50-4` then `P01-1`…`P25-4` (300 total); CC#1–31,
#33–63, #64–95 are assign sources; MIDI clock is followed when `SYNC CLOCK` ≠
`INTERNAL`.

**Diversamente Giovani does not use the drum pipeline at all.** The band mixed
finished live backing tracks ("BASE") straight out of the album sessions with the
drums already in them. Every song on that album is `drums.origin:
backing-track`, which marks `stems`/`drums_midi`/`quantize`/`kit` as `n/a`. Do
not propose separating, transcribing or voicing drums for it — the remaining
songs need a base *mixed* in the album session, which is not a job for this repo.
Isolated drum stems, Aerodrums `.aer` files and per-song BFD3 presets are
recorded in `existing-work.yaml`. For the seven finished bases they are
provenance only. For the **six songs still needing a base** (Bambolina,
Orologiaio, Il Cellulare, Per Niente Stanca, Superman, Mandami un Faxe) they are
the drum source for that mixing session, and the album's electronic drums
(Aerodrums → BFD3) mean the best source is a **re-render from the BFD3 preset
plus the `.aer` capture** — five of the six have a preset — then the isolated
stem, then demucs extraction as a distant fallback. See "drum sources for the six
unmixed songs" in that file. This is still not a reconstruction job: nothing on
this album gets separated into part stems, transcribed, quantised or re-voiced.

**One drummer, one kit, one studio — for the whole of Tutti in Fila.** So a
level- or velocity-based heuristic tuned on one song of that album is expected
to carry to the other ten, and "it works on Manlio" is real evidence rather
than a coincidence. Two consequences worth acting on: a new detection threshold
should be checked against more than one song of the album before it becomes a
default, and a per-song override is a smell — if Manlio needs a different
number from Tutti in Fila, the rule is probably wrong rather than the song
unusual. Musical structure is of course still per-song; this is about levels,
decay and separation behaviour, which are properties of the kit and the room.

**None of that transfers to Diversamente Giovani**, whose drums are electronic
(Aerodrums → BFD3): no room, no bleed between close mics, and velocities that
came out of a sampler rather than a stick. Do not calibrate anything against
Tutti in Fila and then reason about that album with it. In practice this
rarely comes up, because that album is `drums.origin: backing-track` and never
enters the drum pipeline at all — but it matters for the six unmixed songs if
demucs extraction is ever used as the distant fallback described above.

`config/drum-maps/bfd3.yaml` is a **stub with blank note numbers** that falls
back to General MIDI — the real numbers depend on the per-song preset and must be
read off the plugin. Don't fill it in with invented values; same for
`_custom-template.yaml`.

Tempos for Diversamente Giovani in `songs/diversamente-giovani/existing-work.yaml`
came off the band's own production folder names and were cross-checked against
their lyrics docs. They are better than anything `rambass analyze` would produce
— don't overwrite them with detected values. The same file carries the band's own
review punch-list per base; `rambass countin` exists because four of the seven
finished bases are missing their count-in.

**An album track is longer than the song. `bars` is the band's part.** Many of
these recordings carry vocal material outside what the band plays — a sung
intro before the count, a held note after the last hit — and it is not played
at the gig. Manlio is the measured example: the final snare lands at musical
bar 78 beat 2, the band stops dead, and after two seconds of *digital silence*
there is a sung note running to bar 80. A drum stem covers the whole track, so
the transcriber duly found that note and reported it as a lone hi-hat at the
velocity floor in bar 79 — which reads exactly like a phantom and is nothing of
the kind. It is real audio, correctly detected, and simply not part of the
drum part.

Two rules follow. **Never derive `bars` from the audio duration** — nothing in
the pipeline does, `Timeline.bars_for_duration` is not wired to it, and it must
stay that way, because on these songs the file is longer than the music and the
difference is exactly the material to discard. Measure it, by ear, from where
the band stops. And **`bars` is what bounds the part**: `drums clean` trims to
it at both ends (a vocal intro sits at negative musical time, since the anchor
puts bar 1 at the band's entry), so an out-of-scope detection is removed by
fixing the manifest rather than by hand-editing MIDI that the next
re-transcription would regenerate. `bars: 0` means unmeasured, which is not the
same as zero, and trims nothing.

**The video track carries exactly one item per song, and it starts at the region
start.** A rendered lyric video runs on the *audio* clock, so it already contains
the count-in; starting it anywhere else drifts the words by the count-in length.
The title card is therefore the video's own lead-in (filling the count-in, which
has no cues on it) for a song that has cues, and a standalone
`video/<slug>-card.png` held for the whole region for one that does not. Do not
"improve" this by laying a card item over the head of a lyric video — that asks a
question about Reaper's compositing order that nothing here needs to answer.

**The two a cappella songs need a title card and nothing else.** `a-cappella` is
not an unfinished state: `reaper.required_artifacts` returns `("screen",)` for
them, so they count as complete once the card exists. Don't add backing track,
count-in or patch-change requirements back for them.

**Practice tracks are a side goal and must never gate the gig.** Per-member
minus-one MP3s and "the original mix with the new drums in it" are scoped in
`docs/practice-tracks.md` and not yet implemented. Two facts there are decisions,
not guesses: **Diversamente Giovani needs only `vocals` and `bass` separated from
the master mixes** — Vikingo practises against the previous gig's backing tracks,
so no guitar reconstruction and no time-warping happen on that album at all; and
**Tutti in Fila may have all its album guitars removed**, because the gig base
for those songs will be new drums plus guitar layers Paolo re-records. Don't
reintroduce guitar-layer preservation as a problem to solve.

The rules that scope carries:
no practice command writes to `render/` or edits a musical field in `song.yaml`;
practice stages are **not** added to `manifest.STAGES`, because the board's
denominators measure the show; and a practice track is allowed to be imperfect
where a base is not. The gig session and the rebuilt drums are the deliverable
that has to be right.

**The verses of Manlio are played side-stick, and that is declared, not
detected.** Measured on the kit mix: the 180-500 Hz share at a verse backbeat is
0.08-0.10 against 0.40-0.48 at a real snare backbeat, with the snare stem 14-19
dB *quieter* than the hat stem instead of 16-31 dB louder. A rim click never
reaches the shell, so it has no body, and being nothing but a high-frequency
transient the separator hands most of it to the hi-hat. `label_sidesticks`
catches verse-1 and verse-3 from that. It does **not** catch verse-2, and the
reason is not a tuneable constant: across all 941 hat and cymbal stem detections
the hat stem's 5-11 kHz share is 0.17 at a verse backbeat and 0.85-0.96
everywhere else, but verse-2's clicks read 0.84-1.00 — a hi-hat's own spectrum.
Paolo can hear a cross-stick in context; the spectrum there cannot. So
`sections[].backbeat: sidestick` declares it, and that is **arrangement
structure, not a threshold override** — the same category as the section list or
`drums.subdivision`. Do not try to detect verse-2 acoustically; it has been
tried.

The declared backbeat is stamped at **one even velocity** and the reason is a
scale error, not laziness: `scale_velocities` works per instrument, so a hit
found in the hat stem carries a number meaning "loud for a hi-hat" and renaming
it to a rim click makes that number meaningless. The reference is the median of
whatever was measured on the right instrument (v45 on Manlio, which is the floor
and correct — a rim click here is 25-30 dB below the same drummer's snare and the
velocity range only spans 30 dB). Its audible level is a kit decision, and it
**lives in `song.yaml` as `drums.backbeat_velocity`**, not in a flag. Paolo heard
the first result and said the clicks were "barely audible": all 50 on Manlio came
out at v45 under hi-hats at 75-98 and snares at 109. Manlio now declares
`backbeat_velocity: 96`. `drums clean --backbeat-velocity` still overrides for a
listen, but a value only in a flag is gone at the next re-run — same argument as
`drums.subdivision`, and `rambass stale` reports the field when it changes. 0
means "not decided", which is not velocity 0: the measured median is used then.

**A hi-hat is never loudness evidence.** `suppress_cross_stem_bleed`'s `exclude`
list means "quiet by nature", and that disqualifies an instrument from *both*
roles — neither deleted as bleed nor believed as the loud partner that deletes
something else. Trusting them in one direction only is what deleted 99 real hits
on Manlio, 44 of them kicks, because a rim click's leak into the hat stem read as
a v122 accent. A closed hat cannot out-shout a kick.

**Crash-versus-open-hat cannot be settled acoustically on this material, and
four attempts are on record.** `split_cymbal_runs` lists three (decay at
200/500/900 ms, attack centroid, hat coincidence, cymbal/hat level ratio, long
sustain at 0.6-1.2 s) and the fourth is decay at +250 ms: across the 243
measurable cymbal-stem hits on Manlio the median is −16.8 dB, the p90 is +3.0,
and 114 of 243 ring at or above −14 dB. Long ring is the *normal* case in that
stem because an open hat's wash lives there too. What does separate is **musical
position**: the ten cymbal hits within 0.6 beat of a section start decay at a
median +3.4 dB against −19.2 dB for the other 233, and nine of ten are v83+. So
`restore.crash_candidates` reports boundary crashes and everything else goes on
the checklist. Do not propose another spectral test.

**Hand edits go in `song.yaml`, never into the Reaper MIDI item.**
`drums.additions` and `drums.removals` are bar-anchored, reapplied by `rambass
drums restore`, and ticked off by `rambass drums missing`. A crash drawn into the
MIDI item is gone at the next `drums transcribe`, which is how a Stage 7 pass
gets silently redone from scratch and how somebody loses track of what is
finished. `rambass stale` reports a file that differs from what the command wrote
as `edited` rather than `stale`, precisely because the risk there is the opposite
one.

**A derived file can be stale three ways and only one of them is a timestamp.**
An input changed, `song.yaml` changed, or **the code that produced it changed** —
and the third is the one that actually happened: a `drums-quantized.mid` two
commits old, newer than everything it was built from, holding an articulation
since corrected. `provenance.py` stamps all three and `rambass stale` says which
moved. It **never** rebuilds: a re-transcription is minutes of CPU and can change
the part under you. Note also that `missing` and `unknown` do not cascade
downstream — only `stale` does — because otherwise a song where nothing has been
built is a wall of red and the report gets ignored.

**Section names carry meaning, and the rule is Paolo's.** *"If the sections are
named exactly the same, use exactly the same part. If they are the same name
pattern (eg: verse-2 vs verse-3) check the structure but should not match
exactly."* So:

* **Identical names are one part.** `consolidate` pools every section sharing a
  name into a single vote and stamps the result across all of them, so they come
  out bit-identical. Give two sections the same name only when you mean that.
* **A name *family* is not a pooling key.** `verse-1`, `verse-2` and `verse-3`
  vote separately and are free to differ, because they are structurally alike
  but the later ones add hits and swap hits for the dynamics of the song.
  Measured on Manlio: all three verses put the snare in at their own bar 9, but
  verse-2 and verse-3 also thin the closed hats from ~11 a bar to 6 where
  verse-1 keeps them. Same shape, different part. Never pool on a prefix.
* A section that steps up inside itself gets its own name (`verse-2-lift`),
  because a pattern present in 4 of 12 bars is 33% against a 0.55 threshold and
  Stage 6 would delete it and stamp the quiet pattern over the lift.

**Sections need not start on a bar line, and `consolidate` handles that itself.**
Paolo: *"with odd timing we will rarely fit into a .1 start of section
generally"*. Manlio's verse-2 runs from bar 20 beat 3 to bar 32 beat 3. The
repetitions still tile the **bar** grid — a one-bar figure repeats every bar
whichever beat the section began on, because a section boundary does not move
where beat 1 is — but each slot is judged against the repetitions it *could*
have appeared in, so the half-bars at either end are voted on like everything
else. Do not "simplify" this by rounding spans to whole bars: that throws away
evidence *and* leaves those half-bars unconsolidated inside a consolidated
section, which is the exact incoherence Stage 6 exists to remove.

`arrange.py` deliberately **does not generate** a running order. Sequencing is a
musical judgement; what a tool can usefully do is catch what a human misses in
their own list. Don't add an auto-sequencer — add checks.

Each song's `character` block (genre, energy, heaviness, standing, role) is the
band's own read of itself, not anything derived from audio. Energy and heaviness
are separate axes on purpose: Il Phurgone is high energy and not heavy at all.
Never collapse them.

## Testing

`pytest` — `tests/` builds a throwaway project in a tmp dir and exercises
manifests, timeline maths, MIDI round-trips, quantising, build scripts, GX-100
mapping and lyric parsing. All of it runs without ffmpeg, librosa or demucs.
Anything needing those must be skipped, not required.

Run it: `pytest` (or `make test`).

**Build new features test-first.** Write the failing test before the code —
every time, including when the change looks too small to need one. This is not
a style preference, it is what stops the drum pipeline regressing: the
algorithms here are tuned against one song by ear, and a threshold that fixes
Manlio silently breaks Tutti in Fila unless a test pins the behaviour down.
Every tuning decision that was arrived at by measurement gets a test that
would fail if someone "simplified" it back.

Two rules that follow from that:

* **A number that came from a measurement gets a test naming the measurement.**
  The comments in `transcribe.py` are full of hard-won constants (the kick's
  0.18 dominance, the 1.8x look-ahead, `CRASH_SUSTAIN_RATIO`). A constant with
  a story and no test is a constant somebody will "clean up".
* **Verification you did by hand belongs in `tests/`, not in the transcript.**
  If you checked a fix with a one-off script or a hash comparison, that check
  is the test — write it down. An md5 you ran once protects nothing.

The pure-function layering is what makes this cheap: `quantize.py` is pure by
design, and the hit-list passes in `transcribe.py` (`gate_quiet_hits`,
`split_cymbal_runs`, `merge_hat_pairs`, `suppress_crash_bleed`,
`enforce_playability`, `resolve_collisions`) all take `list[Hit]` and return
`list[Hit]` plus a count. None of them touch audio, so all of them are testable
without librosa. Keep new detection logic in that shape for the same reason.

## Git workflow

**Work directly on `main`.** Paolo is the only maintainer, so there is no reason
for feature branches or pull requests here — commit to `main` and push. Don't
create a branch unless explicitly asked to.

History note: the repo was created empty and the first push landed on a
`claude/...` branch, which GitHub then made the default. `main` was created from
that history, so the two are identical up to commit `0041f9a`. The
`claude/ramba-backing-track-tools-wx0lam` branch is dead — if it still exists,
it can be deleted once GitHub's default branch is `main`.

## Don't

* Don't commit audio or video. `.gitignore` covers it; check before `git add -A`.
* Don't invent Reaper `.RPP` internals or drum-VST mappings.
* Don't add a dependency to the core tier without a real need — the point of the
  layering is that a laptop at a venue can run the core commands.
* Don't tell somebody to install ffmpeg without checking `RAMBASS_FFMPEG` first.
  On this machine ffmpeg 9.0 is installed under
  `%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_...\bin` and winget
  linked none of it, so every audio command failed with an install hint for
  something already installed. Point the variable at the folder.
