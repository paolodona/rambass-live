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

## Two commands

```
rambass video ass 04-titolo      # subtitles only — fast
rambass video render 04-titolo   # the MP4 — slow
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
  in the band's shared drive and note the source in `notes:`.

## If ffmpeg is being difficult

`rambass doctor` will tell you whether ffmpeg is there at all. Beyond that, the
render is one ffmpeg command; run with the filter graph printed and try it by
hand. The most common failure is an `image:` cue pointing at a file that does not
exist — that is raised as a clear error before ffmpeg is invoked at all.
