# Rebuilding a drum part to professional standard

**This is a Tutti in Fila document**, and it is the long version of
[drums.md](drums.md). Diversamente Giovani skips all of it — those songs are
`drums.origin: backing-track` and their drums already exist inside a finished
base.

[drums.md](drums.md) documents what `rambass drums transcribe` and `rambass
drums clean` actually do today. This document is about the job those two
commands are one part of: turning a stereo mix from 2005 into a drum track that
sounds like it was programmed on purpose, locks to a click, and holds up through
a PA at a gig.

The two are not in conflict. Everything here still ends at `rambass drums
clean`, still stores positions in bars, and still writes GM-mapped MIDI. What it
adds is the three stages the current pipeline has no answer for: getting a
detector that can see toms, deciding the grid before quantising to it, and
making each section internally consistent.

## The goal, stated precisely

Not "a transcription of what the drummer played". The target is:

* the **same arrangement** — same section lengths, same fills in the same bars,
  same crashes on the same downbeats, because that is what the band has in
  muscle memory;
* the **same kick pattern**, because that is what locks to the bass and the
  riff;
* **consistent** within a section — bar 11 and bar 13 of the same verse are
  identical unless the difference was deliberate;
* **on a fixed grid**, because the click has to be playable;
* **dynamic**, because a flat part sounds like a drum machine and the audience
  can tell;
* **loud and legible on a bad PA**, which is a mixing problem, not a MIDI
  problem.

Fidelity to the original hi-hat pattern is worth very little. Fidelity to the
kick pattern and the crash placement is worth almost everything. Budget your
attention accordingly.

## What this adds to the pipeline in drums.md

| stage | drums.md today | here |
|---|---|---|
| separate | demucs, drums vs. no_drums | plus a second split into five part stems |
| tempo | pick one BPM, or bend the grid | bend the grid, quantise, *then* flatten it |
| transcribe | 3 frequency bands over the drum mix | a detector per part stem, or a learned model |
| clean | de-flam, quantise, velocities, humanise | unchanged — this part is good |
| sections | markers in Reaper | consolidate each section to one pattern |
| fills | "fix them by hand" | replace them, don't repair them |
| kit | "load your drum VST" | which kit, and how it has to be mixed |
| render | render the base | plus a loudness target shared across the set |

Stages 2, 3 and 6 are the ones that change the result. The rest is detail.

## Stage 0 — get the right source file

From `songs/tutti-in-fila/sources.yaml`, download the **WAV**, not the MP3, into
the song's `source/` folder under the filename `source.audio` names. Separation
quality sets a hard ceiling on everything downstream and it is measurably worse
off a 2005 MP3. `rambass check` reports what is still missing.

Track 10 is the exception — it is MP3-only in Drive. Expect the worst result of
the album there and budget accordingly.

## Stage 1 — separate the kit out of the mix

```
rambass stems <song> --drums-only
```

`--drums-only` is two-stem mode: faster, and it also writes `no_drums.wav`,
which is the band-minus-drums bed. **Keep it.** It is the single most useful
file in the whole process and Stage 10 is built on it.

`--model htdemucs_ft` is the default and is worth its extra time on cymbals. On
Apple Silicon add `--device mps`. If the drum stem still sounds smeared —
cymbals swimming, kick hollow — a BS-Roformer-class model through MVSEP or
Moises will beat htdemucs_ft on drums specifically. That is a manual detour, not
something the CLI does.

## Stage 2 — split the kit into part stems

This stage does not exist in the repo yet and it is the highest-value addition
to the whole pipeline.

`rambass drums transcribe` runs onset detection in three fixed frequency bands
over the whole drum mix. [drums.md](drums.md) is honest about the consequences:
no band exists for toms, so a floor tom reads as a kick or a snare; ride and hat
are both short and high, so they are indistinguishable; anything under a ringing
crash is invisible. Those are not tuning problems, they are consequences of the
input being a mixture.

Give the detector one instrument per file and they mostly evaporate. Tools that
do it:

