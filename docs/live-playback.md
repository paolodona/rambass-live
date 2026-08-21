# Live playback

Four things have to happen together on stage: the backing track plays, the lyric
video runs, the GX-100 changes patch, and the band gets a count-in. The guitarist
runs all of it while playing. So the design goal is not features — it is **the
fewest possible actions per song, and nothing computed in real time.**

## Recommendation: one Reaper session for the whole set

The 2023 approach was one project per song, and the problem you hit — loading a
different project between songs — is the thing a single session removes
completely. Everything is open, on one timeline, before the first note.

```
TRACK  BACKING     one pre-rendered stereo WAV per song, count-in included
TRACK  GX-100      short MIDI items: bank select + program change, to the pedal
TRACK  VIDEO       title card, then the lyric video, per song
REGION 01 … 14     one per song, in running order
```

`rambass reaper setlist gig` generates this. Your existing per-song projects
already have exactly these tracks (`BASE`, `Program change`, `Cover`,
`Video - NO AUDIO`), so consolidating is a merge, not a rewrite.

### What the guitarist actually does

**Two footswitches. One press per song.**

| switch | action |
|---|---|
| 1 | **next song** — stop, jump to the next region, play |
| 2 | **stop / panic** — stop and send all-notes-off |

Optionally a third for **restart current song**, which is the one thing you will
want and not have when a song starts wrong.

`reaper/scripts/rambass_transport.lua` provides all three. Reaper identifies a
script by filename, so save one copy per action and edit the `ACTION` line at
the top of each, then MIDI-learn each to a switch.

Between songs the transport is parked at the next region's start, so the video
window shows that song's title card while the band talks. That is why the title
card sits at the head of each region rather than in a gap.

### Where the switches come from

A plain USB or MIDI footswitch is the boring, correct answer: it is one job and
it cannot interact with anything else.

The GX-100 **transmits** program change on its TX channel, so in principle a
memory switch could double as "next song" and save a pedal on the floor. Two
cautions before trying it: check whether the pedal can send a **CC** from an
assign rather than a program change (a CC is much cleaner to MIDI-learn), and
set `MIDI IN THRU` / `USB IN THRU` to `OFF` — otherwise the pedal retransmits
what Reaper sends it and you have built a loop. **Verify this on the bench, not
at soundcheck.** If it is at all fiddly, use a separate switch.

---

## No stutter: architecture first, settings second

The settings everyone reaches for matter far less than this one decision:

> **Nothing is computed in real time at the gig.**

No drum plugin. No amp sim. No EQ, no compressor, no reverb — not one plugin
instance in the project. Every song is a finished stereo WAV that was rendered
days earlier. Then the only real-time work left in the whole system is:

| work | cost | risk |
|---|---|---|
| streaming a stereo WAV from SSD | negligible | none |
| firing a handful of MIDI bytes | negligible | none |
| **decoding video** | real | **this is the only thing that can stutter** |

That is worth restating: once the audio is pre-rendered WAV, an audio dropout is
close to impossible, and **all** of your remaining risk is in the video. Which
means if anything ever glitches, the video is what you sacrifice — and the audio
carries on regardless, because it is on a separate path.

### Do this

1. **Render everything.** One stereo WAV per song. For Diversamente Giovani the
   BASE files already are this. For Tutti in Fila, render after the drum work —
   do not carry a drum VST to the gig.
2. **One sample rate everywhere.** Render at whatever the audio interface runs
   at (48 kHz is the safe default) so nothing is resampled during playback.
   Mixed rates in one project force real-time conversion.
3. **WAV, never MP3.** MP3 costs a decode for every block, forever, for no
   benefit. Disk is free.
4. **Keep the video cheap.** Your 480p choice was right and worth keeping.
   Beyond resolution: a **low frame rate** (15 fps is plenty for lyrics — it
   halves the decode work) and an **all-intra codec** decode far more cheaply
   than long-GOP H.264, at the cost of bitrate. On an internal SSD that is a good
   trade.
5. **Internal SSD only.** Never a USB stick, never an SD card, never a network
   or cloud-synced folder. Pause any sync client.
6. **Raise the audio buffer.** Nothing is being recorded, so latency is
   irrelevant — use a large buffer. In Reaper's audio preferences also increase
   the media/disk read-ahead buffering, and turn **off** "close audio device when
   stopped"; a device that closes between songs will eventually not reopen.
7. **Turn off everything that touches disk on its own.** Autosave, peak-file
   building, undo-history-to-disk, update checks. At the OS level: sleep,
   screensaver, notifications, Wi-Fi, Bluetooth, every other application.
8. **Plugged in *and* charged.** Power management throttling mid-set is a classic.

### Then prove it

Open Reaper's performance meter (**View → Performance Meter**) and play the
**entire set** start to finish on the gig laptop, with the gig interface at the
gig buffer size, with the projector connected. Watch the CPU and disk figures.
Do it twice — once on mains, once on battery, because they can behave
differently.

A set that has never been played through in one sitting has not been tested.

---

## Alternatives, honestly

**QLab** (macOS) is purpose-built for exactly this: a cue list is your setlist, a
GO key advances, and each song is a group cue holding an audio cue, a video cue
and a MIDI cue. It pre-loads cues and is built by and for people whose show
cannot glitch. If reliability is the only thing you care about, it is arguably a
better fit than a DAW.

Against it: macOS only, the video and MIDI features are paid tiers, and it is a
new tool to learn while your assets and your muscle memory are in Reaper.

**Ableton Live** — Session view, one clip per song, follow actions, MIDI on a
track. Excellent for audio and MIDI, weak for video, and another new tool.

**One long video file for the whole set, lyrics and audio muxed** — maximally
robust and needs zero actions, but you cannot stop, restart or reorder, and the
gaps where the band talks are frozen at whatever length you rendered. Not usable
for a band that talks between songs.

**Verdict:** one Reaper session. It removes the exact problem you had, reuses
what you have already built, and once the audio is pre-rendered its reliability
is bounded only by video decode — which you can dial down as far as you like.
Revisit QLab only if the video turns out to be a problem you cannot settle by
lowering resolution and frame rate.

---

## The click question

Decide early, because it changes what you render:

* **Click in the band's monitors only** — the safe, normal choice. Keep the click
  on its own track, render a separate click-only file, and give front-of-house a
  clean feed. Requires the monitor path to exist.
* **Count-in only, no click during the song** — what `rambass countin` gives you:
  a count-in baked into the head of the base, then nothing. Simplest, and enough
  if the band can hold time against the backing track itself.
* **Click baked into the base** — never. It goes to the PA and cannot be removed.

Your existing bases have no click, and four of them have no count-in either,
which is what `rambass countin` fixes. If you decide you want click throughout,
that is a re-render, so decide before Lane A closes in [plan.md](plan.md).
