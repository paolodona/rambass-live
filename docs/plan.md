# Plan

The measurement instrument is `rambass status`. Every gate below is expressed as
a command that either passes or does not, so "how far along are we" is never a
matter of opinion.

```
rambass status                     # the board: one row per song, one column per stage
rambass status --markdown          # paste into the band chat
rambass check                      # are the manifests and source files sane
rambass lyrics check --all         # are the cue files sane
```

---

## The shape of the work

Three facts set the whole plan:

1. **Songs are independent.** Everything except the final assembly is per-song,
   so any number of people can work in parallel, one song each.
2. **Within a song there are three independent lanes** — backing track, lyrics,
   pedal changes — which only converge at the end.
3. **The two albums cost wildly different amounts.** Diversamente Giovani is
   mostly done; Tutti in Fila is the real work. Do not average them in your head.

### The set is decided: 23 songs

**Cut (3)** — recorded as `excluded` in their own `song.yaml`, so they drop off
the board and count for nothing:

* Tutti in Fila 01 — Intro Portamistica Z.Z.I.
* Tutti in Fila 10 — La Canzone Del Solero *(also the only track with no WAV master)*
* Diversamente Giovani 15 — OUTRO

**A cappella (2)** — in the running order, and the only thing to produce is a
title card for the screen. No backing track, no click, no count-in, no pedal
change. `drums.origin: a-cappella` leaves only `source` and `rehearsed`
applicable on the board, and `rambass reaper setlist` asks them for nothing but
the card:

* Tutti in Fila 07 — La Canzone Del Tonno
* Diversamente Giovani 11 — Se Sei Felice

### Effort tiers

| tier | songs | state | cost each |
|---|---:|---|---|
| **A** | 7 | base **and** lyric video already exist | an hour — fix the punch-list |
| **B** | 6 | tempo + lyrics text known, base must be mixed | a mixing session |
| **C** | 6 | Tutti in Fila — nothing but an MP3 | **a day** |
| **D** | 2 | linking pieces — play as album audio | minutes |
| a cappella | 2 | nothing to build but a title card | `rambass video card` |

**The whole remaining cost of the project is Tier C: six days of work.**
Everything else is hours or a mixing session. Cutting Solero and making Tonno
a cappella took Tier C from eight songs to six — a quarter off the expensive
part of the project, for two decisions.

**Tier A is a playable 35-minute set on its own,** so there is a working show
early and everything after it is expansion rather than prerequisite.

<details>
<summary>Which songs are in which tier</summary>

**A** — I Pooh (TIF 02), ForMayGrana, Mother Sacher, Itturfiatrugoy,
Il Phurgone, La Ragazza da Milano, Diversamente Giovani