| tool | notes |
|---|---|
| [LarsNet](https://github.com/polimi-ispl/larsnet) | open source, local, faster than realtime; kick / snare / toms / hi-hat / cymbals |
| `drumsep` | demucs-based, same five-way split |
| Moises Pro | hosted, drum-part stems on the paid tier; easiest if you do not want a Python environment |

This is not a hunch. "Enhanced Automatic Drum Transcription via Drum Stem Source
Separation" ([arXiv 2509.24853](https://arxiv.org/html/2509.24853)) measures
exactly this pipeline and reports 8-class F-measure of 0.84 against a 0.72
baseline on MDB, and 0.76 against 0.65 on ENST.

Two things worth noticing in that paper, because they say something about this
repo: its cymbal handling is a refraction period after a crash, and its
open-versus-closed hat test is loudness decay over a ~150 ms window. Those are
`CRASH_SUSTAIN_RATIO` and `OPEN_HAT_SUSTAIN_RATIO` in `transcribe.py`. The
heuristics here are the right ones. They are just pointed at a mixture instead
of a stem.

Until there is a `rambass` stage for this, run the separator by hand and feed
each stem in with `rambass drums transcribe --file <stem>`, then merge. Tune the
band for each: a kick stem wants the kick band and nothing else.

## Stage 3 — tempo: follow the take, then flatten it

[workflow.md](workflow.md) presents this as a choice — bend the grid with a
tempo map to keep the feel, or fix one BPM and accept that the original audio
slides out of sync as a reference. For a gig the second is obviously right, and
`--strength 1.0` is the correct quantise setting.

But the choice is a false one, and the repo's own invariant is what dissolves
it. Positions are stored in **bars**, so a grid can be changed after the fact
without moving anything musically. That means:

1. **Follow.** Build a tempo map from the beat times `rambass analyze` detects,
   so the grid bends with the take.
2. **Quantise against the bending grid.** Every hit is now within a few
   milliseconds of a line, so quantisation is unambiguous — a hit lands in the
   slot it was played in, not the neighbouring one the band drifted into.
3. **Flatten.** Discard the tempo map and set one fixed BPM. Because positions
   are musical, everything re-renders perfectly straight.

Quantising straight to a fixed grid, without step 1, silently mis-slots hits
wherever the take has drifted more than half a subdivision — and a three-minute
take from 2005 will have. That failure does not show up in the report as a large
mean move, because each individual hit was snapped to *something* nearby. It
shows up as a groove that is subtly wrong in the second half of the song.

`rambass drums import --retempo` already performs step 3's motion. What is
missing is a command to write a tempo map from detected beat times — until then,
enter the tempo changes in `song.yaml` by hand from the `analyze` output, which
prints a BPM per 20-second window.

**Side benefit worth the effort on its own:** that same bending map, used in a
throwaway Reaper project, lets you time-warp the drum stem onto the straight
grid. Without it, the reference audio and the MIDI grid disagree more and more
as the song goes on, and hand-editing fills past the first thirty seconds
becomes guesswork.

## Stage 4 — transcribe

In order of preference:

* **Superior Drummer 3 Tracker**, if SD3 is available. It analyses audio to MIDI
  natively, reads dynamics into velocity, and detects ghost notes. One box, and
  it removes Stages 2 and 4 both.
* **[ADTOF](https://github.com/MZehren/ADTOF)** — a learned drum transcriber
  trained on real rock, MIDI out with velocity. `ADTOF-pytorch` needs only
  torch, no TensorFlow or madmom.
* **`rambass drums transcribe`**, run per part stem from Stage 2.

**EZdrummer 2 and 3 cannot do this.** EZdrummer 3's Bandmate matches *library
grooves* to an audio file you drop in; it does not transcribe what is in the
file. That is a genuinely useful feature — see the alternative at the end — but
it is not audio-to-MIDI and it will not give you the band's part.

Check the hit counts in `--report` before going near the DAW. The rule from
[drums.md](drums.md) stands: 704 hats in a three-minute song is plausible, 4000
is broken detection.

## Stage 5 — clean

```
rambass drums clean <song> --strength 1.0 --deflam-ms 25
```

This part of the pipeline is good and does not need changing. Two cautions.

**Do not use `--accent-kick` / `--accent-snare` as a matter of course.** They
replace every hit's velocity with one fixed number.
[drums.md](drums.md) justifies that because band flux over a drum *mixture* is
only loosely related to how hard the drummer hit — which is true. But onset
energy measured on an isolated snare stem is a decent proxy, and after Stage 2
you have one. Flattening is a workaround for a bad measurement; once the
measurement is good, flattening throws away the ghost notes, the hat accent
pattern and the push into the chorus. Those are the dynamics you are trying to
keep. Normalise per instrument, shape the curve, but keep the contour.

**`--humanize` is solving a smaller problem than it looks.** ±4 ms of timing
jitter is close to inaudible. What actually makes a sampled kit sound fake is:

* **identical velocities**, because velocity is what selects EZdrummer's
  round-robins and layers — repeat the same number and you re-trigger the same
  sample, which is the machine-gun effect people blame on quantising;
* **wrong articulations** — everything on a closed hat, the hat never opening
  under a crash, no pedal hat;
* **physically impossible parts** — two toms 30 ms apart that need three hands,
  hats continuing through a crash on the same arm. Nothing in the repo detects
  this and a listener catches it immediately.

Velocity spread is the humaniser that matters. Check limb plausibility by eye in
the MIDI editor.

## Stage 6 — sections, and consolidating each one to a pattern

This is the stage that decides whether the result sounds programmed or
transcribed, and the repo has nothing for it. `sections` in `song.yaml` are
markers only — `reaper.py` turns them into Reaper markers and regions, and
nothing else reads them.

Without this stage, two things leak through into the final track. Every
unintended inconsistency in the original take survives quantising, *and* every
transcription error is independent per bar. So bar 11 of the verse loses a hat
and bar 13 gains a phantom tom. On the grid, but incoherent — which is precisely
the texture that reads as a bad transcription.

Mark the sections first:

```
rambass section <song> <bar> <name>
```

(`<song> <bar> <name>`, in that order — there is no `add` subcommand.) Then for
each section:

1. Find the repeating unit — one bar or two.
2. Overlay every repetition of that unit in the section.
3. Keep a hit only where it appears in **at least 50–60%** of the repetitions,
   placed at the modal grid slot, with the median velocity of the group.
4. Stamp the result across the whole section.

What that threshold throws away is exactly the two things you do not want:
unintended variation from the drummer, and transcription errors — neither of
which repeats reliably. What survives is the groove the section is actually
made of.

Then put back, by hand, the events that were deliberate: the crash on the
section's first downbeat, the kick variation in the last bar before the chorus,
the fill at the end of each four- or eight-bar phrase. Those are the things a
listener notices, and they should be there because you decided so, not because
a detector happened to catch them.

No off-the-shelf tool does this. Options today: copy the best bar and paste it
over the section in Reaper's MIDI editor, or build the section in EZdrummer 3's
Grid Editor and Song Creator. It is also the most obviously repo-shaped thing in
this document — a pure function over a `DrumPerformance` plus a report dict,
exactly like everything else in `quantize.py`.

## Stage 7 — fills, crashes, articulation

**Do not try to repair transcribed fills.** A three-to-five minute song has
maybe eight to fifteen of them; dense, fast and overlapping is the one case the
detector is worst at, and salvaging its output takes longer than starting over
and sounds worse.

Instead: note which bar each fill is in, then either play it in on pads, draw it
in EZdrummer 3's Grid Editor, or pull a matching fill out of the library
browser. Twenty minutes for the song, and it sounds like a session player.

While you are there, fix the articulations the detector cannot see: open the hat
where the part opens up, put the ride where the ride is, add the pedal hat.

## Stage 8 — the kit, and the mix

**Check the kit against the song's `character` block.** Tutti in Fila is tagged
`genre: metal, energy: 5, heaviness: 5`. EZdrummer 3's core library is a
pop/rock kit and will vanish under distorted guitars. Budget for a heavy EZX —
Metal Machine, Number of the Beast, Rock Solid. The sample library matters more
to "sounds legit at the gig" than any of the MIDI work above.

**Build a real drum map.** There is no EZdrummer entry in `config/drum-maps/`.
Toontrack's GM Extended map shares its *core* notes with GM, but the extended
articulations do not match, and EZdrummer's core kits usually carry three toms
where `drummap.py` emits five names — `tom_low_mid` (43) will hit the wrong
thing or nothing at all, silently. Read the numbers off EZdrummer's own
Interactive MIDI Layout document (Help menu) into
`config/drum-maps/ezdrummer3.yaml`, the same discipline the `bfd3.yaml` comment
argues for. Then:

```
rambass drums remap <song> ezdrummer3 --set-default
```

Send a single kick and watch which pad lights up before trusting a whole song.
Turn EZdrummer's own quantise and humanise **off** — they will fight the
quantising already in the MIDI.

**Mix it for a stage, not for headphones.** Enable Multi-Out in EZdrummer's
Settings → General; Reaper will create the aux tracks for you, and EZdrummer 3
offers up to 16 stereo outputs. Then:

* cymbals **down** — well below where an album mix would put them. There is no
  drummer on stage making them make sense, and they mask everything through a
  cheap PA;
* kick with an audible beater click, or it disappears in a wedge;
* snare cutting around 200 Hz and 4 kHz;
* **check the whole thing in mono.** Wide stereo overheads and room mics
  partially cancel on a mono PA, which is what a lot of small venues run. This
  is the failure that only shows up at soundcheck.

## Stage 9 — render

Render **DRUMS MIDI alone**, from the `BAR 1` marker, to
`songs/<album>/<slug>/render/<slug>.wav`.

Two traps:

* **Not from 0:00.** `reaper.py` places the base at `timeline.count_in_seconds`,
  so the rendered file must not contain the count-in — otherwise it is offset by
  the count-in twice. If the drum part has a pickup before bar 1 it falls inside
  the count-in and gets cut; decide that deliberately rather than discovering it.
* **Never with the click.** `click.py` opens by saying a click baked into a base
  is unremovable, and it is right. Note that
  [reaper.md](reaper.md) currently contradicts itself here: line 74 says to
  render CLICK + DRUMS MIDI together, line 81 says the click is never mixed into
  the base. Line 81 is correct. Line 74 needs fixing.

Then match loudness across the set. `audio.py` has `measure_loudness` (EBU R128
integrated plus true peak) and nothing calls it. Every backing track in the
running order should land at the same integrated LUFS with the same true-peak
ceiling — −1 dBTP is a safe one — or FOH is riding gain between songs all night.

## Stage 10 — QA against the band

Play the programmed drums against `stems/no_drums.wav`.

This is what that file is for and it is worth more than any amount of soloed
listening. Missing crashes, wrong fills, a dropped kick variation and a section
that changed one bar early are all inaudible in solo and glaring against the
rest of the band. Do it before every commit of the MIDI, and once more at final
tempo after Stage 3's flatten.

## The faster alternative: map it, don't transcribe it

Worth considering seriously, because it may be both quicker and better.

The transcription's real value is the **arrangement map**, not the note data.
Fidelity matters for two things: the kick pattern, because it locks with the
bass and the riff, and the crash and section placement, because that is the
arrangement everyone has memorised. For hats, ride, ghost notes and fills, a
professionally played library groove in the right style — stamped consistently
on a fixed grid — will beat a cleaned-up transcription of a take that was never
played to a click.

So: run Stages 0–5 far enough to extract the map (section boundaries in bars,
the kick pattern, where every crash and fill lands), then build the part in
EZdrummer 3 against that map using Bandmate, the groove browser and Song
Creator.

What you get is "same song, same arrangement, same hits in the same places,
played better", which is what a gig needs. Nobody in the audience will notice a
different ride pattern. Everybody notices a fill that lands in the wrong bar.

## Across the album

The scope is smaller than "every Tutti in Fila song". Eleven tracks, but:

* **02 I Puffi** already has a finished base, a Reaper project and a lyric video
  — `drums_origin: backing-track` in `existing-work.yaml`. Skip it.
* **07 La Canzone Del Tonno** is `drums.origin: a-cappella`. It needs a title
  card and nothing else. That is a finished state, not a to-do.
* **01 Intro Portamistica Z.Z.I.** and **10 La Canzone Del Solero** are
  `excluded.from_set: true` — cut. Do not spend a Saturday on the MP3-only one
  for a song that is not in the running order.

Which leaves **six songs and five backing tracks**:

| song | genre | e/h | notes |
|---|---|---|---|
| 03 Tutti In Fila | metal | 5/5 | hardest part, set position 3 |
| 04 Ampiamente Contestabile | rock, fun | 4/2 | |
| 05 Intro Vibratore | linking | 2/1 | **one track with 06** |
| 06 La Vera Storia Del Vibratore | rock / ambient, offbeat | 3/3 | **one track with 05** |
| 08 Skizzo Sonovabic | jazzy rock, epic | 3/3 | longest — most Stage 6 work |
| 09 Manlio | ballad | 1/1 | sparsest part |
| 11 L'Esercito Del Surf | pop / light rock | 3/1 | |

`gig.yaml` is explicit that 05 and 06 run together as one backing track and must
not be separated. They are two `song.yaml` files with two tempi and two
timelines, so decide early how that is modelled — one continuous click across
both, or a join that happens only in the show project.

### What is reusable, and what is not

Reusable once, across all five: the EZdrummer drum map
(`config/drum-maps/ezdrummer3.yaml`), the Reaper template with multi-out routing
and the bus chain, the loudness target, and the Stage 6 method.

**Not reusable: the kit.** That genre column spans metal, jazzy rock, a ballad
and light pop. One EZX across all of them will be wrong four times out of five —
a metal kit on Manlio sounds absurd, and the core pop/rock kit on Tutti In Fila
disappears. Expect roughly three kit choices: something heavy for 03, something
neutral and dry for 04/11, something soft or brushed for 09, and 08 is the
judgement call. Auditioning a kit per song costs ten minutes and is the highest
return per minute in this whole document.

Also not reusable: tempo, sections, and every velocity decision. Those are the
work.

### Order to do them in

Shake the toolchain down on **09 Manlio** first. It is a sparse ballad at energy
1: fewest hits, errors are obvious, iterations are fast, and you will discover
every rough edge in the separation-to-Reaper path on a song where mistakes are
cheap. Then do **03 Tutti In Fila** while patience is highest — it is the
hardest part on the album and it sits at position 3 in the set. Then 04, 11, 06
with 05, and 08 last, since it is the longest and by then the method is routine.

Most `rambass` commands take `--album tutti-in-fila` or `--all` instead of a
song, which is useful for the deterministic stages — `analyze`, `stems`,
`transcribe`. Do not batch `drums clean`: the quantise settings and the tempo
decision are per-song, and running one set of flags across six songs is how you
get five songs quantised against the wrong grid.

## Budget

Roughly **3–6 hours** for one song done properly, of which about one hour is
unattended compute. Most of the human time is Stages 6 and 7. The first song
costs more while the kit, the drum map and the mix template are established;
after that they are reusable across the album.

For the album that is a realistic **two to four full days** of work for the five
backing tracks — front-loaded, because Manlio and Tutti In Fila between them
will establish almost everything the other three reuse.

## Gaps in the repo this document assumes away

Ranked by value, none of them implemented:

1. **A drum sub-separation stage** between `stems` and `drums transcribe` —
   Stage 2. Everything else downstream improves for free.
2. **`rambass drums consolidate`** — the section pattern vote in Stage 6. Pure
   function, fits `quantize.py`'s style exactly.
3. **A tempo map written from detected beat times**, to make Stage 3's
   follow-then-flatten a command rather than hand-entered YAML.
4. **`config/drum-maps/ezdrummer3.yaml`**, read off the plugin.
5. **A set-level loudness match** using the `measure_loudness` already in
   `audio.py`.
6. **The [reaper.md](reaper.md) line 74 contradiction**, which as written bakes
   a click into the base.
