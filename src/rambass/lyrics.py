"""Lyrics as timed subtitle cues: read, write, convert, check, transcribe.

Why lyrics are the one thing not anchored to bars
-------------------------------------------------
Everything else in this repo is positioned in bars, and for good reason. Lyric
cues are the deliberate exception, and they are stored in **SRT**.

The reason is that the existing hand-timed cue files for this band run to well
over a hundred cues in a four-minute song — a short phrase at a time, timed to
the millisecond against a specific master recording, with the shouted words in
caps and the held notes trailing off in ellipses. That is a performance
transcription, not a musical anchor. Quantising it to a bar grid would throw
away the author's timing and buy nothing, because the video is rendered against
the same audio the cues were written against.

So: SRT is the canonical store, `lyrics.srt` per song, in git. Songs authored
from scratch can still be written in the bar-cue `lyrics.md` format and
converted with :func:`from_bar_cues` — and if a song's timing base ever moves,
:func:`shift` and :func:`scale` move the cues with it.

SRT is also simply what subtitle software wants, which is the point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from .timeline import Timeline

#: `1` then `00:00:02,850 --> 00:00:07,039` then one or more text lines.
SRT_TIME = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{1,3})"
    r"\s*-->\s*"
    r"(?P<h2>\d{1,2}):(?P<m2>\d{2}):(?P<s2>\d{2})[,.](?P<ms2>\d{1,3})"
)
LRC_LINE = re.compile(r"^\[(?P<m>\d{1,3}):(?P<s>\d{2})([.:](?P<cs>\d{1,3}))?\]\s*(?P<text>.*)$")

#: Subtitle conventions worth enforcing — a line the audience cannot read in
#: time is worse than no line at all.
#:
#: The floor is deliberately low: the band's existing hand-timed files run
#: rapid-fire syllable cues down to about 0.29 s ("Ad ogni / donna / che vedo /
#: toccherei / una TETTA!"), and that is a style choice, not a mistake. Anything
#: under ~0.2 s really is unreadable.
MIN_DURATION = 0.20          # seconds on screen
MAX_CHARS_PER_LINE = 42      # per rendered line, before wrapping
MAX_LINES = 3


class LyricsError(ValueError):
    """Raised for malformed cue files."""


@dataclass(frozen=True)
class LyricLine:
    """One subtitle cue: a span of time and the text shown during it."""

    start: float
    end: float
    text: str

    def __post_init__(self) -> None:
        if self.start < 0:
            raise LyricsError(f"cue starts before zero: {self.start}")
        if self.end < self.start:
            raise LyricsError(f"cue ends before it starts: {self.start} -> {self.end}")

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def lines(self) -> list[str]:
        return self.text.split("\n")

    def shifted(self, seconds: float) -> LyricLine:
        return replace(self, start=max(0.0, self.start + seconds),
                       end=max(0.0, self.end + seconds))

    def scaled(self, factor: float) -> LyricLine:
        return replace(self, start=self.start * factor, end=self.end * factor)


# ── SRT ──────────────────────────────────────────────────────────────────
def parse_srt(text: str) -> list[LyricLine]:
    """Parse SRT. Tolerates CRLF, BOM, missing indices and dotted decimals."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    out: list[LyricLine] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip("\n")
        if not block.strip():
            continue
        lines = block.split("\n")
        # An optional numeric index line comes first.
        if lines and lines[0].strip().isdigit() and len(lines) > 1:
            lines = lines[1:]
        if not lines:
            continue
        match = SRT_TIME.search(lines[0])
        if not match:
            raise LyricsError(f"no timecode in SRT block:\n{block[:120]}")
        body = "\n".join(lines[1:]).strip("\n")
        out.append(LyricLine(_srt_seconds(match, ""), _srt_seconds(match, "2"), body))
    return sorted(out, key=lambda c: (c.start, c.end))


def _srt_seconds(match: re.Match, suffix: str) -> float:
    hours = int(match.group("h" + suffix))
    minutes = int(match.group("m" + suffix))
    seconds = int(match.group("s" + suffix))
    millis = match.group("ms" + suffix).ljust(3, "0")
    return hours * 3600 + minutes * 60 + seconds + int(millis) / 1000.0


