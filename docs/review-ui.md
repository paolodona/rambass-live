# The project console

**Where the repo is, as of 2026-08-24: Phases 0–4 are built.** `rambass
console` starts the console (`console.py` over `review.py`, tested in
`tests/test_console.py` / `tests/test_review.py`); `review rebuild / clips /
render / note / promote / status` are commands. The review screen's stack was
rebuilt on 2026-08-24 -- one gutter, one mapping in beats, a playhead and a
Reaper-numbered ruler; see "the review screen's one stack" below.
**Gate R2 is answered: yes** — see "The spike, answered" below. Still open:
the Phase 5 thin lyrics/gx100 views, and a set-wide batch rebuild. The rest
of this document is the scope as designed; where it says "would", it now
mostly "does".

## The spike, answered

Measured on Paolo's machine, 2026-08-24. `EZdrummer 3.vst3` **does**
instantiate headlessly through `pedalboard` — about 12 seconds, no window,
no licence dialog — and a *fresh* instance already has a kit loaded, so it
renders without any preset wrangling. Neither half was safe to assume: the
activation path blocking a headless instantiation was the entire reason this
was scoped as a spike.

So `ezrender.py` ships, and `rambass review render <song>` replaces the hand
bounce:

```
rambass review render manlio      # -> qa/candidate.wav
rambass review clips manlio       # -> qa/clips/*.wav
```

Manlio end to end: 313.1 s rendered (last hit 309.1 s = musical bar 78 beat
2, which is where CLAUDE.md records the band stopping, plus a 4 s tail for
the ring), peak 1.000 with exactly one sample at full scale, then 15 section
pairs cut. The candidate clips come out at 12.00 / 8.00 / 32.00 / 16.00 s —
dead on the grid — against references of 12.08 / 8.10 / 31.86 / 16.09,
which is the album take breathing. That is the whole point of the A/B.

**What it deliberately is not:**

* **Not a remap.** The note numbers in the file go to the plugin unchanged.
  `config/drum-maps/ezdrummer3.yaml` still does not exist and CLAUDE.md is
  explicit that invented numbers are worse than none — so the render is
  exactly as good as the song's declared map, which also makes a wrong map
  *audible* here rather than silently corrected.
* **Not the base.** Stereo off the plugin's master, not the multi-out rig
  docs/drums-rebuild.md wants for the gig, and written to `qa/` — never
  `render/`, which docs/practice-tracks.md reserves for the deliverable.
* **Not the song's kit.** A fresh instance is on EZdrummer's default kit.
  `--preset <file>.vstpreset` is how the chosen one gets in; choosing it is
  still a human's job.

`pedalboard` is a new optional extra (`vst`), imported only inside
`ezrender.py` via `audio.require_module`, the same rule `analyze.py` /
`transcribe.py` / `stems.py` follow. `rambass doctor` reports the package and
the plug-in **separately**, because `pedalboard` installed with no VST3 found
renders nothing and would otherwise say nothing about why; `RAMBASS_VST3`
overrides the search, with `locate_tool`'s rule that a set-but-wrong override
is an error rather than a fall-through.

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
between them, the same addressing `rambass drums missing` already prints
(e.g. `23.2.1`), so a problem can be named precisely without opening Reaper.
As built, the ruler carries **Reaper's** bar numbers and the instrument grid
hangs off the same ruler in the same stack — see "the review screen's one
stack" below.

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

## The review screen's one stack, and its two clocks

Paolo, using it: *"the instrument grid with the different drum parts needs to
sit under the two wavs (same width) so that the hits align perfectly with the
waveform above"*, *"when playing there should be a cursor that shows me where
we are"*, and *"much more granular bar subdivisions eg 3.1, 3.2, 3.3 and a
vertical subtle grid on top of the wav canvas so I see where they align"*.

Three of the four things that broke alignment were not visible as bugs:

* **Two label gutters.** The A/B lanes reserved 170px and a 12px gap for their
  label; the grid rows reserved 90px and 14px. So every grid canvas was 78px
  wider than the waveform above it and started 78px further left. There is one
  `--gutter` now, used by all three row kinds, and every canvas in the stack
  carries the same 1px border — `box-sizing:border-box` is global, so a canvas
  with a border has a `clientWidth` 2px smaller than one without.
* **Four drawers, four extents.** `drawWave` spread over `canvas.width`,
  `drawRuler` over `width - 2`, and `drawGrid` put bar lines at `width` but hits
  at `width - 4 + 1`. All of it goes through one `mark.frac * width` now, and a
  hit is drawn *centred* on its line rather than starting at it.
