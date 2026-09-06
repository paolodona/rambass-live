# Unification — a proposal, not a decision

> **Status: PROPOSED. Nothing here has been agreed and no work has started.**
>
> This document exists so the question can be argued from evidence instead of
> from instinct. It is a survey of what the three repos actually share, a
> recommendation, and a sequencing — written down *before* any of it is done, so
> that deciding not to do it is a cheap and legitimate outcome.
>
> **Read the "Reasons to say no" section before the plan.** It is not a
> formality. Two of the objections there are strong enough on their own to sink
> the whole thing, and the correct decision may well be to take step 0 (a small,
> standalone bug fix that stands on its own merits), do nothing else, and revisit
> after the gig.
>
> Nothing in this document should be cited by any other file as though it were
> settled. If a later doc, docstring or CLAUDE.md rule needs a fact from here,
> that fact has to be decided and moved to its permanent home first.
>
> - **Raised:** 2026-09-06, by Paolo, as a question: *"I am wondering if I would
>   be better off creating a single app."*
> - **Decided:** not yet.
> - **Decision owner:** Paolo. Sole maintainer of all three repos.
> - **When it needs deciding:** partially urgent — see *The clock on part of
>   this*. Most of it can wait indefinitely.

---

## The three repos as they stand

| | `rambass-live` | `gx100` | `guitar-practice` (Woodshed) |
|---|---|---|---|
| job | backing tracks, drums, lyric video, Reaper for the gig | tone laboratory for the BOSS GX-100 | slow down / transpose / loop / count reps |
| age | 2026-08-22 | 2026-08-30 | 2026-09-05 |
| commits | 96 | 146 | 50 |
| python src | ~16.5k lines | ~29.7k lines | ~6.2k lines |
| tests | 35 files, ~17.4k lines | 41 files, ~18.1k lines | 23 files, ~7.0k lines |
| docs | ~5.7k lines + 672-line CLAUDE.md | ~11.0k lines + 352-line CLAUDE.md | ~1.6k lines + 160-line CLAUDE.md |
| core deps | pyyaml, mido, numpy | mido, rtmidi, numpy, scipy, soundfile, librosa, pyloudnorm, pydantic, pyyaml, pandas, matplotlib, typer, rich, fastapi, uvicorn, markdown | pyyaml, pydantic, numpy |
| python | `>=3.10` | `>=3.12,<3.13` | `>=3.12` |
| CLI | argparse | Typer | argparse |
| server | stdlib `ThreadingHTTPServer` | FastAPI + uvicorn | stdlib `ThreadingHTTPServer` |
| front end | one 2,944-line `console.html` | `app.js` 1,740 lines + wavesurfer | ES modules, ~4.7k lines, design tokens from artboards |
| songs on disk | **32** | 0 (template only) | 0 (template only) |

All three are under three weeks old. That cuts both ways and both ways matter:
the cost of merging is near its lifetime minimum right now, and the evidence
that they *need* merging is correspondingly thin.

---

## The evidence

Everything in this section is a fact checkable in the repos today, not a
projection. The recommendation stands or falls on it.

### 1. The three repos have already diverged on a fact about the pedal, and the wrong version is in the code that runs at the gig

This is the strongest single piece of evidence and it is not hypothetical.

`gx100`, measured **on the unit** on 2026-09-06 —
`docs/protocol/unknowns.md:406-441` and `src/gx100lab/device/device.py:625-660`:

- `PC n` loads memory `n`. A plain identity. There is **no** PROGRAM MAP
  indirection, and the map was explicitly eliminated as an explanation
  (`unknowns.md:550-572`).
- **`CC 0` / `CC 32` (Bank Select) wedged the unit.** It stopped answering
  SysEx entirely — `identify` and every `read_block` timing out over 5.4 s —
  until its power was pulled. The doc's conclusion is verbatim: *"Strip the
  bank selects and keep the PCs."*

`rambass-live/src/rambass/gx100.py:216-224` emits, for every patch change in
every song:

```python
control_change(control=0,  value=event.bank)   # Bank Select MSB
control_change(control=32, value=0)            # Bank Select LSB
program_change(program=event.program)
```

...and `memory_to_index` / `ProgramMap.slot_for` / `config/gx100.yaml`'s
`sequential_default` model the bank-and-program-map indirection the unit does
not have.

`guitar-practice/docs/05-foot-control.md:99-110` then propagates it forward:

