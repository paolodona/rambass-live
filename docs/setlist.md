# Ordering the set

The order is not taste. There is a body of practice here that professionals
converge on, and it is specific enough to check against.

## What the sources agree on

**The five-part shape.** Hook, lift, breath, climb, closer. Open with something
immediately recognisable — the first minute sets what the audience expects of the
whole night. Build across three or four songs where tempo and dynamics *rise*
rather than plateau. Drop to one real breather so the room can recover. Climb
again, harder, with the best crowd-pleasing material. Finish on the peak.

**Plot it as a curve.** Energy should fall towards a mid-set trough and rise
again — a U, not a line. A set of uniformly mid-tempo songs is the failure mode
everyone describes and nobody notices while writing it: the audience senses
something is wrong without being able to say what.

**But not your *biggest* song at the front.** This is the distinction that
matters most, and the one easiest to get wrong — the "crowd-pleasers at both
ends" advice comes from the function-band world, where the job is instant
approval. A band with a signature song plays by a different rule: that song is a
card you play once, and spending it first deflates everything after it.

The hard data is unambiguous. Metallica's M72 tour ran **99 shows with no encore
at all**, and put **Enter Sandman at 17 and Master of Puppets at 18 — of 19**.
The opening slot is described as *"an upper-mid-tempo fan favorite with a quickly
recognizable opening riff, not too fast or complicated"*. So:

* the opener is **recognisable and energetic, but not the heaviest or fastest
  thing you own** — and not the biggest;
* the biggest songs go **near the end of the main set**;
* they are **not** held back behind a walk-off. Holding back the beloved tracks
  builds anticipation across the set; hiding them behind an encore is a different
  and more fragile trick.

Unfamiliar material lives in the middle, carried by the songs either side.

**Never three the same.** Three songs at the same tempo is the limit, and only
when their feels and keys genuinely differ. Four and the band starts to sound
like one long song. Two songs in the same key back to back is treated as a hard
no. Alternate in groups: two up, a mid, a ballad, back up.

**Keep the gaps short.** Ten to fifteen seconds of silence between songs, no
more. About twelve to fifteen songs an hour once changeovers are counted.

## How this repo models it

Each song carries the band's own read of itself in `song.yaml`:

```yaml
character:
  genre: heavy metal, funny
  energy: 5            # 1 = ballad, 5 = flat out
  heaviness: 5         # 1 = acoustic/clean, 5 = brutal
  standing: known      # signature | hit | known | deep | new
  role: ""             # opener | closer | interlude | detour | linking
  note: "the heaviest song in the set"
```

**Energy and heaviness are separate on purpose.** Il Phurgone is high energy and
not heavy at all; Mandami un Faxe is neither; Orologiaio is both, for ninety
seconds. One number would lose exactly the information that makes an order work.

`standing` is what decides where a song *may* sit, and **`signature` is its own
tier above `hit`** for exactly the reason above: I Puffi is not simply a loud
hit, it is the one people came for, and the model has to know the difference or
it will happily suggest opening with it.

`role` is for the cases where a song's job is fixed — La Ragazza da Milano is the
closer whatever else changes.

## Checking an order

```
rambass arc gig
```

prints the running order with an energy sparkline and then says what the order
breaks. It does not generate an order: sequencing is a musical judgement, and the
useful thing a tool can do is catch what a human misses in their own list.

It checks the opener and closer (energy, and whether the audience knows them),
runs of identical energy, ballads back to back, whether hits reach both ends,
whether unfamiliar material has been left exposed at either end, whether the set
has any dynamic range at all, whether it ever returns to its peak after halfway,
and whether a linking piece has been stranded at the end.

Exit code is non-zero if there are problems, so it can gate a rehearsal.

## The set as it stands

`setlists/gig.yaml` — 23 songs, passing clean. The reasoning is in the file's
own comments, next to the order it justifies. In short:

* **I Puffi at 22, not at 1.** It is the signature song, and it sits in the
  closing run with the anthem after it
* ForMayGrana opens instead — a hit, upbeat, happy, with a riff they know
  immediately. Opening on Mother Sacher, the heaviest thing in the set, would be
  the mistake the checker exists to catch
* first trough at Manlio, peak at Orologiaio into Itturfiatrugoy
* the deep cuts clustered at 12–15, where unfamiliar material belongs and where
  a long song like Skizzo Sonovabic costs least
* second trough at Mandami un Faxe — the riskiest song to place, so it is placed
  deliberately rather than dropped somewhere
* the two a cappella numbers at 10 and 20, spread apart. They are the only points
  where the laptop does nothing, which makes them both the breathers *and* the
  natural places to talk, retune, or recover if something has gone wrong
* the closing run is Mother Sacher → I Puffi → La Ragazza: heaviest, then the
  signature song, then the anthem. Two biggest at 21 and 22 with the structural
  ender last, which is the same shape as Enter Sandman at 17 and Master of
  Puppets at 18 of 19
* **no encore.** The big songs are in the set

`setlists/live-2023.yaml` — what was actually played in 2023, marked
`historical: true` so it is reviewed but not failed. Worth running: **it opened
with I Puffi**, which is the one thing the checker now calls a problem. That is
the easiest set-list mistake there is, and the last set made it.

`setlists/tier-a.yaml` — the seven songs that already have a finished base *and*
a rendered video. Around 35 minutes, playable this week, same principles applied
to a shorter arc. Keep it even after the full set is ready: it is the answer to a
support slot, and the fallback if a rehearsal goes badly.

## One thing the numbers cannot express

La Ragazza da Milano is energy 4, not 5 — Mother Sacher and the metal songs are
more intense. It is still the right closer, because its finale is a big chorus
that repeats as long as it keeps working, and the MIDI has two fake stops before
it comes back. That is a *structural* property, not an energy level. The arc is
built so it lands as the climax regardless.

Sources: [Metallica M72 average setlist, setlist.fm](https://www.setlist.fm/stats/average-setlist/metallica-3bd680c8.html?tour=3dff98b) ·
[Ultimate Classic Rock on the M72 slots](https://ultimateclassicrock.com/metallica-one-night-set-list/) ·
[Bands for Hire](https://www.bandsforhire.net/musicians-blog/planning-the-perfect-setlist) ·
[Music Planner](https://musicplanner.app/blog/how-to-build-the-perfect-setlist) ·
[Stages Music Arts](https://www.stagesmusicarts.com/creating-the-perfect-setlist/) ·
[DIY Musician](https://diymusician.cdbaby.com/music-career/the-art-of-the-set-list-choosing-the-right-songs-in-the-right-order/) ·
[Berklee Online](https://online.berklee.edu/takenote/how-to-prepare-a-setlist/)
