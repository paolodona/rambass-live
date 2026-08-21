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

### Effort tiers

| tier | songs | state | cost each |
|---|---:|---|---|
| **A** | 7 | base **and** lyric video already exist | an hour — fix the punch-list |
| **B** | 7 | tempo + lyrics text known, base must be mixed | a mixing session |
| **C** | 8 | Tutti in Fila — nothing but an MP3 | a day |
| **D** | 4 | intros/outros — play as album audio | minutes, or cut them |

**Tier A is a playable 35-minute set on its own.** That matters: it means there
is a working show early, and everything after it is expansion rather than
prerequisite.

<details>
<summary>Which songs are in which tier</summary>

**A** — I Poohffi (TIF 02), ForMayGrana, Mother Sacher, Itturfiatrugoy,
Il Phurgone, La Ragazza da Milano, Diversamente Giovani

**B** — Bambolina, Orologiaio, Il Cellulare, Per Niente Stanca, Superman,
Se Sei Felice, Mandami un Faxe

**C** — Tutti In Fila, Ampiamente Contestabile, La Vera Storia Del Vibratore,
La Canzone Del Tonno, Skizzo Sonovabic, Manlio, La Canzone Del Solero,
L'Esercito Del Surf

**D** — DG INTRO, DG OUTRO, TIF Intro Portamistica Z.Z.I., TIF Intro Vibratore
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

### Gate 0 ✅ when
- [ ] `rambass setlist gig` lists the songs and a total running time the band agrees to
- [ ] every listed song resolves (the command errors if one does not)
- [ ] the intros/outros are explicitly in or out
- [ ] someone has said out loud how long the set should be

**Recommendation:** commit to Tier A + Tier B as the target set (14 songs, ~70
minutes) and treat Tier C as a stretch list ordered by which songs the band most
wants. Then a cut later costs a mixing session, not a week.

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
4. **Render.** `rambass video render <song>`.

**Gate B ✅ when**
- [ ] `rambass lyrics check <song>` is clean
- [ ] the cues match the house style in [lyrics.md](lyrics.md)
- [ ] the video plays in sync against the **final** base, watched end to end once
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

**Gate 2 ✅ when**
- [ ] one Reaper session, one region per song, in running order
- [ ] no plugin instances anywhere in the project
- [ ] two footswitches work: next song, stop
- [ ] MIDI reaches the pedal and it changes patch
- [ ] the video window is on the projector output and shows the right title card
      when parked between songs

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

## Progress at a glance

| | Phase 0 | Lane A | Lane B | Lane C | Phase 2 | Phase 3 |
|---|---|---|---|---|---|---|
| what it means | set agreed | base final | cues + video | patches | one session | played through |
| measured by | `rambass setlist` | `rnd` | `lyr` `vid` | `gx` | manual | `reh` |
| parallel? | no — do it first | per song | per song | per song | no | no |

Run `rambass status` for the live numbers. As of the last update: 26 songs
scaffolded, tempos known for 13, lyric cues done for 6, videos done for 6,
backing tracks existing for 7.