> Note the gotcha `rambass-live/docs/gx100.md` already documents: **a PC number
> does not name a memory.** [...] `rambass-live/src/rambass/gx100.py` already
> builds those messages correctly, including the CC#0 → CC#32 → PC ordering.

**One fact, three copies, two of them wrong, and the wrong one is what Reaper
will send on stage.**

Scope, honestly stated: for PC 0/1/2 — the band's four patches — rambass's
arithmetic produces the right PC number by coincidence, because
`bank, program = divmod(index, 128)` gives bank 0 for any memory below 128. So
the *numbers* are right for this gig and the *bank selects* are the live hazard.
Above memory 127 it would be wrong outright. And note the counter-evidence
already on record: the September 2023 gig (`Phooffi.RPP`) sent exactly this
CC0/CC32/PC form and it worked, apparently because the unit sat on its play
screen (`unknowns.md:687-693`). So this is "a documented way to wedge the pedal
that we happen to have got away with once", not "the gig is guaranteed to fail".

That is still the wrong side of a risk to be on, and it is the wrong side for a
structural reason: **nothing in any of the three repos can notice the
disagreement.** No test spans them. The fix is trivial; the fact that it was
needed is the argument.

### 2. Three would-be clients, one physical pedal, no arbitration

Raised by Paolo, 2026-09-06: *"the three individual apps now may try to get hold
of the gx100 and trip each other, or conflict in switching the gx100 between
practice, recording or reamp mode."*

Correct, and the failure has a precedent in the repo already.

**The MIDI port is shareable, not exclusive** (`device/midi.py:318-321`). That is
worse than exclusive, because there is no error to see. `unknowns.md:159-174`
records what happened when BOSS TONE STUDIO was open at the same time as a
`gx100lab` read: Tone Studio's `DT1` replies are well-formed SysEx from the right
device, and one was accepted as the answer to *our* question. It surfaced only
because a read-back safety rule refused a write that had actually succeeded. The
entry's own conclusion:

> note what it cost — a *false* refusal — and what it would not have caught: had
> the stray reply carried the expected number of bytes for a read, nothing would
> have complained and **another application's data would have been recorded as a
> measurement**.

Two of Paolo's own apps on that port would reproduce this exactly.

**And the modes are worse.** `device/modes.py` and `docs/01-hardware.md:262-282`
already set it out: PRACTICE / RECORDING / REAMP differ in five values on one
`[MENU] → <USB>` screen, they are system-wide rather than per-patch, **nothing
about a loaded patch says which mode the unit is in**, and — the module docstring
again — *"the failure is silent in both directions. Practise with the lab's
`DRY TO EFX` and the backing track arrives through the distortion; record with the
lab's `EFX OUT LEVEL` and the take is 26 dB down for no visible reason."*

Woodshed wants PRACTICE. A reamp sweep wants REAMP. Mixing a base wants
RECORDING. Nothing arbitrates, nothing records who set what, and nothing
notices.

One nuance that makes this *more* tractable than it looks: `modes.py`
deliberately reads and never sets, on CLAUDE.md's second hardware-safety rule.
But `console/jobs.py` records the write side of `0x00004000` as verified on
2026-09-06 (`verified.yaml` `system:` "USB LEVELS ARE WRITABLE"). So a real mode
*switch* is now legitimately buildable, where a month ago it was not.

### 3. One song, three schemas, and the duplication has not happened yet

`rambass-live/songs/` holds 32 songs. `gx100/songs/` and
`guitar-practice/songs/` hold a `_template` each and nothing else.

