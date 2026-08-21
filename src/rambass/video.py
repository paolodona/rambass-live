"""Lyric and visual video generation for the screen behind the band.

Cues are written in bars, exactly like everything else, in a plain text file per
song (``lyrics.md`` by default)::

    # Nome Canzone

    [bar 1] image: intro-titolo.png
    [bar 9]
    Prima riga del testo
    Seconda riga

    [bar 17.3] image: foto-batterista.jpg
    Ritornello che parte in levare

    [bar 25] blank

A cue holds until the next one. ``image:`` swaps the background, ``blank``
clears the text, and ``[bar 17.3]`` means bar 17 beat 3 — useful for a line that
comes in on an upbeat.

The output is an ASS subtitle file plus, if ffmpeg is present, a rendered MP4.
Keeping the ASS file as an intermediate is deliberate: it can be dropped
straight onto a video track in Reaper or in any playback software, and it can be
proof-read without rendering anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .audio import ffmpeg_path, run
from .timeline import Timeline

CUE_RE = re.compile(
    r"^\[\s*bar\s+(?P<bar>\d+)(?:[.:](?P<beat>\d+(?:\.\d+)?))?\s*\]\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

DEFAULT_STYLE = {
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "font": "DejaVu Sans",
    "font_size": 96,
    "primary": "&H00FFFFFF",     # ASS colours are &HAABBGGRR
    "outline": "&H00101010",
    "back": "&H80000000",
    "outline_width": 4,
    "shadow": 2,
    "alignment": 5,              # 5 = middle centre (ASS uses numpad layout)
    "margin": 120,
    "background": "black",
}


@dataclass
class Cue:
    """One lyric / image cue, anchored to a bar and beat."""

    bar: int
    beat: float = 1.0
    lines: list[str] = field(default_factory=list)
    image: str = ""
    blank: bool = False

    @property
    def text(self) -> str:
        return "\\N".join(self.lines)


def parse_lyrics(text: str) -> list[Cue]:
    """Parse a lyrics file into cues, in bar order."""
    cues: list[Cue] = []
    current: Cue | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue                      # a title / comment heading
        match = CUE_RE.match(line.strip())
        if match:
            beat = float(match.group("beat")) if match.group("beat") else 1.0
            current = Cue(bar=int(match.group("bar")), beat=beat)
            cues.append(current)
            rest = match.group("rest").strip()
            if rest:
                _absorb(current, rest)
            continue
        if current is None:
            # Text before any cue belongs at bar 1.
            current = Cue(bar=1)
            cues.append(current)
        _absorb(current, line.strip())
    return sorted(cues, key=lambda c: (c.bar, c.beat))


def _absorb(cue: Cue, text: str) -> None:
    lowered = text.lower()
    if lowered.startswith("image:"):
        cue.image = text.split(":", 1)[1].strip()
        return
    if lowered in ("blank", "-", "(blank)"):
        cue.blank = True
        return
    cue.lines.append(text)


def build_ass(
    cues: list[Cue],
    timeline: Timeline,
    *,
    end_seconds: float,
    style: dict | None = None,
    title: str = "",
    card_seconds: float = 0.0,
    card_subtitle: str = "",
) -> str:
    """Render cues as an ASS subtitle file, timed off the audio clock.

    ``card_seconds`` holds a title card from the start of the video until then —
    normally the count-in. Same meaning as in :func:`rambass.lyrics.format_ass`,
    which is the writer the CLI uses for SRT-sourced cues.
    """
    settings = {**DEFAULT_STYLE, **(style or {})}
    lines = [
        "[Script Info]",
        f"Title: {title}",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {settings['width']}",
        f"PlayResY: {settings['height']}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        f"Style: Lyrics,{settings['font']},{settings['font_size']},"
        f"{settings['primary']},&H000000FF,{settings['outline']},{settings['back']},"
        f"-1,0,0,0,100,100,0,0,1,{settings['outline_width']},{settings['shadow']},"
        f"{settings['alignment']},{settings['margin']},{settings['margin']},"
        f"{settings['margin']},1",
        *([card_style_line(style)] if card_seconds > 0 else []),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if card_seconds > 0:
        lines.append(card_event(
            title, subtitle=card_subtitle, end=card_seconds, style=style,
        ))

    text_cues = [c for c in cues if c.lines or c.blank]
    for index, cue in enumerate(text_cues):
        if cue.blank and not cue.lines:
            continue
        start = timeline.audio_time(cue.bar, cue.beat)
        following = text_cues[index + 1] if index + 1 < len(text_cues) else None
        end = (
            timeline.audio_time(following.bar, following.beat)
            if following
            else end_seconds
        )
        if end <= start:
            continue
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Lyrics,,0,0,0,,{cue.text}"
        )
    return "\n".join(lines) + "\n"


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


def image_segments(
    cues: list[Cue],
    timeline: Timeline,
    end_seconds: float,
) -> list[tuple[str, float, float]]:
    """``(image, start_seconds, end_seconds)`` for every image cue."""
    image_cues = [c for c in cues if c.image]
    out: list[tuple[str, float, float]] = []
    for index, cue in enumerate(image_cues):
        start = timeline.audio_time(cue.bar, cue.beat)
        following = image_cues[index + 1] if index + 1 < len(image_cues) else None
        end = (
            timeline.audio_time(following.bar, following.beat)
            if following
            else end_seconds
        )
        if end > start:
            out.append((cue.image, start, end))
    return out


def render_video(
    output: str | Path,
    ass_file: str | Path,
    *,
    duration: float,
    images: list[tuple[str, float, float]] | None = None,
    image_dir: str | Path | None = None,
    audio: str | Path | None = None,
    style: dict | None = None,
    crf: int = 20,
) -> Path:
    """Burn the ASS file over a background and (optionally) mux the audio.

    Images are overlaid with ``enable='between(t,...)'`` so one ffmpeg pass
    produces the whole video. The subtitles go on last, which keeps the lyrics
    legible over any image.
    """
    settings = {**DEFAULT_STYLE, **(style or {})}
    images = images or []
    image_dir = Path(image_dir) if image_dir else Path(".")

    cmd = [ffmpeg_path(), "-v", "error", "-stats", "-y", "-nostdin"]
    cmd += [
        "-f", "lavfi",
        "-i", (
            f"color=c={settings['background']}:s={settings['width']}x{settings['height']}"
            f":r={settings['fps']}:d={duration:.3f}"
        ),
    ]

    resolved: list[tuple[Path, float, float]] = []
    for name, start, end in images:
        path = Path(name)
        if not path.is_absolute():
            path = image_dir / name
        if not path.is_file():
            raise FileNotFoundError(f"image cue points at a missing file: {path}")
        resolved.append((path, start, min(end, duration)))
    for path, _, _ in resolved:
        cmd += ["-loop", "1", "-i", str(path)]

    if audio:
        cmd += ["-i", str(audio)]

    filters: list[str] = []
    current = "[0:v]"
    for index, (_, start, end) in enumerate(resolved, start=1):
        scaled = f"[img{index}]"
        filters.append(
            f"[{index}:v]scale={settings['width']}:{settings['height']}"
            f":force_original_aspect_ratio=decrease,"
            f"pad={settings['width']}:{settings['height']}:-1:-1:color="
            f"{settings['background']}{scaled}"
        )
        nxt = f"[ov{index}]"
        filters.append(
            f"{current}{scaled}overlay=x=0:y=0:"
            f"enable='between(t,{start:.3f},{end:.3f})'{nxt}"
        )
        current = nxt

    ass_escaped = str(ass_file).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    filters.append(f"{current}ass='{ass_escaped}'[vout]")

    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
    if audio:
        cmd += ["-map", f"{len(resolved) + 1}:a", "-c:a", "aac", "-b:a", "256k"]
    cmd += [
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-t", f"{duration:.3f}",
        str(output),
    ]

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    run(cmd, quiet=True)
    return output


# ── Title cards ──────────────────────────────────────────────────────────
#
# Every song needs something on screen before its first note, and 17 of the 23
# in the set have no lyric video at all — the two a cappella numbers never will,
# because there is no backing track to time cues against. The card is also what
# makes the parked-between-songs behaviour in docs/live-playback.md work: the
# transport sits at the next region's start while the band talks, so whatever is
# at the head of the region is what the audience is looking at.
#
# A card is deliberately the same machinery as a lyric video — text burnt over a
# background — so it is proof-readable before rendering and needs no new
# dependency. It arrives two ways:
#
# * as the **lead-in of a song's own lyric video**, filling the count-in, which
#   is otherwise blank. One item on the video track, no overlap to arbitrate.
# * as a **standalone file** for a song with no lyric video, which holds the
#   whole region on its own.
#
# What must not happen is a separate card item laid over the head of a lyric
# video: the rendered video runs on the audio clock and therefore already
# contains the count-in, so it has to start at the region start, and two video
# items in the same place is a question about Reaper's compositing that this
# repo does not need to answer.

CARD_STYLE = {
    **DEFAULT_STYLE,
    # The title is the only thing on screen, so it can be much bigger than a
    # lyric line. 96 pt is the floor for lyrics; a card has no competition.
    "font_size": 150,
    "subtitle_size": 60,
    "alignment": 5,
}


def _ass_text(text: str) -> str:
    """One line of plain text, safe to drop into an ASS event.

    ASS gives ``{``, ``}`` and ``\\`` their own meaning, and a stray one turns
    the rest of the line into a silently-ignored override block. Song titles do
    not contain them, but a subtitle typed on the command line might, so they
    are mapped to something harmless rather than escaped.
    """
    return (
        text.replace("{", "(").replace("}", ")").replace("\\", "/")
        .replace("\n", " ").strip()
    )


def card_style_line(style: dict | None = None) -> str:
    """The ``Style: Card`` line, shared by every writer that draws a card."""
    settings = {**CARD_STYLE, **(style or {})}
    return (
        f"Style: Card,{settings['font']},{settings['font_size']},"
        f"{settings['primary']},&H000000FF,{settings['outline']},{settings['back']},"
        f"-1,0,0,0,100,100,0,0,1,{settings['outline_width']},{settings['shadow']},"
        f"{settings['alignment']},{settings['margin']},{settings['margin']},"
        f"{settings['margin']},1"
    )


def card_event(
    title: str,
    *,
    subtitle: str = "",
    start: float = 0.0,
    end: float,
    style: dict | None = None,
) -> str:
    """The one ``Dialogue`` line that puts a title card on screen."""
    settings = {**CARD_STYLE, **(style or {})}
    text = _ass_text(title)
    if subtitle:
        text += f"\\N{{\\fs{settings['subtitle_size']}}}{_ass_text(subtitle)}"
    return (
        f"Dialogue: 0,{_ass_time(start)},{_ass_time(max(end, start + 0.04))},"
        f"Card,,0,0,0,,{text}"
    )


def card_ass(
    title: str,
    *,
    subtitle: str = "",
    duration: float,
    style: dict | None = None,
) -> str:
    """A standalone ASS file holding one title card for ``duration`` seconds."""
    settings = {**CARD_STYLE, **(style or {})}
    lines = [
        "[Script Info]",
        f"Title: {title} — title card",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {settings['width']}",
        f"PlayResY: {settings['height']}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        card_style_line(settings),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        card_event(title, subtitle=subtitle, end=duration, style=settings),
    ]
    return "\n".join(lines) + "\n"


def card_command(
    output: str | Path,
    ass_file: str | Path,
    *,
    duration: float,
    background: str | Path | None = None,
    style: dict | None = None,
    crf: int = 20,
    ffmpeg: str | None = None,
) -> list[str]:
    """The ffmpeg command for one standalone title card.

    Built separately from running it so the command can be tested — and printed
    and run by hand — without ffmpeg being installed. A ``.png`` output is a
    single frame, which is what the show project wants because Reaper can hold a
    still for as long as the region needs; anything else is a still video
    ``duration`` seconds long, for a screen driven by something fussier.
    """
    settings = {**CARD_STYLE, **(style or {})}
    output = Path(output)
    still = output.suffix.lower() == ".png"
    duration = max(duration, 0.04)

    cmd = [ffmpeg or ffmpeg_path(), "-v", "error", "-y", "-nostdin"]
    cmd += [
        "-f", "lavfi",
        "-i", (
            f"color=c={settings['background']}:s={settings['width']}x{settings['height']}"
            f":r={settings['fps']}:d={duration:.3f}"
        ),
    ]

    filters: list[str] = []
    current = "[0:v]"
    if background:
        path = Path(background)
        if not path.is_file():
            raise FileNotFoundError(f"card background is missing: {path}")
        cmd += ["-loop", "1", "-i", str(path)]
        filters.append(
            f"[1:v]scale={settings['width']}:{settings['height']}"
            f":force_original_aspect_ratio=decrease,"
            f"pad={settings['width']}:{settings['height']}:-1:-1:color="
            f"{settings['background']}[bg]"
        )
        filters.append(f"{current}[bg]overlay=x=0:y=0[ov]")
        current = "[ov]"

    ass_escaped = str(ass_file).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    filters.append(f"{current}ass='{ass_escaped}'[vout]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]"]

    if still:
        cmd += ["-frames:v", "1", str(output)]
    else:
        cmd += [
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-t", f"{duration:.3f}",
            str(output),
        ]
    return cmd


def render_card(
    output: str | Path,
    ass_file: str | Path,
    *,
    duration: float,
    background: str | Path | None = None,
    style: dict | None = None,
    crf: int = 20,
) -> Path:
    """Render a title card as a PNG still or a short still video."""
    cmd = card_command(
        output, ass_file,
        duration=duration, background=background, style=style, crf=crf,
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    run(cmd, quiet=True)
    return output
