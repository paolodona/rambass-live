# The project console

**Where the repo is, as of 2026-08-24: Phases 0–2 are built.** `rambass
console` starts the console (`console.py` over `review.py`, tested in
`tests/test_console.py` / `tests/test_review.py`); `review rebuild / clips /
note / promote / status` are commands. Still open: the EZD3 headless-render
spike (Gate R2 — needs Paolo's machine and the real plugin), the Phase 5
thin lyrics/gx100 views, and a set-wide batch rebuild. The rest of this
document is the scope as designed; where it says "would", it now mostly
"does".

Scope for a tool, now largely built. Started from one workflow pain — verifying a
transcribed drum part means opening Reaper, soloing the original, soloing the
rebuilt MIDI, going back and forth by hand — and grew, deliberately, into
something bigger: **a console for the whole build**, not a drums tool with a
dashboard bolted on.

The entry point is the dashboard: the setlist, one row per song, one column
per build stage, a traffic light in every cell. Click a cell and it opens
that song's **stage screen** — a top-to-bottom checklist of what to do next
for that stage, with a button to run the command or a link into a dedicated
tool where a command and a report aren't enough. The drums transcription
review — the A/B stepper, the instrument grid, the notes ledger — is **one
stage screen**, reached by clicking through from the dashboard. It's the
first one fully specified, because it's the pipeline's most painful stage
today, not because the console is a drums tool at heart.

Read this document top to bottom: dashboard → stage screens in general → the
drums cluster's screen in particular → the rebuild mechanism every level of
this shares.

**Visual mockup of the click-through** (dashboard → stage screen → drum
review tool, static, dark-mode): <https://claude.ai/code/artifact/8a50b2ab-1dfc-4e30-bb98-0bc281e29756>.
The dashboard mockup is every song in `songs/*/*/song.yaml` across both
albums (23, once the 3 excluded ones are set aside) with its real, current
`status:` block — not a representative sample. The review-tool mockup shows
the two A/B waveforms **stacked**, not toggled, so a level difference at the
same bar is a visible fact rather than something you have to remember across
a switch — the mocked example is a snare that reads much quieter in the
candidate than the reference at bar 23 — and a shared bar.beat ruler runs
underneath both, the same addressing `rambass drums missing` already prints
(e.g. `23.2.1`), so a problem can be named precisely without opening Reaper.

Grounding the work in real data surfaced a doc inconsistency, and the first
attempt at fixing it picked the wrong side — worth recording because the
lesson generalises. `grep` for "verse-2 runs" turned up four copies of where
Manlio's verse-2 ends: 32.3 in CLAUDE.md and a `test_consolidate_spans.py`
docstring, 28.3 in drums-rebuild.md and a `test_voicing.py` docstring.
Arbitrating between *documents* favoured 32.3. But the **manifest is the
ground truth** — `songs/tutti-in-fila/09-manlio/song.yaml` has `verse-2` at
20.3 and `verse-2-lift` at 28.3 — so verse-2 proper ends at 28.3, and 32.3 is
verse-2 *plus its lift*. All four places now say so precisely, and CLAUDE.md
notes that the manifest wins when a doc disagrees. (The
`test_consolidate_spans.py` fixture spanning 20–32 is unaffected: it is a
synthetic shape, and its docstring now says "verse-2 plus its lift".)

## The dashboard

Rows are the setlist's running order, not an alphabetical list — the same
source `rambass reaper setlist` and `rambass setlist show` already resolve
(`Setlist.load(...).resolve(project)`), with a switcher for which setlist
(the gig, a rehearsal order, "every song, by album and track" when none is
given). A song not in the current setlist still needs a home, so the
fallback is the same album/track order `rambass status` already uses.

Columns are `manifest.STAGES` — `source`, `analyze`, `stems`, `drums_midi`,
`quantize`, `kit`, `render`, `lyrics`, `video`, `gx100`, `rehearsed` — the
exact set `rambass status` already prints as a text grid. This dashboard is
that grid, clickable, in a browser instead of a terminal.

**Each cell is a traffic light**, not a bare tick or cross, because a cell
carries two questions and they need to stay visually distinct:

* **is this stage considered finished** — `song.status[stage]`, the same
  `todo`/`wip`/`done`/`n/a` `rambass mark` already sets: red / amber / green
  / grey. `n/a` is grey and never reads as incomplete — a cappella songs'
  backing-track columns, Diversamente Giovani's `n/a` drum stages, are
  supposed to look finished, and CLAUDE.md is explicit that they count as
  finished.
* **is what's on disk still what today's code and `song.yaml` would
  produce** — `provenance.stale_report`. A green cell whose artifact is
  `stale` gets a small ring around it rather than turning amber outright:
  "marked done" and "provenably out of date" are different facts, and
  collapsing them into one colour would make the dashboard lie in one
  direction or the other. Clicking the ring offers the same rebuild this
  document specifies below.

If lyrics are missing for a song, that's a plain red cell in the `lyrics`
column, in that song's row, exactly where you'd look for it — no separate
missing-work report to remember to run.

**Every cell is a link.** Clicking it opens that song's stage screen,
scrolled to that stage. Clicking the song's title opens the stage screen at
its first unfinished stage — the same "what's next" logic `rambass status`'s
`next_actions` already computes, just one click instead of a read.

A header strip carries the same summary line `rambass status` prints today
(`N songs in the set · X/Y stages complete`), and the setlist switcher. Cut
songs (`excluded: true`) are listed collapsed below the grid, same as
`status.board()`'s "cut from the set" section — not a row of red, because
they are not work.

## Stage screens

A stage screen is a top-to-bottom checklist for one song's one stage (or a
tightly-coupled cluster of stages — see below): a description of what to do,
a status per step, and either a **Run** button or an **Open** link.

