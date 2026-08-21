"""Bar/beat <-> seconds conversion.

Everything in this repo is positioned in **bars and beats**, never in raw
seconds: a section marker, a GX-100 patch change or a lyric cue written as
"bar 33" survives a decision to nudge the song from 128 to 130 BPM, which is
exactly the kind of change that happens while a backing track is being built.

Bar numbering is 1-based and musician-friendly: bar 1 beat 1 is the downbeat of
the song proper. The click count-in lives *before* bar 1, at negative time, so
the rendered audio simply starts earlier than the musical zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction


class TimelineError(ValueError):
    """Raised for malformed tempo maps."""


def parse_time_signature(text: str | tuple[int, int]) -> tuple[int, int]:
    """``"7/8"`` -> ``(7, 8)``."""
    if isinstance(text, tuple):
        return int(text[0]), int(text[1])
    try:
        num, den = str(text).split("/")
        sig = (int(num), int(den))
    except ValueError as exc:  # noqa: B904 - message is the point
        raise TimelineError(f"bad time signature {text!r}, expected e.g. '4/4'") from exc
    if sig[0] < 1 or sig[1] not in (1, 2, 4, 8, 16, 32):
        raise TimelineError(f"implausible time signature {text!r}")
    return sig


@dataclass(frozen=True)
class TempoChange:
    """A tempo and/or time-signature change, anchored to the start of a bar."""

    bar: int
    bpm: float | None = None
    time_signature: tuple[int, int] | None = None

    @classmethod
    def from_dict(cls, data: dict) -> TempoChange:
        sig = data.get("time_signature") or data.get("sig")
        return cls(
            bar=int(data["bar"]),
            bpm=float(data["bpm"]) if data.get("bpm") is not None else None,
            time_signature=parse_time_signature(sig) if sig else None,
        )

    def to_dict(self) -> dict:
        out: dict = {"bar": self.bar}
        if self.bpm is not None:
            out["bpm"] = self.bpm
        if self.time_signature is not None:
            out["time_signature"] = f"{self.time_signature[0]}/{self.time_signature[1]}"
        return out


@dataclass(frozen=True)
class _Segment:
    """A run of bars with constant tempo and metre."""

    start_bar: int
    start_seconds: float
    bpm: float
    sig: tuple[int, int]

    @property
    def seconds_per_beat(self) -> float:
        """Seconds per *notated* beat (the denominator of the time signature).

        BPM is always quarter-note based, as in Reaper and in every DAW, so a
        6/8 bar at 100 BPM is six eighth-notes of 0.3 s, not six of 0.6 s.
        """
        return (60.0 / self.bpm) * (4.0 / self.sig[1])

    @property
    def seconds_per_bar(self) -> float:
        return self.seconds_per_beat * self.sig[0]


@dataclass
class Timeline:
    """A tempo map: constant BPM by default, with optional changes."""

    bpm: float = 120.0
    time_signature: tuple[int, int] = (4, 4)
    changes: list[TempoChange] = field(default_factory=list)
    count_in_bars: int = 0

    def __post_init__(self) -> None:
        if self.bpm <= 0:
            raise TimelineError(f"bpm must be positive, got {self.bpm}")
        self.time_signature = parse_time_signature(self.time_signature)
        self.changes = sorted(self.changes, key=lambda c: c.bar)
        if self.changes and self.changes[0].bar < 1:
            raise TimelineError("tempo changes must be at bar 1 or later")
        self._segments = self._build_segments()

    # ── construction ─────────────────────────────────────────────────────
    def _build_segments(self) -> list[_Segment]:
        segments = [_Segment(1, 0.0, self.bpm, self.time_signature)]
        for change in self.changes:
            current = segments[-1]
            if change.bar <= current.start_bar and len(segments) > 1:
                raise TimelineError(f"duplicate tempo change at bar {change.bar}")
            bpm = change.bpm if change.bpm is not None else current.bpm
            sig = change.time_signature or current.sig
            if change.bar == 1:
                # A change written at bar 1 simply redefines the opening tempo.
                segments[-1] = _Segment(1, 0.0, bpm, sig)
                continue
            bars_in = change.bar - current.start_bar
            start = current.start_seconds + bars_in * current.seconds_per_bar
            segments.append(_Segment(change.bar, start, bpm, sig))
        return segments

    def _segment_for_bar(self, bar: int) -> _Segment:
        chosen = self._segments[0]
        for segment in self._segments:
            if segment.start_bar <= bar:
                chosen = segment
            else:
                break
        return chosen

    # ── queries ──────────────────────────────────────────────────────────
    def bar_beat_to_seconds(self, bar: int, beat: float = 1.0) -> float:
        """Musical position -> seconds from the *musical* zero (bar 1 beat 1).

        Bars before 1 are allowed and return negative times, which is how the
        count-in is expressed.
        """
        if beat < 1:
            raise TimelineError(f"beats are 1-based, got beat {beat}")
        segment = self._segment_for_bar(bar)
        bars_in = bar - segment.start_bar
        return (
            segment.start_seconds
            + bars_in * segment.seconds_per_bar
            + (beat - 1.0) * segment.seconds_per_beat
        )

    def audio_time(self, bar: int, beat: float = 1.0) -> float:
        """Position in the rendered audio file, where t=0 is the first sample.

        This is ``bar_beat_to_seconds`` shifted by the count-in, so it is what
        you feed to Reaper, ffmpeg or a subtitle file.
        """
        return self.bar_beat_to_seconds(bar, beat) + self.count_in_seconds

    @property
    def count_in_seconds(self) -> float:
        if self.count_in_bars <= 0:
            return 0.0
        return -self.bar_beat_to_seconds(1 - self.count_in_bars, 1.0)

    def seconds_to_bar_beat(self, seconds: float) -> tuple[int, float]:
        """Inverse of :meth:`bar_beat_to_seconds` (musical zero based)."""
        segment = self._segments[0]
        for candidate in self._segments:
            if candidate.start_seconds <= seconds + 1e-9:
                segment = candidate
            else:
                break
        offset = seconds - segment.start_seconds
        bars = offset / segment.seconds_per_bar
        bar_index = int(bars // 1)
        beat = (bars - bar_index) * segment.sig[0] + 1.0
        return segment.start_bar + bar_index, beat

    def beats_between(self, start_bar: int, end_bar: int) -> list[tuple[int, int, float]]:
        """Every beat in ``[start_bar, end_bar)`` as ``(bar, beat, seconds)``.

        Used to render the click and to place accents on beat 1.
        """
        out: list[tuple[int, int, float]] = []
        for bar in range(start_bar, end_bar):
            sig = self._segment_for_bar(max(bar, 1)).sig
            for beat in range(1, sig[0] + 1):
                out.append((bar, beat, self.bar_beat_to_seconds(bar, beat)))
        return out

    def bar_length_seconds(self, bar: int) -> float:
        return self._segment_for_bar(bar).seconds_per_bar

    def time_signature_at(self, bar: int) -> tuple[int, int]:
        return self._segment_for_bar(bar).sig

    def bpm_at(self, bar: int) -> float:
        return self._segment_for_bar(bar).bpm

    def bars_for_duration(self, seconds: float) -> int:
        """How many whole bars fit in *seconds* — used to guess song length."""
        bar, _ = self.seconds_to_bar_beat(seconds)
        return max(bar, 1)

    def grid_seconds(self, subdivision: int, start_bar: int, end_bar: int) -> list[float]:
        """Every grid line at *subdivision* per bar-beat, for quantising.

        ``subdivision=4`` gives 16th notes in 4/4 (4 per quarter-note beat).
        """
        if subdivision < 1:
            raise TimelineError("subdivision must be >= 1")
        out: list[float] = []
        for bar in range(start_bar, end_bar):
            segment = self._segment_for_bar(max(bar, 1))
            step = segment.seconds_per_beat / subdivision
            bar_start = self.bar_beat_to_seconds(bar, 1.0)
            for tick in range(segment.sig[0] * subdivision):
                out.append(bar_start + tick * step)
        return out

    # ── MIDI helpers ─────────────────────────────────────────────────────
    def as_midi_tempo_map(self) -> list[tuple[Fraction, float, tuple[int, int]]]:
        """``(beats_from_zero, bpm, time_signature)`` for each tempo segment.

        Positions are in quarter notes, which is what MIDI ticks count.
        """
        out: list[tuple[Fraction, float, tuple[int, int]]] = []
        quarter_pos = Fraction(0)
        previous: _Segment | None = None
        for segment in self._segments:
            if previous is not None:
                bars = segment.start_bar - previous.start_bar
                quarter_pos += Fraction(bars * previous.sig[0] * 4, previous.sig[1])
            out.append((quarter_pos, segment.bpm, segment.sig))
            previous = segment
        return out

    # ── quarter-note conversions (for MIDI ticks) ────────────────────────
    def _segment_for_seconds(self, seconds: float) -> _Segment:
        chosen = self._segments[0]
        for candidate in self._segments:
            if candidate.start_seconds <= seconds + 1e-9:
                chosen = candidate
            else:
                break
        return chosen

    def _segment_start_quarters(self, segment: _Segment) -> float:
        quarters = 0.0
        previous: _Segment | None = None
        for current in self._segments:
            if previous is not None:
                bars = current.start_bar - previous.start_bar
                quarters += bars * previous.sig[0] * 4.0 / previous.sig[1]
            if current is segment:
                return quarters
            previous = current
        return quarters

    def seconds_to_quarters(self, seconds: float) -> float:
        """Seconds from the musical zero -> quarter notes from the musical zero."""
        segment = self._segment_for_seconds(seconds)
        offset = seconds - segment.start_seconds
        return self._segment_start_quarters(segment) + offset * segment.bpm / 60.0

    def quarters_to_seconds(self, quarters: float) -> float:
        """Inverse of :meth:`seconds_to_quarters`."""
        chosen = self._segments[0]
        chosen_start = 0.0
        for segment in self._segments:
            start = self._segment_start_quarters(segment)
            if start <= quarters + 1e-9:
                chosen, chosen_start = segment, start
            else:
                break
        return chosen.start_seconds + (quarters - chosen_start) * 60.0 / chosen.bpm

    def total_quarters(self, bars: int) -> float:
        """Length of *bars* bars, in quarter notes."""
        return self.seconds_to_quarters(self.bar_beat_to_seconds(bars + 1, 1.0))
