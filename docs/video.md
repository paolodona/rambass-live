# Video: lyrics and images

## The cue file

One `lyrics.md` per song. Cues are anchored to bars, like everything else, and a
cue holds until the next one:

```
# Nome Canzone

[bar 1] image: ../../../video/assets/logo.png

[bar 9]
Prima riga del testo
Seconda riga del testo

[bar 17.3] image: foto-batterista.jpg
Ritornello che parte in levare

[bar 25] blank
```

* `[bar 9]` — bar 9, beat 1
* `[bar 17.3]` — bar 17, beat 3, for a line that comes in on an upbeat
* `image: <path>` — swaps the background; relative to the song folder
* `blank` — clears the text
* lines starting with `#` are comments and headings

Text and image cues are tracked separately, so an image can hold across several
lyric changes and vice versa.

## Three commands

```
rambass video ass 04-titolo      # subtitles only — fast
rambass video render 04-titolo   # the MP4 — slow
rambass video card 04-titolo     # a still title card, for a song with no cues
```

Always run `ass` first and read it. It is a text file; a typo or a mistimed line
is obvious there and costs nothing to fix, whereas each render is minutes.

The `.ass` file is also useful on its own: drop it on a video track in Reaper, or
feed it to any player, without rendering anything.

## Rendering

```
rambass video render 04-titolo --with-audio
```

Background, then image overlays with `enable='between(t,...)'`, then the
subtitles burned on last so the lyrics stay legible over any image. One ffmpeg
pass. `--with-audio` muxes in `render/<slug>.wav` if it exists, otherwise the
original mix — handy for checking sync, and you would normally play the video
silent at the gig with the audio coming from Reaper.

## Title cards

Every song needs something on screen before its first note, and the screen also
has to show *something* while the band talks between numbers — the show project
parks the transport at the head of the next region, so whatever is there is what
the audience is looking at.

That comes two ways, and which one a song gets is not a preference:

**A song with lyric cues** gets the card as the **lead-in of its own video**.
`video ass` and `video render` put the title on screen from the first frame until
the end of the count-in, which is the one stretch of the video with no lyric on
it — cues start at bar 1.

```
rambass video render 04-titolo --subtitle "Tutti in Fila"
rambass video render 04-titolo --no-card         # leave the count-in blank
```

**A song with no cues** — the two a cappella numbers, which can never have any
because there is no backing track to time against, and any song whose words are
not written yet — gets a standalone still:

```
rambass video card 11-se-sei-felice --subtitle "Diversamente Giovani"
rambass video card --all                          # one for every song
rambass video card 04-titolo --background foto.jpg
rambass video card 04-titolo --ass-only           # no ffmpeg needed
```

A PNG, deliberately: the show project holds it for the length of the region, and
Reaper can stretch a still to any length, whereas an MP4 would have to be
rendered at a duration nobody knows until the base is finished. `--mp4` exists
for a screen that will not take an image, and then `--duration` matters.

What must **not** happen is a card item laid over the front of a lyric video. A
rendered video runs on the audio clock, so it already contains the count-in and
must start at the region start; two items in the same place on one video track is
a question about Reaper's compositing that this repo does not need to answer.
`rambass reaper setlist` therefore places exactly one item per region and prefers
the lyric video.

## Style

Defaults in `rambass/video.py`: 1920×1080, 30 fps, DejaVu Sans 96 pt, white with
a dark outline, centred. Colours are ASS format, `&HAABBGGRR` — alpha first and
blue before red, which catches everyone out once.

For the band's own laptop and projector, check two things: the projector's real
resolution (a 1080p file on a 1280×800 projector will be scaled and the outline
will soften), and the font. If DejaVu Sans is not installed on the machine doing
the rendering, ffmpeg silently substitutes something else.

## Practicalities for the screen

* **Big.** Legible from the back of the room means bigger than looks right on a
  laptop. 96 pt at 1080p is a floor, not a ceiling.
* **Two lines at a time**, maximum. Four lines of text on a screen behind a band
  is wallpaper, not something anyone reads.
* **Test on the actual projector.** Contrast that looks fine on a screen at home
  disappears under stage light.
* **Put the funny pictures on the instrumentals.** Competing with the vocal line
  for attention loses.
* Keep source images in `video/assets/` if they are shared between songs, or in
  the song folder if they are specific to it. Images are gitignored — keep them
  in the band's shared drive and note the source in `notes:`. Generated cards are
  covered by the same rule: they are rebuilt from `song.yaml` in a second.
* **A card is not a substitute for a video** on a song that is going to have
  one. It is what goes up when there is nothing else, and for the a cappella
  numbers that is the finished answer, not a placeholder.

## If ffmpeg is being difficult

`rambass doctor` will tell you whether ffmpeg is there at all. Beyond that, the
render is one ffmpeg command; run with the filter graph printed and try it by
hand. The most common failure is an `image:` cue pointing at a file that does not
exist — that is raised as a clear error before ffmpeg is invoked at all.