* **Bar arithmetic on sections that are not whole bars.** Every drawer computed
  `end_bar - start_bar`, which counts `closing-fill` (77.3–79.1) as two bars
  when it is one and a half — putting bar 78 half way across the clip instead
  of a third of the way in, 9% of the section out on every line, every hit and
  the playhead. The mapping is in **beats** through the section
  (`sectionBeats` / `beatOffset` / `beatMarks`), which is also what lets a
  section start mid-bar at all, and `playheadBar` was on the same arithmetic —
  so the error reached `song.yaml` through `review promote`.

Two decisions, both Paolo's, both cheap to get wrong later:

* **The ruler prints Reaper numbers**, `bar + count_in.bars`. It is the ruler
  read next to Reaper's own and `qa/review.md` already prints them, so this is
  the same one-conversion-at-the-edge rule `restore.checklist` follows. The
  conversion happens in `drawRuler` and nowhere else: the section header above
  still says the musical bars, because that is what `song.yaml` says, and a
  stored note stays musical. `count_in_bars` is in the review payload for this
  and nothing else.
* **Gridlines are evenly spaced on both lanes**, so `align.yaml` is not
  involved. The reference take breathes — 12.08 s against the candidate's
  12.00 s on Manlio's verse-1 — so a reference line can sit up to ~90 ms from
  where that beat was actually played. That is accepted, and the payoff is
  real: both lanes map musical position linearly, so **one** playhead element
  spans the whole stack instead of one per lane with its own mapping. If
  somebody later wants the reference lines where the band put them, the data is
  there (`AlignMap.source_at`, 310 per-beat anchors on Manlio) — but it stops
  being one line.

**Clicking the stack places the playhead.** Paolo: *"we need the ability to
click on the wav/grid and move the playhead so we can zone in on a given hit or
portion"*. Either waveform, the ruler and every instrument row all take a click
or a drag, because they share one mapping — so it does not matter which row
the hit was spotted in. The position **snaps to the song's own grid**: a hit is on
the grid (`drums.subdivision: 3` on Manlio, so its shuffle triplets), and a
pixel-exact seek lands between two lines, which is never the position anybody
was aiming for. Hold alt to place it exactly. The transport reads the position
back in **Reaper's** numbers and `restore.checklist`'s format, so a spot found
by ear can be typed into `rambass section` or matched against what `rambass
drums missing` printed — while the note the same playhead would log stays
musical, as the ledger stores it.

**Two things the click exposed, both of which were the panel writing what it
never showed.** Paolo, after logging one note: *"I was not aware I was adding a
note to the 'crash' (no visual clue of what was selected) and I dont know what
1.1 refers to (again I did not select it, and nevertheless the section starts at
3.1)"*.

* `saveNote` hard-coded `instrument: "crash"` for every missing- or extra-hit
  note. The instrument is now a control on screen, populated from
  `drummap.CANONICAL` through the payload — so it cannot drift from what
  `review.promote_notes` will accept — and it defaults to **nothing**, which
  makes the note a written-down observation that `promote` leaves alone. That is
  a better default than a guess nobody was shown. Clicking an **instrument row**
  picks that instrument and highlights the row, which is the visual clue the
  panel was missing: the row is already labelled. Also the answer to *"should we
  split crash into crash, splash etc?"* — it already is. `crash`, `crash_2`,
  `crash_choke`, `china`, `china_choke` and `splash` are all in `CANONICAL` and
  mapped in `general-midi.yaml` (GM 49, 57, 49-choked, 52, 52, 55). Nothing to
  add; the panel was hiding the vocabulary.
* The chip printed the note's stored **musical** bar while the ruler above it
  prints **Reaper's**. Both were right and they disagreed by the count-in, so a
  note correctly filed at musical 1.1 read `1.1` against a ruler saying `3.1`
  and looked two bars wrong. Every number a human reads on this screen now goes
  through one `reaperAt()` — the ruler, the transport readout, the "filing at"
  line and the chips — and the stored note stays musical. Mixing two clocks
  on one screen is the cost of the Reaper-numbered ruler, and this is where
  it has to be paid.

## Hearing it in context: the band layer

Paolo: *"I would like to listen to the candidate drums in context (use the ref
aligned time warped section on top of the candidate drums), and the reference in
context too ... a switch that layers all the other instruments on top that uses
the most appropriate version (ref aligned or original)"*, and, deciding the
implementation: *"need to keep playing when toggling other instruments on or
off"*.

`b`, or the switch in the transport. It is the same trick `s` has always used:
**four elements, all playing all the time, and the switch moves `muted`.** A
paused element restarted on unmute comes back a frame or two late, which is a
flam — in a tool built to judge flams. `restart()`, `seek()` and the loop all
move every element together, so the layer is never a frame behind the drums it
is under.

**Which bed is "most appropriate" is not a preference, it is the clock.**