The overlap is real and already designed for. The five covers
(`songs/covers/`: The Final Countdown, 18 and Life, I Want It All, John Holmes,
Danneggia l'Erezione) are songs Paolo will practise (Woodshed), needs a patch
for (gx100), and has on the backing-track board (rambass). All three `song.yaml`
schemas already carry `slug`, `title`, `artist`/`album`, a tempo block and a
section list. `songs/covers/03-i-want-it-all/song.yaml` even carries
`status.rehearsed` — which is Woodshed's readiness concept, sitting in rambass,
unpopulated.

The cross-references are designed but not resolvable: Woodshed's
`manifest.py:138` declares `patch: str | None = None  # optional: a patch id in
gx100 songs/<slug>/song.yaml`, and nothing can check it.

**This is the one item with a clock on it**, and the clock is not the gig — it is
the moment the other two `songs/` directories get populated. Today the decision
is free. Once 32 songs exist in three places it is a migration.

### 4. Real, acknowledged library duplication

Not inferred — the copies say so in their own docstrings. `guitar-practice`
alone carries **67 cross-repo path references** to its two siblings, 18 of them
carrying a line number (13 of those in source files)
(`rambass-live/src/rambass/cli.py:71`, `console.py:58`, `project.py:48`,
`project.py:67`, `audio.py:28-214`, ...).

| concept | copies | notes |
|---|---|---|
| demucs wrapper | **3** | `rambass/stems.py`, `woodshed/separate.py`, `gx100lab/analysis/separate.py` |
| ffmpeg location + `require_module` | 2 | `rambass/audio.py:75,163` → `woodshed/tools.py:162,249`, "lifted and adapted" |
| `doctor` | 3 | `woodshed/doctor.py:4` cites `rambass/doctor.py:14-20` and `:147-169` |
| repo discovery + `slugify` | 3 | `woodshed/library.py:28,48` cites `rambass/project.py:48,67` |
| tempo refinement | 2 | `woodshed/tempofit.py:10` — "lifted verbatim" |
| click DSP | 2 | `woodshed/click.py:104` — "lifted verbatim" |
| HTTP range serving | 2 | `woodshed/server.py:134` cites `rambass/console.py:58` |
| CLI UTF-8 fix | 2 | `woodshed/cli.py:69` cites `rambass/cli.py:71` |
| background job runner | 3 shapes | `gx100lab/console/jobs.py` (developed), `rambass/console.py` (ad-hoc thread), `woodshed/capture_runner.py` |
| GX-100 control | 2 models | `rambass/gx100.py` (doc-derived) vs `gx100lab/device/` (unit-verified) — see §1 |

Rough size: 1,500–2,500 lines of duplicate-ish code, which would collapse to
perhaps 900.

**The lifts are careful, well-reasoned and correctly attributed.** That is
exactly the problem. They are as good as copies get, and they still cannot tell
you when the original moves. Two of the line-numbered citations were checked
while writing this and both are still accurate — because they are a day old.
Nothing will say when they are not.

### 5. The UI assets are unevenly distributed

Each repo has one thing the others should have and cannot get to:

- **Woodshed has the design system.** `web/tokens.css` — 85 lines of custom
  properties derived hex-by-hex from real artboards in `design/*.dc.html`. One
  accent; semantic colour never doubles as it; tabular figures because the rep
  count must not shift width. This is what "the practice app has a way better
  look and feel" is made of, and it is portable as-is.
- **rambass has the waveform instrument.** `console.html` — one gutter, one
  `mark.frac * width` mapping, one playhead across lanes, zoom that is
  explicitly a view and never touches the loop, and four alignment bugs found
  and fixed. Woodshed's own `docs/01-architecture.md:143` says: *"read that
  section before drawing a single pixel."*
- **gx100 has the job runner.** `console/jobs.py` — subprocess jobs, chunked
  (not line-buffered) reads so pytest's progress dots appear, SSE streaming, and
  a command allowlist that refuses `--save` / `user-memory` *before* the process
  starts.

---

## Reasons to say no

Read these before the plan. Each is a real cost, and the first two are
individually sufficient to decline.

**1. The gig.** `rambass-live` has a date and `docs/plan.md` measures progress
against it. Every hour spent on repo surgery is an hour not spent rebuilding
drums. Nothing in this proposal makes a single song ready.

**2. Woodshed's own roadmap names this exact failure mode.**
`guitar-practice/docs/07-roadmap.md:83-85`, under *What would make this go
wrong*:

> **Practising the tool instead of the guitar.** The honest failure mode for a
> developer with a gig in a year.

A unification project is the most seductive available version of that. It feels
like architecture, it is enjoyable, it produces no music, and it can absorb an
unbounded amount of time. **If this is undertaken and step 4 is not reached by
the time the drums need rebuilding, it was the wrong call.**

**3. The dependency floors genuinely conflict, and the conflict is load-bearing.**
rambass's core is `pyyaml + mido + numpy` on `>=3.10` *deliberately*, so "a
laptop at a venue can run the core commands" (`CLAUDE.md`, "Don't"). gx100's
core is librosa + scipy + pandas + matplotlib + fastapi + rtmidi, pinned
`>=3.12,<3.13` because python-rtmidi has no cp313 Windows wheel. A single
installable app puts matplotlib on the gig laptop and pins everything to 3.12.
This is soluble with package boundaries — but it means "one app" in the literal
sense is off the table before anything else is discussed.

**4. Three repos is not obviously wrong for one maintainer.** Every repo is
under three weeks old. Three of the four duplication classes above are small
functions with good docstrings. It is entirely defensible to keep three repos,
fix the pedal fact by hand, and accept the copies as the cost of independence.
The honest counter to §4 is: *the duplication is 900 lines and the merge is a
week*.

**5. The docs are the asset and they are the migration cost.** ~18.3k lines of
measured, argued prose plus 1,184 lines of CLAUDE.md, most of it explaining why
a specific number is a specific number. That reasoning is the most valuable thing
in these repos and the easiest to damage in a reflow. Any plan that involves
"rationalising the documentation" should be rejected on sight.

**6. The concept collisions are a real trap** — see *What must not merge*. The
naive version of this project ("merge the similar concepts") would destroy
working code in at least two places.

---

## What must not merge

If unification goes ahead, this table is the guard rail. Several of these look
like obvious wins and are not.

| Looks shared | Actually | Why merging breaks it |
|---|---|---|
| `sections.py` (rambass, 553 lines) vs `sections.py` (Woodshed, 234 lines) | Same filename, incompatible models | rambass: bar-anchored, tiling, and *identical names are one part* — `quantize.consolidate` pools them and stamps them identically. Woodshed: second-anchored spans that deliberately overlap and nest, with lanes and containment derived. Both are correct. A merged abstraction is neither |
| bars vs seconds | Both invariants are right | rambass is bars-never-seconds because a tempo can change. Woodshed is seconds-never-bars because a commercial recording is immutable — and its CLAUDE.md already argues the exception explicitly, citing rambass's own `lyrics.srt` and `practice/align.yaml` carve-outs. Do not "harmonise" these |
| `song.yaml` | Three facets, one identity | Unify the *identity*. Never the facets — see the plan |
| `analyze.py` × 3 | Only `refine_tempo` is genuinely shared | gx100's `analysis/tempo.py` answers "is that delay a dotted eighth", which is a different question with a different confidence model |
| the drum / tone / practice pipelines | Unrelated | There is no shared abstraction here worth having. Do not look for one |
| the three front ends | Three instruments | rambass's review console is a purpose-built A/B drum-review tool. Woodshed's practice view is read from six feet away with a guitar on. Share tokens and components, never a shell |
| `CLAUDE.md` × 3 | Three sets of hard-won, app-specific rules | Merging them produces a document nobody reads. Shared rules move up; app rules stay put |

---

## The recommendation

**One repo, four packages, three apps. Not one app.**

```
guitar/                       one repo, one uv workspace
  packages/
    core/      toolchain location, audio io, demucs, tempo refine,
               click DSP, slugs, doctor framework, provenance, jobs
    device/    the ONLY code that talks to the GX-100:
               port lock, mode state machine, verified catalog
    web/       design tokens + waveform/ruler/playhead stack + job panel
  apps/
    rambass/     backing tracks, drums, video, Reaper     (gig deadline)
    gx100lab/    tone lab                                 (device-bound)
    woodshed/    practice                                 (youngest)
  songs/         ONE song library, three facet files per song
```

Three apps because they have different users-in-the-moment (a laptop at a venue,
a bench with a pedal on it, a couch with a guitar), different install floors and
different deadlines. One repo because the cross-repo lifts and the 66 path
references are already there, are already unverifiable, and become imports and
CI failures the moment the boundary goes away.

### The device package is the point of the exercise

`packages/device/` would own:

- **One port session, with a lock.** One holder at a time; the other two apps
  get *"the tone lab is holding the pedal"* instead of silent crosstalk. A
  lockfile plus a liveness check is enough — one human, one machine.
- **Mode as a state machine with an owner.** `require(PRACTICE)` from Woodshed,
  `require(REAMP)` from a sweep; declare what you need, restore on exit, record
  who set what. Newly buildable, since the USB-level write address was verified
  on 2026-09-06.
- **One program-change path.** `select_patch` (works from any screen) as the
  default; a bare `PC` for the DAW-on-play-screen case; `CC 0` / `CC 32`
  unreachable from anywhere.
- **One catalog.** `data/catalog/verified.yaml`, `amps.yaml`, the enum remaps.
  Today rambass has its own doc-derived model of the pedal and no route by which
  it could ever learn that the unit disagrees. That is the general fix for §1:
  **the pedal has exactly one model of itself, and it is the one fired at the
  hardware.**

### One song, three facets

```
songs/<slug>/
  song.yaml        identity, artist/album, key, tuning, tempo, recording + sha256
  live.yaml        rambass:  bars, count_in, drums, sections (bar-anchored), video
  tone.yaml        gx100:    patches[], structure, cues, voice
  practice.yaml    woodshed: sections (spans, seconds), ladder, targets
```

Each app owns its facet and reads the shared head. Woodshed's `section.patch` →
gx100 patch id becomes resolvable, and a slug typo becomes a test failure. The
pleasant thing that falls out is a single readiness view — backing track done,
patch assigned, section at 100% — which is only possible once identity is
shared.

### Jobs and progress

Promote `gx100lab/console/jobs.py` into `packages/core/` roughly as-is; it is
the developed one. Add job persistence across a server restart (demucs runs are
minutes) and one notification surface. The allowlist idea generalises: rambass
wants *"nothing may write to `render/`"*, Woodshed wants *"nothing sends a PC
without the toggle"*.

### UI

`packages/web/`, seeded from the three assets in §5: tokens from Woodshed, the
waveform stack from rambass, the job panel from gx100. Not one SPA — a
component kit and a set of JSON shapes. Leave gx100 on FastAPI initially and the
other two on the stdlib server; forcing that convergence now is cost without
benefit.

---

## Sequencing, if it goes ahead

Ordered by pain relieved per unit of risk, and shaped so rambass is never
blocked.

| # | Step | Size | Blocked by | Stands alone? |
|---|---|---|---|---|
| **0** | Strip `CC 0`/`CC 32` from `rambass gx100 midi`; delete the program-map indirection; test asserting no bank-select byte is ever emitted; correct `guitar-practice/docs/05-foot-control.md` and cross-link the gx100 finding from `rambass-live/docs/gx100.md` | ~1 hour | nothing | **Yes — do this regardless** |
| 1 | Merge the repos, move nothing. `git subtree` ×3 preserving history, one uv workspace, three packages that build and test exactly as now | ~half a day | 0 | Yes — reversible |
| 2 | `packages/device/`: move `gx100lab/device/` down, add the port lock and mode state machine, repoint rambass at it, delete `rambass/gx100.py`'s Roland model | ~2 days | 1 | Retires §1 and §2 permanently |
| 3 | `packages/core/`: `tools`/`audio`, `doctor`, `separate`, `tempofit`, `click`, `slugify`, `jobs` — in that order | ~2 days | 1 | Mostly deleting copies |
| 4 | Song identity: shared head + three facets | ~2 days | 1 | **Has a clock — see below** |
| 5 | `packages/web/`, then re-skin the consoles one at a time | open-ended | 1 | Pure polish |
| 6 | A `guitar` umbrella CLI; the unified readiness dashboard | open-ended | 4 | Only worth it after 4 |

Steps 1–3 hold nearly all the value. Steps 5–6 can wait indefinitely and should
be assumed to.

### The clock on part of this

Step 0 is urgent on its own merits and independent of everything else.

Step 4 has a deadline that is **not the gig**: it is the moment `gx100/songs/`
and `guitar-practice/songs/` get populated. Today both hold a template and
nothing else, so the shared-identity decision is free. Once 32 songs exist in
three places it becomes a migration with three sources of truth to reconcile.

Everything else can wait as long as it likes.

---

## What would change this recommendation

Written down now so the decision can be revisited honestly:

- **If the pedal stops being shared.** If Woodshed's Program Change support is
  dropped and rambass's patch-change MIDI is hand-checked once, §2 mostly
  evaporates and the case weakens a lot.
- **If Woodshed does not survive contact with use.** It is the youngest by three
  weeks. Two repos with one duplication class between them is not a project.
- **If the drums fall behind.** The gig is the deliverable. This is not.
- **If step 1 turns out to be more than half a day.** That is the canary. A
  history-preserving subtree merge of three clean repos on one machine should be
  boring. If it is not, the estimates below it are wrong too.

---

## Decision log

| date | who | decision |
|---|---|---|
| 2026-09-06 | Paolo | Question raised. Investigation done, this document written. **No decision taken.** |