def format_srt(cues: list[LyricLine], *, newline: str = "\r\n") -> str:
    """Write SRT. Indices are renumbered from 1.

    CRLF by default: it is what the SRT convention says, what every subtitle
    editor emits, and what the band's existing hand-timed files already use, so
    a regenerated file diffs cleanly against them.
    """
    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}{newline}"
            f"{_srt_time(cue.start)} --> {_srt_time(cue.end)}{newline}"
            f"{cue.text}{newline}"
        )
    return newline.join(blocks)


def _srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    millis = int(round(seconds * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


# ── WebVTT ───────────────────────────────────────────────────────────────
def format_vtt(cues: list[LyricLine]) -> str:
    """WebVTT — what browser-based and online subtitle editors usually want."""
    parts = ["WEBVTT", ""]
    for index, cue in enumerate(cues, start=1):
        parts.append(str(index))
        parts.append(f"{_vtt_time(cue.start)} --> {_vtt_time(cue.end)}")
        parts.append(cue.text)
        parts.append("")
    return "\n".join(parts)


def _vtt_time(seconds: float) -> str:
    return _srt_time(seconds).replace(",", ".")


# ── LRC ──────────────────────────────────────────────────────────────────
def format_lrc(cues: list[LyricLine], *, title: str = "", artist: str = "") -> str:
    """LRC — one timestamp per line, for karaoke players.

    LRC has no end times, so a cue's end is only represented when it is
    followed by a gap, which is written as an empty line. Multi-line cues are
    flattened to one line each, sharing the cue's start.
    """
    parts = []
    if title:
        parts.append(f"[ti:{title}]")
    if artist:
        parts.append(f"[ar:{artist}]")
    for index, cue in enumerate(cues):
        for line in cue.lines:
            parts.append(f"[{_lrc_time(cue.start)}]{line}")
        following = cues[index + 1] if index + 1 < len(cues) else None
        if following is None or following.start - cue.end > 0.5:
            parts.append(f"[{_lrc_time(cue.end)}]")
    return "\n".join(parts) + "\n"


def _lrc_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    centis = int(round(seconds * 100))
    minutes, rest = divmod(centis, 6000)
    secs, centis = divmod(rest, 100)
    return f"{minutes:02d}:{secs:02d}.{centis:02d}"


def parse_lrc(text: str) -> list[LyricLine]:
    """Parse LRC, deriving each cue's end from the next timestamp."""
    stamped: list[tuple[float, str]] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        match = LRC_LINE.match(raw.strip())
        if not match:
            continue
        centis = int((match.group("cs") or "0").ljust(2, "0")[:2])
        seconds = int(match.group("m")) * 60 + int(match.group("s")) + centis / 100.0
        stamped.append((seconds, match.group("text").strip()))
    stamped.sort(key=lambda item: item[0])

    out: list[LyricLine] = []
    for index, (start, text_line) in enumerate(stamped):
        if not text_line:                      # a bare timestamp ends the previous cue
            continue
        end = stamped[index + 1][0] if index + 1 < len(stamped) else start + 3.0
        out.append(LyricLine(start, max(end, start + MIN_DURATION), text_line))
    return out


# ── ASS ──────────────────────────────────────────────────────────────────
def format_ass(
    cues: list[LyricLine],
    *,
    style: dict | None = None,
    title: str = "",
    card_seconds: float = 0.0,
    card_subtitle: str = "",
) -> str:
    """ASS, sharing the styling used by ``rambass video render``.

    ``card_seconds`` puts a title card on screen from the start of the video
    until that time — normally the count-in, which is otherwise blank, since
    cues start at bar 1. That is what the audience reads while the band is
    counted in, and what the projector shows when the show project is parked at
    the head of the song between numbers.
    """
    from .video import DEFAULT_STYLE, card_event, card_style_line

    settings = {**DEFAULT_STYLE, **(style or {})}
    styles = [
        f"Style: Lyrics,{settings['font']},{settings['font_size']},"
        f"{settings['primary']},&H000000FF,{settings['outline']},{settings['back']},"
        f"-1,0,0,0,100,100,0,0,1,{settings['outline_width']},{settings['shadow']},"
        f"{settings['alignment']},{settings['margin']},{settings['margin']},"
        f"{settings['margin']},1",
    ]
    if card_seconds > 0:
        styles.append(card_style_line(style))
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
        *styles,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if card_seconds > 0:
        lines.append(card_event(
            title, subtitle=card_subtitle, end=card_seconds, style=style,
        ))
    for cue in cues:
        body = cue.text.replace("\n", r"\N")
        lines.append(
            f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},Lyrics,,0,0,0,,{body}"
        )
    return "\n".join(lines) + "\n"


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


# ── plain text ───────────────────────────────────────────────────────────
def format_text(cues: list[LyricLine]) -> str:
    """Just the words — for the sleeve, the band's chat, or proof-reading."""
    return "\n".join(cue.text for cue in cues) + "\n"


# ── bar-cue bridge ───────────────────────────────────────────────────────
def from_bar_cues(text: str, timeline: Timeline, *, end_seconds: float) -> list[LyricLine]:
    """Convert the bar-anchored ``lyrics.md`` format into timed cues."""
    from .video import parse_lyrics

    cues = [c for c in parse_lyrics(text) if c.lines or c.blank]
    out: list[LyricLine] = []
    for index, cue in enumerate(cues):
        if cue.blank and not cue.lines:
            continue
        start = timeline.audio_time(cue.bar, cue.beat)
        following = cues[index + 1] if index + 1 < len(cues) else None
        stop = timeline.audio_time(following.bar, following.beat) if following else end_seconds
        if stop > start:
            out.append(LyricLine(start, stop, "\n".join(cue.lines)))
    return out


def to_bar_cues(cues: list[LyricLine], timeline: Timeline) -> str:
    """Convert timed cues back to the bar-anchored format.

    Lossy by definition — cue times are snapped to the nearest beat. Useful when
    a song's lyrics were drafted against one recording and now need to follow a
    tempo that is still being decided; not a round trip.
    """
    parts = []
    for cue in cues:
        musical = cue.start - timeline.count_in_seconds
        bar, beat = timeline.seconds_to_bar_beat(musical)
        beat_rounded = max(1, int(round(beat)))
        marker = f"[bar {bar}]" if beat_rounded == 1 else f"[bar {bar}.{beat_rounded}]"
        parts.append(marker)
        parts.extend(cue.lines)
        parts.append("")
    return "\n".join(parts)


# ── editing ──────────────────────────────────────────────────────────────
def shift(cues: list[LyricLine], seconds: float) -> list[LyricLine]:
    """Move every cue later (positive) or earlier (negative).

    This is what you need after adding a count-in, or when the cues were timed
    against the album master and the backing track starts somewhere else.
    """
    return [cue.shifted(seconds) for cue in cues]


def scale(cues: list[LyricLine], factor: float) -> list[LyricLine]:
    """Stretch or compress the whole cue list about time zero."""
    if factor <= 0:
        raise LyricsError("scale factor must be positive")
    return [cue.scaled(factor) for cue in cues]


def retime(cues: list[LyricLine], from_bpm: float, to_bpm: float) -> list[LyricLine]:
    """Rescale cues for a tempo change — 124 BPM cues onto a 128 BPM render."""
    if from_bpm <= 0 or to_bpm <= 0:
        raise LyricsError("tempos must be positive")
    return scale(cues, from_bpm / to_bpm)


def wrap_lines(cues: list[LyricLine], max_chars: int = MAX_CHARS_PER_LINE) -> list[LyricLine]:
    """Break over-long cue text onto extra lines at word boundaries."""
    out: list[LyricLine] = []
    for cue in cues:
        wrapped: list[str] = []
        for line in cue.lines:
            if len(line) <= max_chars:
                wrapped.append(line)
                continue
            current = ""
            for word in line.split():
                candidate = f"{current} {word}".strip()
                if len(candidate) > max_chars and current:
                    wrapped.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                wrapped.append(current)
        out.append(replace(cue, text="\n".join(wrapped)))
    return out


# ── checking ─────────────────────────────────────────────────────────────
def problems(cues: list[LyricLine], *, duration: float | None = None) -> list[str]:
    """Everything wrong with a cue list that a renderer would not tell you."""
    out: list[str] = []
    if not cues:
        return ["no cues at all"]

    for index, cue in enumerate(cues, start=1):
        if cue.duration < MIN_DURATION:
            out.append(f"cue {index} at {cue.start:.2f}s is on screen for only "
                       f"{cue.duration * 1000:.0f} ms")
        if not cue.text.strip():
            out.append(f"cue {index} at {cue.start:.2f}s has no text")
        if len(cue.lines) > MAX_LINES:
            out.append(f"cue {index} at {cue.start:.2f}s has {len(cue.lines)} lines "
                       f"(more than {MAX_LINES} is wallpaper, not something anyone reads)")
        for line in cue.lines:
            if len(line) > MAX_CHARS_PER_LINE:
                out.append(f"cue {index} at {cue.start:.2f}s has a {len(line)}-character "
                           f"line; wrap it or split the cue")

    for index in range(len(cues) - 1):
        current, following = cues[index], cues[index + 1]
        if following.start < current.end - 0.001:
            out.append(f"cues {index + 1} and {index + 2} overlap "
                       f"({current.end:.3f}s > {following.start:.3f}s)")

    if duration is not None and cues[-1].end > duration + 1.0:
        out.append(f"last cue ends at {cues[-1].end:.1f}s, after the {duration:.1f}s "
                   "the song lasts")
    return out


#: Keys :func:`stats` always returns, so callers can format without guarding.
STAT_KEYS = (
    "cues", "first", "last", "words", "mean_duration", "shortest",
    "longest_line", "largest_gap",
)


def stats(cues: list[LyricLine]) -> dict:
    """Summary numbers, printed by ``rambass lyrics check``.

    Always returns every key in :data:`STAT_KEYS`, zeroed for an empty cue list —
    an empty ``lyrics.md`` template is the normal state of a song nobody has
    started yet, and that should not be a crash.
    """
    if not cues:
        return dict.fromkeys(STAT_KEYS, 0)
    durations = [c.duration for c in cues]
    characters = [len(line) for c in cues for line in c.lines]
    gaps = [cues[i + 1].start - cues[i].end for i in range(len(cues) - 1)]
    return {
        "cues": len(cues),
        "first": round(cues[0].start, 2),
        "last": round(cues[-1].end, 2),
        "words": sum(len(c.text.split()) for c in cues),
        "mean_duration": round(sum(durations) / len(durations), 2),
        "shortest": round(min(durations), 2),
        "longest_line": max(characters) if characters else 0,
        "largest_gap": round(max(gaps), 2) if gaps else 0.0,
    }


# ── automatic transcription ──────────────────────────────────────────────
def transcribe(
    audio,
    *,
    language: str = "it",
    model: str = "medium",
    max_chars: int = MAX_CHARS_PER_LINE,
    prompt: str = "",
    device: str = "auto",
) -> tuple[list[LyricLine], dict]:
    """Draft cues from audio with Whisper.

    This is a **drafting aid**, not a transcriber of record. Sung Italian over a
    full band mix is close to the hardest case there is for speech recognition:
    expect it to get the shape and rough timing of each phrase and to mangle
    invented words, dialect, shouted asides and anything buried in the mix —
    which, for this band, is a lot of the funniest lines.

    Two things make it markedly better and are worth the effort:

    * run it on a separated **vocal stem** rather than the full mix
      (``rambass stems`` produces one), and
    * pass the real words via *prompt* when you have them, which biases the
      decoder towards the band's spellings instead of the nearest dictionary
      word.

    Then read every line against the audio and fix it. The point is to skip the
    typing, not the listening.
    """
    from .audio import AudioError

    try:
        from faster_whisper import WhisperModel  # noqa: PLC0415 - optional extra
    except ImportError:
        try:
            import whisper  # noqa: PLC0415 - optional fallback
        except ImportError as exc:
            raise AudioError(
                "automatic lyric transcription needs a Whisper implementation.\n"
                "  install it with:  pip install -e '.[lyrics]'\n"
                "  (that brings in faster-whisper, which runs well on CPU)"
            ) from exc
        return _transcribe_openai_whisper(whisper, audio, language, model, max_chars, prompt)

    engine = WhisperModel(
        model,
        device="auto" if device == "auto" else device,
        compute_type="int8" if device in ("cpu", "auto") else "float16",
    )
    segments, info = engine.transcribe(
        str(audio),
        language=language,
        initial_prompt=prompt or None,
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    cues: list[LyricLine] = []
    for segment in segments:
        text = (segment.text or "").strip()
        if not text:
            continue
        cues.append(LyricLine(float(segment.start), float(segment.end), text))
    report = {
        "engine": f"faster-whisper {model}",
        "language": getattr(info, "language", language),
        "language_probability": round(float(getattr(info, "language_probability", 0.0)), 3),
        "segments": len(cues),
    }
    return wrap_lines(_split_long(cues, max_chars), max_chars), report


def _transcribe_openai_whisper(whisper, audio, language, model, max_chars, prompt):
    engine = whisper.load_model(model)
    result = engine.transcribe(
        str(audio), language=language, initial_prompt=prompt or None, verbose=False
    )
    cues = [
        LyricLine(float(s["start"]), float(s["end"]), s["text"].strip())
        for s in result.get("segments", [])
        if s.get("text", "").strip()
    ]
    report = {
        "engine": f"openai-whisper {model}",
        "language": result.get("language", language),
        "segments": len(cues),
    }
    return wrap_lines(_split_long(cues, max_chars), max_chars), report


def _split_long(cues: list[LyricLine], max_chars: int) -> list[LyricLine]:
    """Split a long segment into several cues, sharing its span by word count.

    Whisper returns whole sentences; a lyric video wants short phrases. Without
    this, one cue covers eight seconds and four lines of text.
    """
    out: list[LyricLine] = []
    for cue in cues:
        text = cue.text.strip()
        if len(text) <= max_chars * MAX_LINES:
            out.append(cue)
            continue
        words = text.split()
        chunks: list[list[str]] = [[]]
        for word in words:
            candidate = " ".join([*chunks[-1], word])
            if len(candidate) > max_chars and chunks[-1]:
                chunks.append([word])
            else:
                chunks[-1].append(word)
        total = sum(len(c) for c in chunks) or 1
        cursor = cue.start
        for chunk in chunks:
            span = cue.duration * len(chunk) / total
            end = min(cue.end, cursor + span)
            if end - cursor >= 0.05:
                out.append(LyricLine(cursor, end, " ".join(chunk)))
            cursor = end
    return out


# ── file IO ──────────────────────────────────────────────────────────────
#: Extension -> writer.
WRITERS = {
    "srt": lambda cues, **kw: format_srt(cues),
    "vtt": lambda cues, **kw: format_vtt(cues),
    "lrc": lambda cues, **kw: format_lrc(cues, title=kw.get("title", "")),
    "ass": lambda cues, **kw: format_ass(cues, title=kw.get("title", "")),
    "txt": lambda cues, **kw: format_text(cues),
}


def load(path: str | Path) -> list[LyricLine]:
    """Read a cue file, picking the parser from the extension."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in (".srt", ".vtt"):
        return parse_srt(_strip_vtt_header(text) if suffix == ".vtt" else text)
    if suffix == ".lrc":
        return parse_lrc(text)
    raise LyricsError(
        f"do not know how to read {path.name}; expected .srt, .vtt or .lrc"
    )


def _strip_vtt_header(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[0].lstrip("﻿").startswith("WEBVTT"):
        lines = lines[1:]
    return "\n".join(lines)


def save(path: str | Path, cues: list[LyricLine], *, title: str = "") -> Path:
    path = Path(path)
    suffix = path.suffix.lower().lstrip(".")
    if suffix not in WRITERS:
        raise LyricsError(f"cannot write {suffix!r}; known: {', '.join(sorted(WRITERS))}")
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" so the CRLF that format_srt emits is written through verbatim
    # instead of being translated again on Windows.
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(WRITERS[suffix](cues, title=title))
    return path