| audible | bed under it | why that one |
|---|---|---|
| candidate | `practice/no_drums-aligned.wav` | the render puts musical bar 1 at sample 0; the warped bed does too |
| reference | `stems/no_drums.wav` | the original take, and the original take's own band, untouched |

Crossing them is the one thing this must never do. Manlio's own map has the band
−88 to +258 ms away from the fixed grid, so the *unwarped* mix under programmed
drums flams by a quarter second by the end of the song — and a reviewer hearing
that would file it against the programmed part, which is the one thing this
screen exists to judge. So `review.aligned_bed_path` has **no fallback**: with no
warp yet the switch says so and names `rambass align <song> --fit --warp`, which
is a better answer than a bed that drifts. The pairing lives in
`review.CLOCK_OF` and is asserted from both ends — the server cuts each bed on
its side's clock, and the page's `audible()` can only ever pair a take with its
own bed.

Two smaller consequences worth not re-deriving:

* **The side is read off the clip's file name, longest suffix first.** It used
  to be `endswith("-cand.wav") else "ref"`, which calls `...-cand-band.wav` a
  reference and would have cut the warped bed on the recording's clock. The
  section slug sits in the same string and is free to contain the word "ref".
* **The recorded bed carries the stems command and the warped one carries
  nothing.** `rambass stems --drums-only` writes `no_drums.wav` as well as
  `drums.wav` and is already whitelisted by `run_step_command`; the warp is a
  practice step and practice is deliberately kept off the stage screens
  (`review.STAGE_OF_STEP` — a stale warp must never ring a show column), so
  there is nothing for a button to run and the hint is the fix.

**The bed plays at 0.6**, tuned down by ear in three passes (0.8, then 0.7,
then 0.6 — each tenth off the full mix rather than off the number before it). The bed is a full band mix
and the candidate is a bare kit, so at equal gain the part being judged is the
quieter of the two. It is a gain on the *bed*, never a cut on the drums:
attenuating those would change what a velocity sounds like, which is one of the
things a review pass is listening for. One constant, `BAND_VOLUME` at the top of
the page's script — lower it to push the band further back.

The lanes are **104 CSS px** tall — 208 device pixels at DPR 2, twice what they
were. Paolo: *"so I can better see the waveforms, even the fainter hits"*. The
peak drawing is linear in amplitude, so a ghost note gets exactly twice the
pixels; nothing else in the stack moves, because the playhead spans the stack
rather than being sized to a lane.

**Clips are served with byte ranges, or none of the above works.** `_clip`
answered every request with 200 and the whole body, ignoring `Range` and never
sending `Accept-Ranges` — and a browser treats a media resource with no range
support as **not seekable**, so assigning `currentTime` snaps back to the start
of what is buffered. Clicking the waveform computed the right position and
playback restarted from the beginning, which reads as a page bug and is not one:
no amount of work on the page could have fixed it. `parse_byte_range` handles
the three single-range forms a media element actually sends, and answers the two
kinds of no differently — a header it cannot parse is ignored (RFC 9110; a
clip that 400s here is a clip that never plays), while a well-formed range past
the end is the 416 a player needs to correct itself from. The handler is on
HTTP/1.1 now, because seeking is a stream of range requests and HTTP/1.0 closes
the connection after each one. One more of the same family: seeking to *exactly*
`duration` fires `ended`, so clicking the right-hand edge of a lane restarted
the loop — `seek` stops 10 ms short.

**The notes panel reads as a list, and a note can be retired.** Paolo: *"there
is a `[open]` string that does nothing, also how do I remove a wrong note?"* and
*"can the notes be listed in a ordered list rather than like tag/pills? (they
pile up horizontally and are difficult to read)"*. Both the same panel. The
notes were inline pills, which wrap mid-sentence and have nowhere to put a
control; they are a list now — position, kind, instrument, comment, then the
acts — and `[open]` is gone, because it was the *default* status printed on
every row, which said nothing and read as a button. A status badge is drawn
only for `promoted` and `dismissed`.

Two ways to retire one, deliberately kept apart:

* **dismiss** is a judgement worth keeping — "I listened, it was nothing".
  `NOTE_STATUSES` has had `dismissed` since the ledger was written and nothing
  could set it; `review_markdown` already marks it `[-]`, and `promote_notes`
  only ever touches an open note. Reversible.
* **remove** is for an entry that should never have existed — the wrong bar,
  the wrong instrument — and leaves nothing behind. It asks first, because the
  comment is typed by hand. `remove_note` **refuses a promoted note**: its edit
  is in `song.yaml` and `drums restore` applies that blindly, so dropping the
  ledger entry would leave the addition in place with nothing on record saying
  where it came from. The refusal names the entry to take out instead.

