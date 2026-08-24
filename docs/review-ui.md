# A stepper UI for QA'ing a drum part

Scope for a tool, not yet built. It answers a real workflow pain, described by
Paolo: today, verifying a transcribed/reconstructed drum part means opening
Reaper, soloing the original drum stem for a bar or two, soloing the rebuilt
MIDI, going back and forth by hand — mute, unmute, scrub back to the section
start, repeat — and holding every problem found in his head or in scattered
Reaper notes, because there is nowhere to write "bar 43, missing crash" down in
a way that survives. On top of that, Reaper's default drum view does not show
toms and cymbals legibly, and there is no route from "I heard a problem" to
"put this fill into EZdrummer" other than doing it entirely by hand.

This is **Stage 10** (drums-rebuild.md) tooled properly, plus the missing link
into Stage 7. It does not replace `drums.additions`/`drums.removals` — it feeds
them.

## What already exists, and what this adds

`stems/no_drums.wav` (the band minus the album's drums) and, once built,
`practice/no_drums-aligned.wav` (the same, time-warped onto the fixed grid) are
already the right reference material — drums-rebuild.md Stage 10 says so. What
does not exist is a fast way to **step through** a song against that reference
and **write down** what is wrong, bar by bar, without touching Reaper at all.

Three things this adds:

1. **Pre-cut A/B clips**, one pair per section (and drillable to one pair per
   bar), so comparing is "press a button" instead of "select a range, solo a
   track, press play, select the other range, solo the other track, press
   play".
2. **A visible grid keyed by instrument name** — kick / snare / hats / every
   tom / every cymbal as its own row, using the same canonical names
   `drummap.py` already carries — instead of Reaper's default drum-map view,
   which is what is actually behind "it doesn't show toms and everything".
   `mido` is already a dependency; drawing note-on events on a grid is a
   rendering problem, not a new capability.
3. **A bar-anchored notes ledger** that a comment gets typed into once, and a
   **promote** action that turns a confirmed note into a real
   `drums.additions` / `drums.removals` entry — the same "machine proposes,
   `git diff` reviews, `drums restore` applies" split `restore.py` already
   uses for crash candidates.

## The two forks this scope resolved

**EZdrummer 3 render:** both, in this order. Phase 1 takes a **manually
bounced** wav as the candidate — the same render you already do for Stage 9,
just done once per review pass instead of once for the final base — so the
whole review tool is usable this week and never blocks on an unproven
capability. A **spike**, in parallel and explicitly not committed scope,
tries headless rendering straight from `drums-quantized.mid` through EZD3's
VST3 with `pedalboard` (which added instrument-plugin hosting — feed it MIDI
events and a duration, get audio back). The real unknown is whether EZD3's
license/activation path blocks a headless instantiation at all, and that can
only be answered on your machine, with the real plugin installed — not in
this sandbox. If it works, `rambass review render` replaces the manual bounce
with one command; if it doesn't, Phase 1 already stands on its own and nothing
is lost.

**Step granularity:** section is the default navigation grain — next/prev
moves between named sections from `song.yaml`, matching how you already think
about the song — with every section scrubbable bar-by-bar underneath.

## Architecture

New module, cheap tier:

| module | job | deps |
|---|---|---|
| `review.py` | clip boundaries, the notes ledger, promoting notes to edits | none at import time; splitting goes through `audio.py`'s ffmpeg wrapper |

`review.py` follows the same discipline as `restore.py` and `quantize.py`:
pure functions over bars and `Hit`s wherever possible, no disk access, no
audio decode. The one place seconds appear is where a clip boundary is handed
to `audio.py` to cut — and that boundary always comes from
`Timeline.bar_beat_to_seconds` / `Timeline.audio_time`, never invented from a
clip's own duration. Getting the count-in wrong here is exactly the "two
clocks" mistake CLAUDE.md warns about, and it would show up as reference and
candidate clips drifting apart by the count-in length — the one bug this tool
exists to catch, self-inflicted.

The headless-render spike is a separate, explicitly optional path:

| module | job | deps |
|---|---|---|
| `ezrender.py` | host a VST3 instrument plugin, feed it MIDI, capture audio | `pedalboard` (new optional extra) — imported only inside this module, via `audio.require_module`, same pattern as `analyze.py`/`transcribe.py`/`stems.py` |

If the spike fails, this module simply never ships and nothing else depends
on it.

### Data model — a sidecar, not a `song.yaml` field

Review notes are **not** `drums.additions`/`drums.removals`. Those are literal
MIDI edits, already vetted, ready for `drums restore` to reapply. A review note
is a raw, unfiltered "something might be wrong here" — most of them will turn
out to be nothing, some will become an addition, a removal, or a rebalanced
`backbeat_velocity`, and a few will just get typed into `notes:` for later.
Mixing that into `song.yaml`'s musical truth would put unreviewed chatter next
to decisions `drums restore` trusts blindly.

So: `qa/review.yaml`, git-tracked, bar-anchored (musical bars, never Reaper's —
same reason `drums.additions` uses musical bars: a stored ruler number slides
the day `count_in.bars` changes).

```yaml
# qa/review.yaml
version: drums-quantized.mid        # which candidate this pass reviewed
notes:
- bar: 43
  beat: 1.0
  section: verse-2
  kind: missing-hit                 # missing-hit | extra-hit | wrong-instrument
                                     # | timing | velocity | other
  instrument: crash
  comment: "no crash going into the lift, original has one"
  status: open                      # open | promoted | dismissed
  created: 2026-08-24
```

`qa/review.md` is generated from it for reading next to the ruler — same
pairing as `restore.checklist`'s `qa/missing-hits.md`, Reaper bar numbers in
the rendering, musical bars in the source of truth.

### Promoting a note

```
rambass review promote <song>
```

Walks `qa/review.yaml` for open notes with `kind: missing-hit` or
`extra-hit` and a velocity/instrument confident enough to propose — same
shape as `restore.propose_additions` — and writes them into
`song.drum_additions` / `song.drum_removals`, marking the source note
`promoted`. Anything else (`timing`, `wrong-instrument`, `other`, or a
`missing-hit` with no velocity guess) is left for a human to turn into an edit
by hand, exactly as `restore.py` already leaves fills for a human. `git diff`
is still the review step before `drums restore` applies anything.

### CLI surface

```
rambass review clips <song> [--version drums-quantized|drums-final] [--candidate <wav>]
    # cuts reference (stems/drums.wav, or no_drums-aligned bed) and candidate
    # into one clip pair per section (and, lazily, per bar) under a scratch
    # cache dir — gitignored, same as every other rendered audio file
rambass review serve <song>
    # starts the stepper UI (see below) against those clips
rambass review note <song> <bar> "comment" [--kind missing-hit] [--instrument crash]
    # add a note without the UI — keeps the ledger scriptable and testable
rambass review promote <song>
rambass review status [--album ...]
    # open-note counts per song, same shape as `practice status`

rambass review render <song> --ezd3     # THE SPIKE. Optional extra required.
    # renders drums-quantized.mid through a VST3 instrument, headless.
    # Explicitly not required for anything else above to work.
```

Not added to `manifest.STAGES` and writes nothing to `render/` or to any
musical field in `song.yaml` except through the explicit `promote` action —
same protective rule `practice.py` follows, for the same reason: this is a QA
instrument, not a gig deliverable, and it must never make the status board
look less finished than the gig actually is.

## The UI itself

A local web page (`rambass review serve` starts a small stdlib
`http.server` — no new UI framework dependency, since the page is a stepper
with buttons and a grid, not an app). Runs on Paolo's own machine, one song at
a time.

**Layout, top to bottom:**

1. **Position header** — section name, bar range, a section dropdown and
   bar-within-section scrubber. `←`/`→` (or `[`/`]`) move by the current unit
   (section by default, bar inside a section); `↑`/`↓` switch units.
2. **A/B transport.** This is the actual time saver. Both clips load and loop
   **in sync**, reference and candidate at the same playhead position; a
   single key (`1` reference, `2` candidate, or hold `space` to hear the other
   one) **mutes/unmutes rather than restarting playback**. That is the
   specific fix for the described pain — no more solo-track-scrub-back-solo-
   other-track. Loop is on by default; `l` toggles it.
3. **Instrument grid.** One row per canonical instrument name that has a hit
   anywhere in the visible bars — kick, snare, sidestick, every tom, hi-hat
   closed/open, ride, ride bell, every cymbal — rendered from the candidate
   MIDI directly (`midiio.read_drum_midi` + `drummap.CANONICAL`), ticks at
   the song's subdivision. This is the toms-and-cymbals visibility Reaper's
   default view does not give.
4. **Notes panel.** A text box plus a `kind` chooser (missing-hit / extra-hit
   / wrong-instrument / timing / velocity / other), pinned to the bar the
   playhead is on; `Enter` commits a note to `qa/review.yaml` and returns
   focus to the transport, so logging a comment never breaks the listening
   flow. Existing notes for the visible range show inline on the grid at
   their bar, click to edit, checkbox to dismiss.
5. **"Send to EZdrummer" button**, per section. Not real drag-and-drop out of
   a browser — that is not a mechanism worth building when there is a
   trivial, robust alternative: it writes that section's slice of
   `drums-quantized.mid` to `midi/sections/<name>.mid` and opens the
   containing folder. EZdrummer 3 reads plain `.mid` files from a linked
   folder into its browser (drums-rebuild.md's "faster alternative" section
   already describes this), so this is one file write plus one `explorer`/
   `open` call, and the actual drag happens in the OS, which is what already
   works reliably.

Keyboard-first throughout, because the whole point is not touching the mouse
between "I heard a problem" and "it's logged and I've moved on."

## Testing

Everything in `review.py` is arithmetic and MIDI reading, so it is tested like
`restore.py` — no ffmpeg, no browser:

* clip boundaries for a section that does not start on a bar line come out at
  the right second, cross-checked against `Timeline.bar_beat_to_seconds`
  directly (the count-in regression: assert the boundary moves when
  `count_in.bars` changes and a stored ruler number would not have)
* the notes ledger round-trips through YAML with musical bars preserved
* `promote` only touches notes confident enough (has a velocity, a canonical
  instrument, `kind` in the promotable set) and marks the rest untouched —
  same test shape as `restore.propose_additions`
* the instrument-grid rendering is a pure function from a `DrumPerformance` to
  rows of ticks; no browser needed to test it, only to look at it

The `ezrender.py` spike needs the actual plugin to test at all, so it stays
outside the ordinary suite the way `stems.py`'s demucs call already does.

## Plan

| phase | what | blocked by |
|---|---|---|
| **1** | `review.py`: clip-boundary functions, `qa/review.yaml` model, CLI (`clips`, `note`, `promote`, `status`) | — |
| **2** | the stepper UI: transport, instrument grid, notes panel, against a manually-bounced candidate wav | Phase 1 |
| **3** | "send to EZdrummer" file export | Phase 2 |
| **4** (spike, parallel, not committed) | `ezrender.py`: can `pedalboard` host EZD3's VST3 headlessly at all, on Paolo's machine | — |

**Gate R1** — one Tutti in Fila section reviewed end to end: clips cut, A/B
mute-swap works without a restart, a note logged and promoted into
`drums.additions`, `drums restore` picks it up.

**Gate R2** — the spike has a yes/no answer. Yes: `review render` replaces the
manual bounce. No: Phase 1-3 already stand, and the manual bounce stays
however long the tool is useful.

## Honest limits

* **This does not replace listening to the part in context.** Practice-tracks
  deliverable 1 — the album mix with the new drums swapped in — finds problems
  a soloed A/B never will, because the rest of the band is what a wrong fill
  actually clashes with. This tool is for the fast, mechanical first pass;
  do the in-context listen before Gate A regardless.
* **The instrument grid is only as good as the MIDI it reads.** A hit
  `drums clean` mis-typed (kick called snare) shows up mis-typed here too —
  this surfaces problems, it does not independently verify instrument
  identity.
* **The EZdrummer send-to-folder step is one-way.** Whatever comes back out of
  EZdrummer's Grid Editor or Song Track has to be dragged back into Reaper and
  re-imported (`rambass drums import`) by hand, same as the existing
  Stage 4/7 workflow in drums-rebuild.md. This tool does not close that loop
  automatically, and doing so is not worth the risk of silently overwriting a
  hand-edited MIDI item.
