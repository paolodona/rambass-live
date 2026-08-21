# Gig-day runbook

The drummer is a laptop. Plan for the laptop failing.

## Week before

* [ ] `rambass check` clean, `rambass status` all `done`
* [ ] Whole set played start to finish, on the gig laptop, in one sitting
* [ ] Loudness matched across songs — nothing jumps between tracks
* [ ] `rambass setlist gig --out setlist.md` printed
* [ ] `rambass gx100 sheet --setlist gig --out gx100-sheet.md` printed and taped
      to the pedalboard
* [ ] Backing tracks exported as plain WAVs to a **second device** (phone,
      second laptop, USB stick). If Reaper will not open, the show still happens
      from a media player and a running order.

## Day before

* [ ] Reaper: **Preferences > Audio > Close audio device when stopped** → off
* [ ] Every autosave, backup prompt and update check disabled
* [ ] OS notifications off, sleep off, screensaver off, Bluetooth off
* [ ] Laptop plugged in *and* charged — both, not either
* [ ] Spare cables: USB, the audio interface's power supply, one MIDI cable
* [ ] Interface buffer set for stable playback, not low latency; nothing is
      being recorded

## Soundcheck

* [ ] Click **only** in the band's monitors, never to the PA. Confirm this by
      listening to the PA, not by looking at the routing.
* [ ] Count-in audible to everyone who needs it
* [ ] One patch change tested end to end: Reaper sends, pedal switches
* [ ] MIDI clock reaching the pedal — check a tempo-synced delay follows the song
* [ ] Transport footswitch tested: next song, restart song, panic stop
* [ ] Confirm what happens on a panic stop — silence, not a stuck note

## During

* Regions are in running order; `play_next` moves to the next one.
* If a song starts wrong, `play_again` restarts the region — it does not advance.
* If a note hangs, `stop_panic` — stop plus all-notes-off on every track.
* If the laptop dies mid-set: the printed running order and the patch sheet are
  the fallback. The band plays the rest without drums, or from the WAVs on the
  second device.

## After

* Note what actually went wrong in the song's `notes:` field, while it is fresh.
  Next gig's you will not remember.