Identity is `note_key` — the fields a human filed, joined. `qa/review.yaml` has
no ids and should not grow any: it is a hand-editable file, and an id is a thing
whoever edits it has to maintain for no benefit of their own. An index would
have done, right up until the list re-sorted under two open tabs and deleted the
wrong row.

**And the loop has to actually close.** Paolo: *"once I have added a note, how
is that processed? should I hit rebuild and the notes should be incorporated?"*
The honest answer was no, and nothing said so. The chain is:

1. the note lands in `qa/review.yaml` — raw, undecided
2. **Promote** turns the confident ones (`missing-hit` / `extra-hit` with an
   instrument in `CANONICAL`) into `drums.additions` / `removals`; a timing or
   velocity complaint is left for a human, which is `PROMOTABLE`'s whole point
3. `git diff` — machine proposes, git reviews
4. **Rebuild** — `drums restore` watches `drums/additions`, so the promote makes
   `midi/drums-restored.mid` stale and this regenerates it
5. **Re-render the candidate**, which is the step that was missing

`qa/candidate.wav` is *not* in `provenance.PIPELINE` and should not be: it needs
the VST3 plugin, so on a machine without one every extracted song would report a
missing artifact and Rebuild would fail on a step that is not part of the show —
the same argument that keeps the practice pair out. So `candidate_state` reports
whether the candidate is older than the MIDI it was rendered from, the screen
says so in an amber bar with the button that fixes it, and the whole chain is
written next to the three buttons that carry it out. Without that, promoting and
rebuilding played back the audio from before the edit and the note looked like it
had done nothing.

The other half was the clip cache: `_clip` cut a clip on first request and kept
it forever, so a re-rendered candidate went on being A/B'd as the old audio. It
is an mtime check against the source **and** `song.yaml` — every reason the
source moved counts, including a section boundary edit, which does not move the
audio but does move where the clip starts and stops. Resolved from the clip's
own file name rather than from `clip_spans`, because a scrub is a stream of range
requests and every one of them lands there; parsing a 310-anchor align map per
request is not the place to be.

Not built, and the obvious next thing if the click is not enough: **looping a
selected range** rather than the whole section. Drag-select two positions and
loop between them, which is what "or portion" would want for A/B-ing one bar
over and over. The clip is already cut per section, so this is a pair of
fractions and a `timeupdate` check, not new audio.

The grid resolution is the song's own `drums.subdivision`, which the payload
already carried and the page ignored: 3 on Manlio, so the subtle lines are the
shuffle triplets its hats and its 4.667 kicks actually sit on. Lines and labels
both thin out rather than crowd — `theme-finale` is 48 beats in ~1100px, where
a label per beat cannot fit and a line per triplet is mush, while
`theme-intro-stop` is 8 beats and can carry both.

Two tests run the page's geometry **in node** (skipped when node is absent, the
same rule as `test_the_pages_script_parses`, ffmpeg and librosa): one exercises
`beatMarks`/`positionAt` on Manlio's two awkward sections, one draws a lane, a
ruler and a grid row onto recording canvases of equal width and asserts a bar
line lands at the same x in all three. Checking that by eye in a browser and
writing nothing down is what CLAUDE.md's rule about hand verification is for.

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
4. **Sections** (`rambass sections`) — an **Open**, not a Run: writing the
   section list is a hand edit to `song.yaml`, not a rebuild. It opens the
   sections editor, below.
5. **Consolidate** (`rambass drums consolidate`) — Run. Split from the row
   above, which used to do both jobs: the note "mark the sections first" was
   doing the work a UI should, and the dependency between the two — real, and
   already wired, since the step carries `sections` in its provenance fields —
   could not be seen while one row was both the cause and the effect.
6. **Missing hits & restore** (`rambass drums missing`, `rambass drums
   restore`) — this is the step that gets an **Open**, not a Run, because
   deciding what's actually missing needs ears, not a report. It opens:

#### The sections editor

`#/sections/<slug>`, reached from the Sections row. The list with its Reaper
position, its span in bars (from `consolidation_spans`, the extent
`consolidate` actually uses, not a rounded one), its name, its declared
`backbeat` and its note; a form to add or replace one; `×` to remove; and
`check_sections`'s findings underneath in its own words. Everything on it comes
from `sections.section_table`, which is also what `rambass sections` prints, so
the screen and the terminal cannot disagree about where a section is.

**Every bar number on this screen is Reaper's, in and out.** The table reads the
ruler, the position field is labelled as the ruler, and `sections.to_musical`
does the one subtraction server-side — which is the point of the screen
existing: `rambass section` takes musical bars unless you remember
`--reaper-bar`, so the arithmetic was being done in somebody's head, and that is
the kind of arithmetic that is right nine times and wrong once. The name field
is backed by a `datalist` of the existing names, because identical names are
pooled by `consolidate` into one part and stamped bit-identical — picking an
existing one is a deliberate act worth making easy, and typing `verse` when you
meant `verse-2` is a real and silent mistake.

