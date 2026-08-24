# A review & rebuild UI, starting with drums

Scope for a tool, not yet built. It started from one real workflow pain,
described by Paolo: verifying a transcribed/reconstructed drum part means
opening Reaper, soloing the original drum stem for a bar or two, soloing the
rebuilt MIDI, going back and forth by hand — mute, unmute, scrub back to the
section start, repeat — and holding every problem found in his head or in
scattered Reaper notes, because there is nowhere to write "bar 43, missing
crash" down in a way that survives. On top of that, Reaper's default drum
view does not show toms and cymbals legibly, and there is no route from "I
heard a problem" to "put this fill into EZdrummer" other than doing it
entirely by hand.

This is **Stage 10** (drums-rebuild.md) tooled properly, plus the missing link
into Stage 7. It does not replace `drums.additions`/`drums.removals` — it
feeds them.

It grew two more requirements once the shape of it was clear: whatever fixes
a note leads to (a new addition, a moved section) must be re-buildable from
inside the UI, with zero risk of silently discarding a hand edit — see
"Rebuilding without leaving the UI" below — and the mechanism that makes that
possible is not drums-specific, so the scope now also covers turning this
into the shell for every phase of the build, not only the drum rebuild — see
"Scaling this to the whole build". Drums stays the first thing actually
built inside it, because it is the concrete, painful case that exists today.

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
| `review.py` | clip boundaries, the notes ledger, promoting notes to edits, the rebuild selector, the phase-view registry and dashboard rendering | none at import time; splitting goes through `audio.py`'s ffmpeg wrapper, rebuilding shells out to `rambass` itself |

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
rambass review note <song> <bar> "comment" [--kind missing-hit] [--instrument crash]
    # add a note without the UI — keeps the ledger scriptable and testable
rambass review promote <song>
rambass review status [--album ...]
    # open-note counts per song, same shape as `practice status`
rambass review rebuild <song> [--step <name>] [--force <artifact>]
    # runs every stale/missing PIPELINE step for this song, in order, via
    # the exact commands `rambass stale` would print. --force <artifact>
    # is the one-artifact-at-a-time override for `edited`/`unknown`, and
    # writes a `.bak` first. This is what `r` calls in the UI, and it is a
    # first-class command so it works from a terminal too.

rambass review serve                    # the dashboard: every song x STAGES
rambass review serve <song>             # that song's phase view(s)

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
   bar-within-section scrubber for the mouse; `n`/`p` are the keyboard route
   between sections (see the table below).
2. **A/B transport.** This is the actual time saver. Both clips load and loop
   **in sync**, reference and candidate at the same playhead position, so
   switching which one is audible never restarts playback or loses the
   listening position — the specific fix for the described
   solo-track-scrub-back-solo-other-track pain.

**Keyboard shortcuts** (the whole point is not touching the mouse between
"I heard a problem" and "it's logged"). The four asked for, plus the ones
that complete the same loop — logging a note, drilling into a bar, rebuilding
after a fix, and getting back out — proposed rather than settled, since
you're the one who'll be typing them:

| key | action |
|---|---|
| `space` | start/stop the transport |
| `s` | switch which sample is audible — candidate (rendered MIDI) ↔ reference (original drums) — without restarting playback |
| `n` / `p` | next / previous section |
| `,` / `.` | previous / next **bar** within the current section — the mouse-free version of the bar scrubber, for drilling to the exact bar once a section-level A/B says something's off |
| `l` | toggle loop on/off |
| `m` | log a **missing-hit** note at the bar the playhead is on and focus the comment box — the commonest Stage 7 edit (drums-rebuild.md), one key away |
| `x` | log an **extra-hit** note the same way — the other half of that same commonest edit |
| `Enter` | commit the open note and return focus to the transport |
| `Esc` | discard the open note draft and return focus to the transport |
| `r` | **rebuild** everything stale for this song (see below), then refresh the clips and this view |
| `d` | back to the dashboard |
| `?` | show this list on-screen |

