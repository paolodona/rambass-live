# Reaper

## Why there is no .RPP generator

Writing `.RPP` files by hand is a trap: the format shifts between versions and
fails in ways that are hard to debug from the outside. Instead `rambass reaper
build` emits a small tab-separated **build script** (`.rbs`) and a ReaScript
executes it through Reaper's own API — so the project is always a valid Reaper
project, and the Lua is short enough to adjust on gig day.

## Building a song project

```
rambass reaper build 04-titolo
```

writes `reaper/build/<album>-<slug>.rbs`. Then, in Reaper:

1. **File > New Project**
2. **Actions > Show action list…**
3. **ReaScript: Load…** → `reaper/scripts/rambass_build_song.lua`
4. **Run**, and pick the `.rbs` file

It only ever *adds* to the current project, so running it on a fresh project is
safe; running it twice gives you everything twice.

You get:

| track | contents |
|---|---|
| STICKS | `render/sticks.wav` — the drumstick count-in, at 0. Unmuted; may go to the PA |
| BACKING | the finished base, starting after the count-in |
| CLICK | `render/click.wav` — the song click. **Muted on build**; rehearsal and overdubs only |
| DRUMS MIDI | the quantised drum MIDI — put your drum VST here |
| REF drums / bass / other / vocals | the demucs stems, muted reference |
| REF mix | the original mix, muted reference |
| GX-100 MIDI | the patch-change MIDI — route its output to the pedalboard |

plus the tempo map, a marker and a region per section, a `BAR 1` marker, and a
marker at every patch change.

## Where bar 1 is

The tempo starts at **time zero**, not at bar 1, so the count-in is in tempo
too. That means:

* Reaper bar 1 = the first count-in bar
* musical bar 1 = Reaper bar (`count_in.bars` + 1) — bar 3 with the default
  two-bar count-in

The `BAR 1` marker is there so you never have to do that arithmetic. Everything
`rambass` generates already agrees on this convention; if you move items by hand,
keep them lined up with that marker.

## Save it as your own project

The `.rbs` and the generated project are disposable — `reaper/build/` and
`songs/**/reaper/` are gitignored. Once built, **Save As** into the song folder
and work there. Regenerating is for when the tempo or the arrangement changes,
and then you rebuild rather than patching the old project.

## Setting up the drum VST

Load your kit on **DRUMS MIDI**. Whatever it is, check that:

* it is receiving on the channel your drum map declares (10 for GM);
* its own mapping matches `config/drum-maps/<map>.yaml` — send a single kick and
  watch which pad lights up before you trust a whole song;
* the kit's own quantise/humanise features are **off**, or they will fight the
  quantising already done in the MIDI.

## Rendering the backing track

Render the CLICK + DRUMS MIDI tracks (and nothing else) to
`songs/<album>/<slug>/render/<slug>.wav`. That filename is what `rambass reaper
setlist` looks for when assembling the whole-show project, and what `rambass
video render --with-audio` muxes in.

The count-in and the click are two separate stems and are never mixed into the
base — see [live-playback.md](live-playback.md#count-in-and-click-two-stems-never-in-the-base).
Render the backing track from BACKING alone; STICKS and CLICK stay as their own
files so they can be muted independently.

## Whole-show project

Per-song projects are for *making* the songs; this one is for *playing* them, and
the two never mix — see
[live-playback.md](live-playback.md#two-kinds-of-reaper-session-and-the-line-between-them)
for the split, the five files each song must produce, and the anti-stutter
checklist.

```
rambass reaper setlist gig
```

One project, one region per song in running order, each rendered backing track
placed on a single `BACKING` track. This is the project you actually open at the
gig.

## Live transport control

`reaper/scripts/rambass_transport.lua` gives three actions:

| ACTION | what it does |
|---|---|
| `play_next` | stop, jump to the start of the next region, play |
| `play_again` | back to the start of the current region, play |
| `stop_panic` | stop and send all-notes-off everywhere |

Reaper identifies a script by its filename, so **save one copy per action** —
`rambass_next.lua`, `rambass_again.lua`, `rambass_panic.lua` — and edit the
`ACTION` line at the top of each. Load each as its own action, then bind it:
**Actions > Show action list**, select the script, and either **Add…** under
Shortcuts or use the **MIDI learn** button and step on the footswitch.

Because the GX-100 transmits program change and CC on its TX channel (see
docs/gx100.md), the pedalboard itself can drive these — no extra footswitch
needed. Decide in advance whether the pedal is the master (it drives Reaper) or
the slave (Reaper drives it); doing both at once is how you get feedback loops
and mysterious patch changes mid-song.

## Before the gig

* Reaper: **Options > Preferences > Audio > Close audio device when stopped** →
  **off**. A device that re-opens between songs will eventually not re-open.
* Turn off every autosave, backup and "check for updates" prompt.
* Test the whole set once, start to finish, on the actual gig laptop, with the
  actual interface, on battery.