Two defects it exposed, both fixed here rather than worked around:

* **There was no way to remove a section.** `rambass section` only added or
  replaced, so undoing a mis-typed boundary meant opening `song.yaml` in an
  editor. `sections.remove_section` and `rambass section-rm` now exist, and the
  refusal for a position that holds nothing lists the ones that do — in Reaper's
  numbers, because a human getting this wrong is reading the ruler when they do.
* **Replacing a section dropped its `backbeat` and `note`.** `cmd_section_add`
  filtered out the section at that position and appended a fresh `Section`, so
  correcting a *name* silently un-declared the arrangement. On Manlio that is
  three verses of side-sticks — settled by ear once, never re-derived from audio
  (CLAUDE.md) — vanishing with nothing in the diff to explain why the clicks had
  stopped. `add_section` carries both forward unless they are given explicitly;
  `--backbeat ''` clears one deliberately.

`sections.py` gained the editors, and its docstring had to be amended: it said
the module "never edits". That claim is about *inventing* a section — a musical
judgement no tool should make, and `check_sections` still changes nothing, ever
— but applying a boundary somebody heard is a different act, and it belongs
beside the checker rather than in a second module.

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
3. **Instrument grid**, in the same stack as the waveforms and on the same
   ruler, so a hit sits under its own transient. One row per canonical
   instrument name with a hit in the visible bars — kick, snare, sidestick, every tom, hi-hat
   closed/open, ride, ride bell, every cymbal — rendered straight from the
   MIDI (`midiio.read_drum_midi` + `drummap.CANONICAL`). This is the
   toms-and-cymbals visibility Reaper's default drum view does not give.
4. **Notes panel.** A text box plus a `kind` chooser (missing-hit /
   extra-hit / swap-hit / wrong-instrument / timing / velocity / other), pinned
   to the bar the playhead is on. A **swap** reveals a second instrument
   select — the drum that is playing, and the drum it should be. Existing notes for the visible range show inline
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
| `shift`+`space` | play the section again **from the top** — the thing a review pass does over and over. Plays whether or not it was already playing, and leaves the side and the band layer as they were |
| `s` | switch which sample is audible — candidate ↔ reference — without restarting playback |
| `b` | lay the rest of the band under whichever side is audible — warped bed under the candidate, album mix under the reference. A mute, so it never stops the audio |
| `n` / `p` | next / previous section |
| `,` / `.` | previous / next **bar** within the current section |
| `shift`+`D` | mark this section done, or reopen it — the pips under the header are every section, green for the ones already listened to |
| `g` | divide the snap grid: the song's subdivision, then halves and quarters of it. `alt`+click is still no snap at all |
| `shift`+`R` | promote every confident note, rebuild what that makes stale, re-render the candidate, and come back to this section |
| `l` | toggle loop on/off |
| `m` | log a **missing-hit** note at the playhead's bar and focus the comment box — the commonest Stage 7 edit, one key away |
| `x` | log an **extra-hit** note the same way — the other half of that same edit |
| `Enter` | commit the open note, return focus to the transport |
| `Esc` | discard the open note draft, return focus to the transport |
| `r` | rebuild everything stale for this song, then refresh the clips and this view |
| `d` | back to the dashboard |
| `?` | show this list on-screen |

**Right-click a grid row to file the edit where you heard it.** Paolo: *"if the
playhead is at 16.2 and the hihat_closed grid lane is selected I should be able
to right-click on that specific hit and get add "xxx" here, remove, or swap with
"xxx" ... the same as using the notes section below, but quicker and in
context"*. The row names the instrument, the pointer names the position — the
two controls the panel makes you touch — and the menu files the same three notes
into the same ledger for the same `promote`. Nothing here writes to `song.yaml`.

What it offers depends on what is under the pointer, read off the same `ticks`
the row was drawn from, so the menu and the eye cannot disagree: on a hit,
*remove* and *swap for …*; on an empty grid line, *add*. The other items are
shown disabled with the reason in their tooltip rather than hidden, because a
menu whose items move is a menu you have to read every time. The swap's `go`
stays dead until a drum is named — a swap filed to whatever happened to be first
in a list is worse than no shortcut. Position snaps to `drums.subdivision` and
reads in Reaper numbers like everything else on the screen; the stored note is
musical. `menuModel` is pure and checked in node, because the one thing this
must never do is offer "remove" where there is no hit.

Right-click does **not** move the playhead: `scrubFrom` is left-button only now.
Seeking the audio out from under the ear while a section loops is exactly what
you do not want from the gesture that files a note about what you just heard.