`m`/`x` don't replace the `kind` chooser — they just pre-fill the two you'll
reach for most and jump the cursor straight into the comment box, so logging
the routine case is "hear it, press a letter, type why, Enter" with no mouse
at all. Everything else keeps the dropdown.
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
6. **A status strip, always visible.** How many pipeline artifacts for this
   song are stale, missing, or hand-edited — the same three states
   `rambass stale` already reports — and the Rebuild button (`r`). This is
   what closes the loop from "promoted a note into `drums.additions`" back to
   "clips are current again" without a trip to a terminal.

Keyboard-first throughout, because the whole point is not touching the mouse
between "I heard a problem" and "it's logged and I've moved on."

## Rebuilding without leaving the UI

This reuses `provenance.py` exactly as it stands. `stale_report(song)` already
returns, per pipeline artifact, a verdict — `ok` / `missing` / `unknown` /
`stale` / `edited` — and the exact command that produces it
(`Staleness.command`, e.g. `rambass drums clean 09-manlio`). That *is* the
rebuild mechanism; nothing new has to be invented, only wired to a button and
a key.

**The rule that makes it safe is the one `provenance.py` already states, kept
intact rather than worked around:** never touch `edited`. A hand edit is
deliberate, and the risk with it runs the opposite way from staleness —
overwriting it. So:

* `r` (or the status strip's Rebuild button) runs every `stale` and `missing`
  step in `PIPELINE` order, each via the exact command `stale_report` already
  names — a subprocess call to the real `rambass` CLI, not a parallel
  in-process pipeline, so the UI can never produce a result a human typing the
  same command by hand would not also get.
* Afterwards it re-cuts the A/B clips (`review clips`) and refreshes whatever
  view is open. That is the actual answer to "continue editing/testing
  without staleness": promote a note → `drums.additions` changes →
  `drums restore` and everything downstream shows stale → press `r` → the
  clips playing are the current ones, with no terminal and no manual command
  chain.
* `edited` artifacts are **listed, never touched** — the same warning
  `rambass stale` already prints ("changed since it was built — probably
  edited by hand... put the edits in `drums.additions`/`removals` so they
  survive"). Forcing one past that needs a second, explicit confirmation per
  artifact, and the command should write a `.bak` of the file first: a
  terminal workflow already risks this by typing the wrong command, and a
  one-key rebuild button should not make that mistake *easier* to make by
  accident.
* `unknown` (never stamped by this tool — built by hand, or before it
  existed) is reported the same way and is a one-click opt-in, not part of
  the automatic set, for the identical reason as `edited`: nothing on disk
  can say whether it happens to still be current.

Same rule `rambass stale` already lives by — it never rebuilds on its own,
the report decides what's safe — brought into the UI instead of reinvented
for it.

## Scaling this to the whole build

Nothing in the rebuild mechanism above is drums-specific, only its content
is, so the scope should say so rather than build a one-off that gets
rewritten the first time somebody wants the same thing for lyrics. Two
changes turn this into the shell for every stage in `manifest.STAGES`
(`source`, `analyze`, `stems`, `drums_midi`, `quantize`, `kit`, `render`,
`lyrics`, `video`, `gx100`, `rehearsed`):

1. **`provenance.PIPELINE` grows one `Step` per stage that has a producing
   command and does not have one yet** — `analyze`, `kit`/`render` (`click` +
   `reaper build`), `lyrics` (`lyrics check`), `video` (`video ass`/`render`),
   `gx100` (`gx100 midi`). Each addition is metadata only — a name, an
   artifact path, a command string, the inputs/fields/modules that make it
   stale — the exact shape the six drum-chain steps already use. No existing
   command's behaviour changes; `rambass stale` picking up the wider table
   for free is the reason the table lives in one place rather than one per
   command.
2. **A phase-view registry.** `review.py` keeps a small mapping of stage name
   → view: what to render for that stage's detail page, and which `PIPELINE`
   step(s) its corner of the rebuild button covers. The drums A/B stepper this
   document already specifies *is* the `drums_midi`/`quantize`/`kit` view —
   the first, and for now the only, fully-built one. `lyrics` and `gx100`
   start as the thinnest possible views (today's `lyrics check` / `gx100
   sheet` terminal output, rendered as a page instead of dumped to a
   terminal) and grow real UI later without moving anything, because the
   registry is the extension point rather than a rewrite target.

**The dashboard** is `rambass review serve` with no song argument: the same
song-by-`STAGES` grid `status.board()` already computes, as clickable HTML
instead of terminal text, with each cell's mark sourced from `stale_report`
rather than only `song.status` — so a cell can read "done, but now stale"
instead of just "done" — and clicking it opens that song's phase view for
that stage. `d` from any phase view returns here; `rambass review serve
<song>` jumps straight to that song's most relevant open phase.

`qa/review.yaml` picks up one more field, `phase: drums | lyrics | video |
gx100 | ...`, so the same ledger and the same `promote` mechanism eventually
serve every phase — a lyrics-timing complaint and a missing crash are both
"a note against a bar, waiting to become a real edit," and there is no
reason to give them two ledgers.

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
* **the rebuild selector never includes `edited` or `unknown`** in its
  automatic set — given a fixture `stale_report` result with one of each of
  the five states, the function that picks what `r` runs returns exactly
  the `stale` and `missing` ones. This is the test that would fail if
  someone "simplified" the safety rule away.
* the extended `provenance.PIPELINE` entries (once added) are tested the same
  way the existing six already are — `Step.command` formats with the song's
  slug, and staleness flips when the fields/inputs they declare change

The `ezrender.py` spike needs the actual plugin to test at all, so it stays
outside the ordinary suite the way `stems.py`'s demucs call already does.

## Plan

| phase | what | blocked by |
|---|---|---|
| **0** | the generic shell: extend `provenance.PIPELINE` to every stage, the phase-view registry, the dashboard route, the rebuild selector + `--force` path | — |
| **1** | `review.py`'s drums-specific pieces: clip-boundary functions, `qa/review.yaml` model, CLI (`clips`, `note`, `promote`, `status`) | Phase 0 |
| **2** | the drums phase view: transport, instrument grid, notes panel, against a manually-bounced candidate wav | Phase 1 |
| **3** | "send to EZdrummer" file export | Phase 2 |
| **4** (spike, parallel, not committed) | `ezrender.py`: can `pedalboard` host EZD3's VST3 headlessly at all, on Paolo's machine | — |
| **5** | thin phase views for `lyrics` and `gx100` on top of Phase 0's registry, reusing their existing CLI output | Phase 0 |

Phase 0 first, deliberately: it is the part that stops the drums view from
being a one-off that has to be unpicked later, and it's also the part that
directly answers "make sure I can keep testing without staleness" — that
capability should exist before there's a second phase view competing for the
same rebuild button.

**Gate R0** — the dashboard renders every song × `STAGES` from real data,
one stage on one song is deliberately made stale (edit `song.yaml` by hand),
`r` rebuilds it via the exact command `rambass stale` would print, and a
second artifact deliberately hand-edited is correctly left alone and only
touched via `--force`.

**Gate R1** — one Tutti in Fila section reviewed end to end: clips cut, A/B
mute-swap works without a restart, a note logged and promoted into
`drums.additions`, and pressing `r` (not re-running commands by hand) is what
gets `drums restore` re-applied and the clips current again.

**Gate R2** — the spike has a yes/no answer. Yes: `review render` replaces the
manual bounce. No: Phases 0-3 already stand, and the manual bounce stays
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
* **Rebuild is only as safe as the `Step` table is complete.** If a phase's
  producing command writes a file `PIPELINE` doesn't know about, `r` will not
  touch it and will report it "ok" by omission rather than by verdict — the
  same blind spot `rambass stale` already has today, not a new one this UI
  introduces. Extending Phase 0's table honestly (real inputs, real fields)
  matters more than adding phases quickly.
* **One song at a time, still.** The dashboard lists everything, but the
  rebuild button acts on the song whose phase view is open — running it
  across the whole set is a `--all`-style batch command for later, not
  something this scope commits to now.