* **Run** is for a step that's one deterministic command with nothing to look
  at afterward but its report — `rambass analyze`, `rambass click`,
  `rambass gx100 midi`. Pressing it is exactly the rebuild mechanism this
  document specifies once, below, used here instead of on a whole song: run
  the command, show its output, mark the step's status from the result.
* **Open** is for a step that needs a screen of its own because a report
  isn't the point — you have to *listen*, *look*, or *decide*, not just read
  a number. The drums review tool below is the worked example.

**The step list is not new content.** It is `docs/workflow.md`'s existing
per-song sequence (and, for the drums cluster specifically,
`docs/drums-rebuild.md`'s stage-by-stage breakdown), held as a small data
table instead of static prose — the same relationship `provenance.PIPELINE`
already has to `rambass stale`'s explanations, one level up. A maintainer
edits the table when the workflow changes; the table is what the screen
reads, so the two cannot drift the way a hand-written checklist and the docs
it was copied from eventually do.

**The two albums need different steps, and the table says so rather than two
screens knowing so.** workflow.md's own table has Diversamente Giovani
collapsing to "import the Reaper project, fix the count-in, do the lyrics"
while Tutti in Fila runs the full sequence — so the step table is resolved
per song from `drums.origin`/album, the same way `provenance.PIPELINE`'s
`skip_origins` already prunes steps that don't apply to a song. One data
table, not a fork in the UI.

Each step's status is the same two axes as the dashboard, at finer grain:
not-yet-done, done-and-fresh, or done-but-stale (with the rebuild offered
right there, inline, rather than sending you back to the dashboard).

### The drums cluster's screen

`drums_midi` + `quantize` + `kit` collapse into **one** stage screen on
purpose. On Tutti in Fila these are not three separate sit-down sessions;
they're one continuous pass that loops back on itself — clean, listen,
consolidate, listen, restore, listen again — and three separate screens would
just mean clicking back to the dashboard between steps that belong together.

Its step list is drums-rebuild.md's own stages, each a **Run** except where
noted:

1. **Separate** (`rambass stems`) — Run.
2. **Transcribe** (`rambass drums transcribe`, or `drums import` for a
   recorded take) — Run.
3. **Clean** (`rambass drums clean`) — Run.
4. **Sections & consolidate** (`rambass sections`, `rambass drums
   consolidate`) — Run, plus the section list itself is edited here (bar,
   name, declared `backbeat`).
5. **Missing hits & restore** (`rambass drums missing`, `rambass drums
   restore`) — this is the step that gets an **Open**, not a Run, because
   deciding what's actually missing needs ears, not a report. It opens:

#### The drums review tool

The A/B stepper this scope started from, unchanged in substance, now reached
by clicking through dashboard → drums cluster screen → this step, instead of
being the whole application.

**The two forks already resolved, unchanged:**

* **EZdrummer 3 render:** both, in order. The candidate wav is a **manually
  bounced** render at first — the same render already done for Stage 9, once
  per review pass instead of once for the final base — so this tool works
  without depending on anything unproven. A parallel, explicitly
  non-committed **spike** tries headless rendering straight from
  `drums-quantized.mid` through EZD3's VST3 with `pedalboard`. The real
  unknown — whether EZD3's license/activation path blocks a headless
  instantiation — is answerable only on your machine with the real plugin,
  not in a sandbox. If it works, `rambass review render` replaces the manual
  bounce; if not, everything else here already stands on its own.
* **Step granularity:** section is the default grain — `n`/`p` move between
  named sections from `song.yaml` — with every section scrubbable bar by bar
  underneath.

**Layout, top to bottom:**

1. **Position header** — section name, bar range, a dropdown and a
   bar-within-section scrubber for the mouse.
2. **A/B transport.** The actual time saver: both clips load and loop **in
   sync**, at the same playhead position, so switching which one is audible
   never restarts playback — the fix for the solo-track-scrub-back-solo-
   other-track cycle this whole thing started from.
3. **Instrument grid.** One row per canonical instrument name with a hit in
   the visible bars — kick, snare, sidestick, every tom, hi-hat
   closed/open, ride, ride bell, every cymbal — rendered straight from the
   MIDI (`midiio.read_drum_midi` + `drummap.CANONICAL`). This is the
   toms-and-cymbals visibility Reaper's default drum view does not give.
4. **Notes panel.** A text box plus a `kind` chooser (missing-hit /
   extra-hit / wrong-instrument / timing / velocity / other), pinned to the
   bar the playhead is on. Existing notes for the visible range show inline
   on the grid at their bar; click to edit, checkbox to dismiss.
5. **"Send to EZdrummer" button**, per section. Not real drag-and-drop out
   of a browser — not a mechanism worth building when there's a trivial,
   robust alternative: it writes that section's slice of the MIDI to
   `midi/sections/<name>.mid` and opens the containing folder. EZdrummer 3
   already reads plain `.mid` files from a linked folder into its browser
   (drums-rebuild.md's "faster alternative" section), so this is one file
   write and one `explorer`/`open` call, and the drag happens in the OS,
   which already works reliably.
6. **A status strip** — how many pipeline artifacts for this song are stale,
   missing, or hand-edited, and the Rebuild control. Closes the loop from
   "promoted a note into `drums.additions`" back to "clips are current
   again" without leaving this screen.

**Keyboard shortcuts** (the whole point is not touching the mouse between
"I heard a problem" and "it's logged"):

| key | action |
|---|---|
| `space` | start/stop the transport |
| `s` | switch which sample is audible — candidate ↔ reference — without restarting playback |
| `n` / `p` | next / previous section |
| `,` / `.` | previous / next **bar** within the current section |
| `l` | toggle loop on/off |
| `m` | log a **missing-hit** note at the playhead's bar and focus the comment box — the commonest Stage 7 edit, one key away |
| `x` | log an **extra-hit** note the same way — the other half of that same edit |
| `Enter` | commit the open note, return focus to the transport |
| `Esc` | discard the open note draft, return focus to the transport |
| `r` | rebuild everything stale for this song, then refresh the clips and this view |
| `d` | back to the dashboard |
| `?` | show this list on-screen |

`m`/`x` don't replace the `kind` chooser — they pre-fill the two you'll reach
for most and jump the cursor into the comment box, so the routine case is
"hear it, press a letter, type why, `Enter`", no mouse at all.

**Data model — a sidecar, not a `song.yaml` field.** A review note is a raw,
unfiltered "something might be wrong here", not a vetted edit — most turn out
to be nothing, some become an addition, a removal, or a rebalanced
`backbeat_velocity`. Mixing that into `song.yaml`'s musical truth would put
unreviewed chatter next to decisions `drums restore` trusts blindly. So:
`qa/review.yaml`, git-tracked, bar-anchored in **musical** bars (same reason
`drums.additions` is — a stored ruler number slides the day `count_in.bars`
changes), one file per song, one ledger shared across every phase via a
`phase:` field:

```yaml
# qa/review.yaml
version: drums-quantized.mid        # which candidate this pass reviewed
notes:
- bar: 43
  beat: 1.0
  phase: drums
  section: verse-2
  kind: missing-hit                 # missing-hit | extra-hit | wrong-instrument
                                     # | timing | velocity | other
  instrument: crash
  comment: "no crash going into the lift, original has one"
  status: open                      # open | promoted | dismissed
  created: 2026-08-24
```

`qa/review.md` is generated from it for reading next to the ruler — same
pairing as `restore.checklist`'s `qa/missing-hits.md`.

**Promoting a note** (`rambass review promote <song>`) walks open notes with
`kind: missing-hit`/`extra-hit` and a confident velocity/instrument — same
shape as `restore.propose_additions` — into `song.drum_additions`/
`drum_removals`, marking the source note `promoted`. Anything less confident
is left for a human to turn into an edit by hand, exactly as `restore.py`
already leaves fills for a human. `git diff` is still the review step before
`drums restore` applies anything.

## Rebuilding without leaving the console

One primitive, used from three places: a dashboard cell's stale ring, a
stage-screen step's Run button, and the drums screen's `r` key.

It reuses `provenance.py` exactly as it stands. `stale_report(song)` already
returns, per pipeline artifact, a verdict — `ok` / `missing` / `unknown` /
`stale` / `edited` — and the exact command that produces it
(`Staleness.command`, e.g. `rambass drums clean 09-manlio`). That *is* the
mechanism; nothing new needs inventing, only wiring to a button.

**The rule that makes it safe is the one `provenance.py` already states, kept
intact rather than worked around:** never touch `edited`. A hand edit is
deliberate, and the risk with it runs the opposite way from staleness —
overwriting it. So:

* Rebuilding a song, a stage, or one step runs every `stale`/`missing` step
  in `PIPELINE` order that falls within that scope, each via the exact
  command `stale_report` already names — a subprocess call to the real
  `rambass` CLI, not a parallel in-process pipeline, so the console can never
  produce a result a human typing the same command wouldn't also get.
* Afterwards it re-cuts any open A/B clips and refreshes whatever screen is
  open. That's the actual answer to "keep testing without staleness":
  promote a note → `drums.additions` changes → everything downstream shows
  stale → press `r` (or click the ring, or press Run) → current again, no
  terminal, no manual command chain.
* `edited` artifacts are **listed, never touched** — the same warning
  `rambass stale` already prints. Forcing one past that needs a second,
  explicit confirmation per artifact, and the command should write a `.bak`
  of the file first: a terminal workflow already risks this by typing the
  wrong command, and a one-click rebuild should not make that mistake
  *easier* to make by accident.
* `unknown` (never stamped by this tool) is reported the same way and is a
  one-click opt-in, not part of the automatic set, for the identical reason
  as `edited`.

## Architecture

New module, cheap tier:

| module | job | deps |
|---|---|---|
| `review.py` | dashboard rendering, the per-stage step tables (keyed by album/origin), the rebuild selector, the drums cluster's clip boundaries and notes ledger, promoting notes to edits | none at import time; splitting goes through `audio.py`'s ffmpeg wrapper, rebuilding shells out to `rambass` itself |

Pure functions over bars and `Hit`s wherever possible, same discipline as
`restore.py`/`quantize.py`: no disk access, no audio decode, except where a
clip boundary is handed to `audio.py` to cut. That boundary always comes
from `Timeline.bar_beat_to_seconds`/`Timeline.audio_time`, never invented
from a clip's own duration — getting the count-in wrong here is exactly the
"two clocks" mistake CLAUDE.md warns about, and it would show up as
reference and candidate clips drifting apart by the count-in length.

The headless-render spike is separate and explicitly optional:

| module | job | deps |
|---|---|---|
| `ezrender.py` | host a VST3 instrument plugin, feed it MIDI, capture audio | `pedalboard` (new optional extra), imported only inside this module via `audio.require_module`, same pattern as `analyze.py`/`transcribe.py`/`stems.py` |

If the spike fails, this module simply never ships and nothing else depends
on it.

`provenance.PIPELINE` grows one `Step` per stage that has a producing
command and doesn't have one yet — `analyze`, `kit`/`render` (`click` +
`reaper build`), `lyrics` (`lyrics check`), `video` (`video ass`/`render`),
`gx100` (`gx100 midi`). Each addition is metadata only, the same shape the
six drum-chain steps already use; no existing command's behaviour changes,
and `rambass stale` picking up the wider table for free is the reason the
table lives in one place.

### CLI surface

```
rambass console                         # the dashboard: every song x STAGES
rambass-console                         # ...and the same thing as its own
                                        #    executable, for the shell
rambass review serve                    # ...and where it lives in the tree
rambass review serve <song>              # that song's stage screen(s)
rambass review serve <song> --stage drums_midi   # land on one stage directly

rambass review clips <song> [--version drums-quantized|drums-final] [--candidate <wav>]
    # cuts reference and candidate into one clip pair per section (and,
    # lazily, per bar) under a scratch cache dir — gitignored
rambass review note <song> <bar> "comment" [--kind missing-hit] [--instrument crash]
    # add a note without the UI — keeps the ledger scriptable and testable
rambass review promote <song>
rambass review status [--album ...]
    # open-note counts per song, same shape as `practice status`
rambass review rebuild <song> [--stage <name>] [--step <name>] [--force <artifact>]
    # runs every stale/missing PIPELINE step in scope, in order, via the
    # exact commands `rambass stale` would print. --force <artifact> is the
    # one-artifact-at-a-time override for edited/unknown, and writes a
    # `.bak` first. This is what a rebuild control calls, and it's a
    # first-class command so it works from a terminal too.

rambass review render <song> --ezd3     # THE SPIKE. Optional extra required.
    # renders drums-quantized.mid through a VST3 instrument, headless.
    # Explicitly not required for anything else above to work.
```

Not added to `manifest.STAGES` and writes nothing to `render/` or to any
musical field in `song.yaml` except through the explicit `promote` action —
same protective rule `practice.py` follows: this is a QA and build console,
not a gig deliverable, and it must never make the status board look less
finished than the gig actually is.

## Testing

Everything worth testing is arithmetic, MIDI reading, or data-table
resolution — no browser, no ffmpeg needed for most of it:

* **the step table resolves the right steps per album/origin** — the
  DG-vs-TIF collapse from workflow.md is exactly the kind of thing CLAUDE.md
  asks a test to pin down: given `drums.origin: backing-track`, the drums
  cluster's step list comes back empty/`n/a`, not the ten-step sequence.
* clip boundaries for a section that doesn't start on a bar line come out at
  the right second, cross-checked against `Timeline.bar_beat_to_seconds`
  directly (the count-in regression: the boundary moves when
  `count_in.bars` changes, which a stored ruler number would not have).
* the notes ledger round-trips through YAML with musical bars preserved.
* `promote` only touches notes confident enough (velocity, canonical
  instrument, `kind` in the promotable set) and leaves the rest untouched —
  same test shape as `restore.propose_additions`.
* the instrument-grid rendering is a pure function from a `DrumPerformance`
  to rows of ticks; no browser needed to test it, only to look at it.
* **the rebuild selector never includes `edited` or `unknown`** in its
  automatic set — given a fixture `stale_report` with one of each of the
  five states, the function that decides what to run returns exactly the
  `stale` and `missing` ones. The test that would fail if someone
  "simplified" the safety rule away.
* the extended `provenance.PIPELINE` entries are tested the same way the
  existing six already are — `Step.command` formats with the song's slug,
  and staleness flips when the fields/inputs they declare change.

The `ezrender.py` spike needs the actual plugin to test at all, so it stays
outside the ordinary suite the way `stems.py`'s demucs call already does.

## Plan

| phase | what | blocked by |
|---|---|---|
| **0** | the console shell: the dashboard, the generic stage-screen renderer, the step-table data for every `STAGES` entry (content-authoring from workflow.md/drums-rebuild.md, not new design), extend `provenance.PIPELINE`, the rebuild selector + `--force` path | — |
| **1** | the drums cluster's data: clip-boundary functions, `qa/review.yaml` model, CLI (`clips`, `note`, `promote`, `status`) | Phase 0 |
| **2** | the drums review tool: transport, instrument grid, notes panel, against a manually-bounced candidate wav | Phase 1 |
| **3** | "send to EZdrummer" file export | Phase 2 |
| **4** (spike, parallel, not committed) | `ezrender.py`: can `pedalboard` host EZD3's VST3 headlessly at all, on Paolo's machine | — |
| **5** (opportunistic) | bespoke screens for other stages beyond "Run + show report" — a lyrics timeline scrubber is the likely first candidate | Phase 0 |

Phase 0 first, deliberately: it's what stops the drums screen from being a
one-off that has to be unpicked later, and it's the part that makes "click
through from the dashboard" and "keep testing without staleness" true for
the whole project on day one, not just for drums once Phase 2 lands.

**Gate R0** — the dashboard renders every song across both albums against a
real setlist, in the setlist's order; a handful of cells are spot-checked
against `rambass status`/`rambass stale` run by hand and agree; clicking a
red `lyrics` cell on a real song with no lyrics lands on that stage's screen
with a not-yet-done first step; clicking through a Diversamente Giovani song
into the drums cluster shows the collapsed three-step sequence, not Tutti in
Fila's ten.

**Gate R1** — one Tutti in Fila section reviewed end to end from the
dashboard: click through to the drums cluster's screen, open the review
tool, clips cut, A/B mute-swap works without a restart, a note logged and
promoted into `drums.additions`, and pressing `r` — not re-running commands
by hand — is what gets `drums restore` re-applied and the clips current
again.

**Gate R2** — the spike has a yes/no answer. Yes: `review render` replaces
the manual bounce. No: Phases 0-3 already stand, and the manual bounce stays
however long the tool is useful.

## Honest limits

* **This does not replace listening to the part in context.** Practice-tracks
  deliverable 1 — the album mix with the new drums swapped in — finds
  problems a soloed A/B never will, because the rest of the band is what a
  wrong fill actually clashes with. Do the in-context listen before Gate A
  regardless.
* **The instrument grid is only as good as the MIDI it reads.** A hit
  `drums clean` mis-typed shows up mis-typed here too — this surfaces
  problems, it does not independently verify instrument identity.
* **The EZdrummer send-to-folder step is one-way.** Whatever comes back out
  of EZdrummer's Grid Editor or Song Track has to be dragged back into
  Reaper and re-imported (`rambass drums import`) by hand, same as the
  existing Stage 4/7 workflow. This tool does not close that loop
  automatically, and doing so is not worth the risk of silently overwriting
  a hand-edited MIDI item.
* **Rebuild is only as safe as the `Step` table is complete.** A producing
  command that writes a file `PIPELINE` doesn't know about won't be touched
  by a rebuild and will report "ok" by omission rather than by verdict — the
  same blind spot `rambass stale` already has today, not a new one this
  console introduces.
* **The step tables can drift from the prose they're sourced from.**
  workflow.md and drums-rebuild.md stay the authoritative explanation of
  *why*; the step tables are their structured mirror, and editing one
  without the other is a real failure mode — the same relationship code
  comments already have with the tests that pin them down, and worth the
  same discipline.
* **Still needs a browser and `rambass console` running locally.**
  `rambass status`/`rambass stale` in a terminal stay the ground truth for
  anyone without a browser handy; the console is a friendlier front end over
  the exact same data, not a second source of it.
* **One song's rebuild at a time, still.** A dashboard-wide "rebuild
  everything stale across the set" is a natural `--all`-style extension, not
  something this scope commits to now — enough could be stale across a
  whole album that it turns into an unattended-overnight job rather than a
  click, and that deserves its own design pass.