**The transport's readouts are fixed-width.** The row is centre-justified, so a
readout that grows a character pushes half of it left and the other half right —
and the playhead readout is rewritten on every animation frame while playing, so
that jitter is continuous. `#at` is 9ch (`100.1.895` is the widest a Reaper
bar.beat gets), and the band switch, the loop state and the which-side readout
are sized for their longest label.

**A removal cannot delete a hit `drums.additions` declares — so promote does
not write one.** Paolo, on Manlio: *"I have a promoted note 11.4.667 swap-hit
crash → hihat_open but the midi now includes both the hihat_open and the
crash"*. The crash at that position was never in the transcription; `drums
missing --propose` had put it in `drums.additions`. `apply_edits` applies
removals to the performance it was handed and adds the additions afterwards, so
the swap's removal matched nothing and the part came out with both drums —
while the ledger, the diff and the console all said the promotion had worked.

`promote_notes` now asks whether the hit is a **declared** one (`_declared_hit`)
and edits the declaration instead:

| the hit is | swap-hit | extra-hit |
|---|---|---|
| in the transcription | removal + addition, as before | removal |
| in `drums.additions` | that entry's `instrument` is rewritten in place | that entry is retracted |

Both are exactly reversible, which is why `demote` can invert them: a swap moved
one field of one addition and moves it back (the note carries both drum names),
and a retracted declaration comes back with the velocity the note carries —
which is why the grid menu files the hit's own velocity on a removal too. The
diff is better as well: one line changing `crash` to `hihat_open`, rather than a
removal contradicting an addition three screens further up the file.

A pair written **by hand** still works — a removal plus an addition of the same
drum at the same position is a legitimate way to re-voice a transcribed hit's
velocity — but `apply_edits` now reports it as `contradicted` and `drums
restore` prints a line saying the addition wins and the hit stays. It is also no
longer counted as a *stale* removal: it matched something, just not a hit, and
two complaints about one line send the reader in two directions.

**A note belongs to a section in beats, not in bar numbers.** `renderChips`
filtered with `note.bar >= start_bar && note.bar < end_bar`, which looks
equivalent to the span and is not: Manlio's verse-2 runs 20.3 to **28.3**, so a
note at bar 28 failed `28 < 28` and disappeared from the section it was filed
in — while turning up under verse-2-lift, which starts at 28.3 and never
contained it. Paolo, filing the same tom three times: *"it won't let me"*. It
had let him, three times, into a list he was not looking at. `inSection()` uses
the same `fracFor` arithmetic as every line, hit and playhead on the screen, for
the same reason the drawers do (docs above): sections here rarely start on a bar
line, so this is the common case rather than a corner.

That bug is also why de-duping no longer stays completely quiet: silence plus an
invisible chip reads exactly like a refusal. A filing that added nothing now
says "already noted at 30.2.667" in the log the screen already answers in — the
grid menu says the same — and the note itself is untouched.

**A note that only corrects an earlier one is folded into it.** Paolo: *"First
I have added a tom_mid, then swapped with tom_high, those two notes can be
replaced with missing-hit tom_high"*. That pair is not two observations, it is
one observation and a correction to it, and the ledger should say what the part
needs: one hit, of the drum it ended up being. `merge_notes` folds a
`swap-hit(X→Y)` into a `missing-hit(X)` at the same position, chains as far as
they go (`crash → crash_2 → china` is one missing china), and folds swaps of a
*transcribed* hit into a single swap naming where it ended up.

Order in the file is not the signal — the ledger is sorted by position, so two
notes at one position keep whatever order they were written in, and on Manlio
the swap came out first. It matches on instruments instead.

**Only when both halves are in the same state.** A promoted `missing-hit` whose
swap is still open describes a manifest that says the *old* drum; merging then
would have the ledger claim the new one was promoted. Once the swap is promoted
too, `promote_notes` has rewritten that declaration in place (see the removal
rule above), so the merged note is exactly what `song.yaml` says — which is why
every promote path runs the merge straight after promoting, and `add_note` runs
it when the pair is filed open in the first place. An `extra-hit` is never
merged away: "there is a hit here that should not be" is its own observation.

**One observation, one note.** Paolo: *"ensure we cannot create duplicated
notes ... trying to remove the same hit twice should not yield a duplication
(silent de-duping per note/type/instrument/time)"*. Easy to do now that a note
is two clicks in the grid with no typing, and a second copy says nothing the
first did not — `promote` skips it as already done while the ledger reads as two
problems. Identity is `observation_of()`: bar, beat, phase, kind, instrument and
swap target. **Not** the comment — two passes over one missing crash write two
different sentences about it, and it is still one missing crash — and not the
status either.

Two things carry over from the second filing, because both are information the
ledger did not have: a **dismissed** note comes back open (filing it again is
asserting it again, and swallowing that leaves a chip the eye reads as struck
out), and a **comment fills in** where there was none. A promoted note stays
promoted: its edit is in `song.yaml` already. Silent in the console, where the
chip is already on screen; `rambass review note` says "already noted … left as
it was", because "noted" for a note that added nothing is a small lie.

De-duping is in `add_note`, the one funnel both the CLI and the console use, so
a hand-edited `qa/review.yaml` can still contain a pair — and `remove_note`
still takes exactly one of them, which is what its test now writes the ledger
directly to prove.

**Promote is not a one-way door any more.** `demote` on a promoted chip, or
`rambass review demote <song> <bar>`, takes the edit back out of `song.yaml`
and reopens the note. It matches on the same `(bar, beat, instrument)` triple
`promote` keyed on, so a hand-written addition at the same bar is not swept up
with it; a swap takes both of its edits. An edit that has already gone by hand
is not an error — refusing then would leave a note stuck in `promoted` with
nothing behind it, which is the state that is actually wrong. Before this the
only way back was editing the manifest, which is the one thing the console
exists to avoid, and `remove_note`'s refusal now names the command instead of
the YAML key.

**Sections can be ticked off.** Paolo: *"mark a section as Done so that when I
reopen the project I know I can skip it"*. `shift`+`D`, or the control under the
section name. It lives in `qa/review.yaml` beside the notes — a record of what a
human did in a review pass, not a musical fact, and nothing `drums restore`
reads should have to step over it — and it is keyed by **position, not name**:
two sections can share a name (CLAUDE.md: identical names are one part), so a
name would tick both from one listen, and a section that moves is one whose
clips were re-cut, which is when the mark should stop following it. Three cues,
because the question gets asked two ways: a badge on the section in front of
you, a control that says which way it will move, and a pip per section under the
header — green for done, outlined for where you are, clickable to jump, with the
count beside it. `write_ledger` defaults `done` to what is already on disk,
because it rewrites the whole file and a note saved afterwards would otherwise
wipe every tick.

**One control for the sequence.** Paolo: *"promote, rebuild and re-render ...
three actions that are always in sequence. Would I ever need to do them
separately?"* — sometimes, yes: promote alone is how you read the `git diff`
before anything touches the MIDI, rebuild alone is for a change that came from
somewhere else (a section edit, a re-transcription), and re-render alone is for
a candidate stale against a MIDI nobody needs to rebuild. So the three stay, and
`shift`+`R` runs the sequence that a review pass runs every time.

Order matters in both directions, and `promote_rebuild_render` enforces it: the
promotion is **saved to disk first**, because the rebuild runs `drums restore`
as a subprocess and that reads `song.yaml` — an addition still only in memory
would be rebuilt away — and the render is **last**, or it renders the part from
before the rebuild. It stops early two ways: a failed rebuild does not go on to
render (audio that looks current and is not is worse than no audio), and a run
where nothing was rebuilt and the candidate is already fresh renders nothing at
all, because a quarter of an hour of VST time for a click that changed nothing
is not a no-op. Then the screen reloads **on the same section**, which is the
point — you press it to hear the edit you just made, where you made it.

**The snap grid divides.** `g` cycles the song's own subdivision, then halves
and quarters of it (`1/3 → 1/6 → 1/12` on a shuffle). Paolo: *"I need to add a
hit between 4.4.333 and 4.4.667 and I cannot snap at the correct point"*. The
song's subdivision stays the default because that is where the *hits* are, but a
note is sometimes about a place between two of its lines, and the only
alternative was alt — no snap at all, filing whatever decimal the pixel happened
to be. One `snapSubdivision()` feeds the snap, the playhead's reported beat, the
drawn grid and the right-click menu, so the line you see, the position you land
on and the beat that reaches `drums.additions` cannot disagree.

**Two bugs the above turned up, both worth not re-introducing:**

* **An abandoned screen kept playing.** `renderReview` builds a *new*
  `ReviewView` on every rebuild and re-render, and the router builds one per
  navigation — while the four audio elements belong to the old instance and are
  not in the DOM, so nothing stopped them. They played on under the new screen,
  answering to no transport: the new view's space bar stops the new view's
  elements and the orphan keeps going. It was true of the candidate and the
  reference from the start; the band layer is only the half you cannot miss.
  `stopReview()` is called by both doors now.
* **Clips are served `Cache-Control: no-store`.** A clip is re-cut whenever its
  source moves, but the URL does not change when it does — so a browser holding
  the old bytes would go on playing the part from before the re-render, which is
  the one lie this screen must not tell. There is no validator to revalidate
  against, so `no-store` rather than `no-cache`; the file is on the same machine.

**`swap-hit` is the third confident kind.** Paolo: *"a hit may be correct but
with the wrong item, for example swapping an open hi-hat to a crash or a crash
to a crash_2"*. `restore.apply_edits` already calls that the commonest edit
there is — its removals run before its additions precisely so a replacement is
one edit — but the ledger had no way to say it in one note, so a swap took two
notes that `promote` could not tell were one decision. It carries both drums
(`instrument` is the one playing, `swap_to` the one it should be) and promotes
to **a removal and an addition at the one position**, each cross-referencing the
other in its `note:`. Without a target it is skipped, not guessed at — a swap
with one end named is exactly a `wrong-instrument` observation, which is what
that kind stays for: *this is not that drum, and I do not yet know which one it
is.* The velocity rule is unchanged: blank means `apply_edits` takes the median
of the new instrument's own hits, which is better than inheriting a number that
meant "loud for a hi-hat".

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

### One job per song at a time

A separation is three minutes, and for all of it the button looks unpressed --
so a second press is what a person does next, and the server is threaded, so
both ran. Paolo watched two demucs runs of Manlio start together, writing the
same `stems/.demucs/htdemucs_ft`. Three things stop it now:

* a **per-song lock** in `console.py`: a second `/api/rebuild` or `/api/run`
  for a song already building is a 409 naming the song, released on every way
  out including the raise that becomes a 400. Per song, because that is where
  the collision is -- two different songs rebuilding is only slow;
* the page **disables its buttons** while a job is in flight and says progress
  is in the terminal;
* a per-step button **runs its own step**. It used to carry the command string
  and post an empty payload, which means *the whole chain*: "Find the tempo"
  started a demucs separation. A row the staleness table tracks posts
  `{step}` to `/api/rebuild`; a row it does not (analyze, lyrics
  transcribe/check, video card, countin) posts `{command}` to `/api/run`,
  whitelisted against the commands that song's own screens offer.

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
    # lazily, per bar) under a scratch cache dir — gitignored. Cuts the two
    # band beds as well when they exist, each on its own side's clock; a
    # missing bed is reported, never fatal — the A/B does not need it
rambass review note <song> <bar> "comment" [--kind missing-hit] [--instrument crash]
rambass review note <song> <bar> "comment" --kind swap-hit     --instrument hihat_open --swap-to crash
    # add a note without the UI — keeps the ledger scriptable and testable
rambass review promote <song>
rambass review demote <song> <bar> [--instrument crash] [--beat-any] [--reaper-bar]
    # the other half of promote: takes the edit back out of song.yaml and
    # reopens the note, for one promoted by mistake
rambass review status [--album ...]
    # open-note counts per song, same shape as `practice status`
rambass review rebuild <song> [--stage <name>] [--step <name>] [--force <artifact>]
    # runs every stale/missing PIPELINE step in scope, in order, via the
    # exact commands `rambass stale` would print. --force <artifact> is the
    # one-artifact-at-a-time override for edited/unknown, and writes a
    # `.bak` first. This is what a rebuild control calls, and it's a
    # first-class command so it works from a terminal too.

rambass review render <song>            # Optional `vst` extra required.
    # renders the drum MIDI through a VST3 instrument, headless, to
    # qa/candidate.wav. --plugin / --preset / --tail / --sample-rate.
    # Still not required for anything else above to work: without it,
    # `review clips --candidate <wav>` takes a hand bounce as before.
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

The `ezrender.py` spike needs the actual plugin to test at all, so that one
test skips when it is absent, the way `stems.py`'s demucs call already does.
Everything *around* the plugin is in the ordinary suite (`tests/
test_ezrender.py`): which clock the render sits on — musical bar 1 at sample
0, no count-in, guarded the same way the clip boundaries are — that every
note_on gets a note_off, that the render runs past the last hit so a closing
crash can ring, and `locate_plugin`'s override rules.

## Plan

| phase | what | blocked by |
|---|---|---|
| **0** | the console shell: the dashboard, the generic stage-screen renderer, the step-table data for every `STAGES` entry (content-authoring from workflow.md/drums-rebuild.md, not new design), extend `provenance.PIPELINE`, the rebuild selector + `--force` path | — |
| **1** | the drums cluster's data: clip-boundary functions, `qa/review.yaml` model, CLI (`clips`, `note`, `promote`, `status`) | Phase 0 |
| **2** | the drums review tool: transport, instrument grid, notes panel, against a manually-bounced candidate wav | Phase 1 |
| **3** | "send to EZdrummer" file export | Phase 2 |
| **4** (spike — **answered yes, shipped**) | `ezrender.py`: `pedalboard` hosts EZD3's VST3 headlessly; `review render` writes `qa/candidate.wav` | — |
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

**Gate R2** — the spike has a yes/no answer. **Answered yes on 2026-08-24**;
`review render` replaces the manual bounce, and the manual bounce stays
available as `--candidate <wav>` for anyone without the plugin.

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