**B** — Bambolina, Orologiaio, Il Cellulare, Per Niente Stanca, Superman,
Mandami un Faxe — but see the question raised in
[practice-tracks.md](practice-tracks.md#diversamente-giovani--separate-two-stems-warp-nothing):
if the previous gig had usable backing tracks for *these* songs too, some of
Tier B may already be done.

**C** — Tutti In Fila, Ampiamente Contestabile, La Vera Storia Del Vibratore,
Skizzo Sonovabic, Manlio, L'Esercito Del Surf

**D** — DG INTRO, TIF Intro Vibratore *(runs into La Vera Storia Del Vibratore —
treat the two as one backing track)*
</details>

---

## Phase 0 — Decide the set

**This is the only gate that blocks everything, and it is a band decision, not a
technical one.** Every task downstream is per-song, so a song cut after its
lyrics are timed is a day thrown away — the Tier C songs especially.

Edit `setlists/gig.yaml`, then:

```
rambass setlist gig
```

### Gate 0 ✅ — passed

`setlists/gig.yaml` holds the agreed 23. Cuts and a cappella calls are recorded
in the songs themselves, so the board and the plan cannot drift from the decision:

```
rambass scope <song> out --reason "..."      # cut a song
rambass accompaniment <song> a-cappella      # in the set, nothing to build
rambass arc gig                              # check the running order
```

The running order itself is settled and passes clean — see
[setlist.md](setlist.md) for the principles it was built on and why each
decision was made. `setlists/tier-a.yaml` is the seven-song set playable now.

Still open, and worth settling before Lane B starts on the Tier C songs:

- [x] **Do the two a cappella numbers want anything on screen?** Yes, a static
      title card — their words cannot be auto-synced, because there is no backing
      track to time against. `rambass video card <song>` renders it, and the show
      project holds it for the whole region. That is the *finished* answer for
      those two songs, not a placeholder: a card is the only artifact they need,
      and `rambass reaper setlist` counts them complete once it exists.
- [ ] **Do they want a stick count-in, or a starting pitch?** `rambass countin`
      works on them already — it needs no base — and a count-in for an
      unaccompanied entry is arguably more useful than for a song with a track.
      A pitch reference would be new.

---

## Phase 1 — Per-song production · fully parallel

One song per person. Within a song, the three lanes below can also run in
parallel, with one ordering constraint: **Lane A must reach Gate A before Lane B
step 3 and Lane C step 2**, because both need the final timing of the backing
track.

```
        ┌─ Lane A: backing track ──┐
song ───┼─ Lane B: lyrics ─────────┼──> song ready
        └─ Lane C: pedal changes ──┘
             (B3 and C2 wait for Gate A)
```

### Lane A — backing track → `render`

| tier | what to do |
|---|---|
| A | `rambass countin <song>` for the four missing a count-in, plus `rambass click`; remix the rest of the punch-list in the album session |
| B | mix a BASE in the album session — album mix minus what the band plays live — then `rambass countin` and `rambass click` |
| C | `rambass analyze --write` → `rambass stems --drums-only` → `rambass drums transcribe` → `rambass drums clean` → voice the kit → render |
| D | none; use the album audio as-is |

**Gate A ✅ when**
- [ ] a single stereo WAV exists in `render/`, at the gig sample rate
- [ ] `render/sticks.wav` exists — the drumstick count-in, as its own stem
- [ ] `render/click.wav` exists — the song click, as its own stem
- [ ] **no click anywhere in the base itself**
- [ ] loudness is matched across songs (nothing jumps between tracks)
- [ ] `rambass status` shows `rnd` as `X`
- [ ] **the filename is frozen** — Lanes B and C are timed against this file

### Lane B — lyrics → `lyrics`, then `video`

1. **Words.** 10 DG songs have a Google Doc with the full text (ids in
   `existing-work.yaml`). Tutti in Fila has none — someone transcribes by ear.
   Paste into `lyrics.txt`.
2. **Timing.** `rambass lyrics transcribe <song> --prompt-file lyrics.txt`, then
   fix every line by hand in a subtitle editor. Six DG songs skip this entirely —
   `rambass lyrics import` and they are done.
3. **Align to the final base** (needs Gate A): `rambass lyrics shift <song> <s>`.
   The existing SRTs were timed against the album master, and the base starts
   somewhere else.
4. **Render.** `rambass video render <song>` — which also puts the title card on
   screen through the count-in. A song whose words are not written yet still gets
   something on the screen: `rambass video card <song>`.

**Gate B ✅ when**
- [ ] `rambass lyrics check <song>` is clean
- [ ] the cues match the house style in [lyrics.md](lyrics.md)
- [ ] the video plays in sync against the **final** base, watched end to end once
- [ ] the first frame is the title card, not black
- [ ] `rambass status` shows `lyr` and `vid` as `X`

### Lane C — pedal changes → `gx100`

1. Decide which memory each section wants — the band uses four
   (Clean Acustic / Lead / WAH / Clean Clean).
2. Find the bars (needs Gate A): `rambass reaper import` recovers them for the
   seven songs that already have a project; otherwise mark them by ear.
3. `rambass patch <song> <bar> <memory>` then `rambass gx100 midi <song>`.

**Gate C ✅ when**
- [ ] every change is in `song.yaml` and `midi/gx100.mid` exists
- [ ] `rambass gx100 sheet --setlist gig` lists them all
- [ ] **one change has been tested against the real pedal** — a program change
      names a *slot*, not a memory, so this is the step that catches a wrong
      PROGRAM MAP (see [gx100.md](gx100.md))

---

## Phase 2 — Assemble the show · one person, needs all of Phase 1

```
rambass reaper setlist gig
```

See [live-playback.md](live-playback.md) for the architecture and why it is one
session rather than one per song.

You work in per-song sessions throughout production and assemble this one at the
end — see [live-playback.md](live-playback.md#two-kinds-of-reaper-session-and-the-line-between-them)
for why that split is forced rather than chosen, and for the files each song
has to produce. **Build the show session early and rebuild it often;** it works
with half the songs missing, and reordering the set never touches per-song work.

**Gate 2 ✅ when**
- [ ] one Reaper session, one region per song, in running order
- [ ] every region carries its count-in, backing track, pedal MIDI and one video
      item — `rambass reaper setlist` lists what is still missing per song, and
      the two a cappella numbers need only their card
- [ ] no region length is still a guess from a bar count
- [ ] no plugin instances anywhere in the project
- [ ] two footswitches work: next song, stop
- [ ] MIDI reaches the pedal and it changes patch
- [ ] the video window is on the projector output and shows the right title card
      when parked between songs — every song has one, either as its video's
      lead-in or as a still

---

## Phase 3 — Dry run · the gate everyone skips

**Gate 3 ✅ when**
- [ ] the **whole set** played start to finish, on the gig laptop, with the gig
      interface, with the projector connected — in one sitting, without touching
      the keyboard
- [ ] Reaper's performance meter watched throughout; no disk or CPU spikes
- [ ] done twice: once on mains, once on battery
- [ ] the band has played to the click and says the count-ins work
- [ ] `rambass status` shows `reh` as `X` for every song in the set

Anything found here is cheap. The same thing found at the gig is not.

---

## Phase 4 — Gig day

[live-runbook.md](live-runbook.md). The short version: printed running order,
printed patch sheet, the WAVs on a second device, and no updates installed that
week.

---

---

## Side goal — practice tracks · never blocks anything

Per-member practice MP3s (drums + everything but your own part), plus the
original mix with the rebuilt drums swapped in. Scoped in full in
[practice-tracks.md](practice-tracks.md).

It is deliberately outside the gates above: practice stages are **not** on the
status board, no practice command writes to `render/` or edits a musical field in
`song.yaml`, and a practice track is allowed to be imperfect where a base is not.

Two things about it are worth knowing while planning Phase 1:

* **Minus-one tracks need nothing from Lane A**, and nothing from the alignment
  problem either: built with each album's own drums they are a plain re-sum of
  separated stems. Diversamente Giovani needs only `vocals` and `bass`
  separated from the masters — Vikingo practises against the previous gig's
  backing tracks — so that album is an afternoon's work, not a phase.
* **The drums-in-the-mix track is a Lane A QA instrument**, not just a rehearsal
  aid — it is drums-rebuild.md Stage 10 done properly. Run it on a Tier C song
  *before* Gate A freezes that song's base filename, because that is when the
  drum problems it finds are still cheap.

## Progress at a glance

| | Phase 0 | Lane A | Lane B | Lane C | Phase 2 | Phase 3 |
|---|---|---|---|---|---|---|
| what it means | set agreed | base final | cues + video | patches | one session | played through |
| measured by | `rambass setlist` | `rnd` | `lyr` `vid` | `gx` | manual | `reh` |
| parallel? | no — do it first | per song | per song | per song | no | no |

Run `rambass status` for the live numbers. As of the last update: 23 songs in
the set (3 cut, 2 a cappella), tempos known for 14, lyric cues done for 6,
videos done for 6, backing tracks existing for 7.
