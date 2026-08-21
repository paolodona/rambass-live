"""Click-track rendering.

The click is the spine of the whole show: the drummer is a laptop now, so the
click is what the band locks to, and the count-in is what tells them a song is
about to start. It is generated from the same :class:`~rambass.timeline.Timeline`
as the drum MIDI and the section markers, so the three can never disagree.
"""

from __future__ import annotations

import numpy as np

from .audio import write_wav
from .timeline import Timeline

#: Two easily-distinguished woodblock-ish pitches. High = beat 1.
DOWNBEAT_HZ = 1600.0
BEAT_HZ = 1000.0
COUNT_IN_HZ = 2000.0


def render_click(
    timeline: Timeline,
    bars: int,
    *,
    sample_rate: int = 48000,
    count_in_bars: int | None = None,
    accent_downbeat: bool = True,
    click_ms: float = 28.0,
    level_db: float = -9.0,
    accent_db: float = -4.0,
    tail_seconds: float = 1.0,
) -> np.ndarray:
    """Render a mono click for *bars* bars plus the count-in.

    Sample zero is the first sample of the count-in, matching
    :meth:`Timeline.audio_time`, so the click WAV and everything else in the
    project share one time origin.
    """
    if bars < 1:
        raise ValueError("bars must be at least 1")
    count_in = timeline.count_in_bars if count_in_bars is None else count_in_bars
    timeline = Timeline(
        bpm=timeline.bpm,
        time_signature=timeline.time_signature,
        changes=list(timeline.changes),
        count_in_bars=count_in,
    )

    offset = timeline.count_in_seconds
    total = offset + timeline.bar_beat_to_seconds(bars + 1, 1.0) + tail_seconds
    buffer = np.zeros(int(total * sample_rate) + 1, dtype=np.float32)

    beats = timeline.beats_between(1 - count_in, bars + 1)
    for bar, beat, seconds in beats:
        position = seconds + offset
        index = int(position * sample_rate)
        if index < 0 or index >= len(buffer):
            continue
        is_count_in = bar < 1
        is_downbeat = beat == 1
        if is_count_in:
            frequency = COUNT_IN_HZ
            gain = _db(accent_db)
        elif is_downbeat and accent_downbeat:
            frequency = DOWNBEAT_HZ
            gain = _db(accent_db)
        else:
            frequency = BEAT_HZ
            gain = _db(level_db)
        tone = _tick(frequency, click_ms, sample_rate) * gain
        end = min(len(buffer), index + len(tone))
        buffer[index:end] += tone[: end - index]

    peak = float(np.max(np.abs(buffer))) if buffer.size else 0.0
    if peak > 0.99:
        buffer *= 0.99 / peak
    return buffer


def render_click_file(
    path,
    timeline: Timeline,
    bars: int,
    *,
    sample_rate: int = 48000,
    **kwargs,
):
    """Render and write a click WAV. Returns the path."""
    samples = render_click(timeline, bars, sample_rate=sample_rate, **kwargs)
    return write_wav(path, samples, sample_rate, bit_depth=24)


def _tick(frequency: float, click_ms: float, sample_rate: int) -> np.ndarray:
    """A short sine burst with a fast exponential decay — cuts through a mix."""
    length = max(4, int(click_ms / 1000.0 * sample_rate))
    t = np.arange(length, dtype=np.float32) / sample_rate
    envelope = np.exp(-t * (4000.0 / click_ms))
    # A touch of second harmonic makes it audible through in-ear monitors.
    wave = np.sin(2 * np.pi * frequency * t) + 0.3 * np.sin(4 * np.pi * frequency * t)
    return (wave * envelope).astype(np.float32)


def _db(value: float) -> float:
    return float(10.0 ** (value / 20.0))
