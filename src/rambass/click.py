"""Count-in and click rendering — two separate stems, never mixed into the base.

There are two different jobs here and they are deliberately two files:

**Sticks** (``sticks.wav``) — the count-in, and only the count-in. Drumsticks
counting the band in, the way the drummer would have. It is a musical part, not
a utility signal, so it is fine for it to reach the PA: the audience hearing
four stick clicks before a song is normal, and the band gets a real cue instead
of a beep.

**Click** (``click.wav``) — the click for the song itself, from bar 1 to the end.
This one is for rehearsal, and for tracking overdubs onto a finished base
(guitar doubles, choir vocals). It never goes to the PA.

The two do not overlap: sticks cover the count-in, click covers the song. So any
combination of the two can be enabled without anything doubling up, and each can
be muted on its own.

**Nothing here ever writes a click into the backing track.** A click baked into
the base is unremovable, and it is the one mistake in this area that cannot be
undone later.
"""

from __future__ import annotations

import numpy as np

from .audio import write_wav
from .timeline import Timeline

#: Tonal click pitches, for the rehearsal click track. High = beat 1.
DOWNBEAT_HZ = 1600.0
BEAT_HZ = 1000.0

#: Drumstick voicing. Stick-on-stick is a noise transient with a woody
#: resonance, not a pitch — two bandpassed noise bursts with a very fast decay
#: get close, and a brighter, slightly longer one marks beat 1.
STICK_LOW_HZ = 1900.0
STICK_HIGH_HZ = 4100.0
STICK_DECAY_MS = 9.0
STICK_LENGTH_MS = 55.0


# ── drumstick count-in ───────────────────────────────────────────────────
def _biquad_bandpass(
    signal: np.ndarray, sample_rate: int, frequency: float, q: float
) -> np.ndarray:
    """RBJ-cookbook bandpass (constant skirt gain), applied in place of scipy.

    Hand-rolled because scipy is an optional extra and a stick hit is only a
    couple of thousand samples — the Python loop costs nothing at this size and
    saves making the click depend on the audio extra.
    """
    w0 = 2.0 * np.pi * frequency / sample_rate
    alpha = np.sin(w0) / (2.0 * q)
    b0, b1, b2 = q * alpha, 0.0, -q * alpha
    a0, a1, a2 = 1.0 + alpha, -2.0 * np.cos(w0), 1.0 - alpha
    b0, b1, b2 = b0 / a0, b1 / a0, b2 / a0
    a1, a2 = a1 / a0, a2 / a0

    out = np.zeros_like(signal)
    x1 = x2 = y1 = y2 = 0.0
    for index, sample in enumerate(signal):
        value = b0 * sample + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, sample
        y2, y1 = y1, value
        out[index] = value
    return out


def stick_hit(
    sample_rate: int = 48000,
    *,
    accent: bool = False,
    seed: int = 0,
    length_ms: float = STICK_LENGTH_MS,
    decay_ms: float = STICK_DECAY_MS,
) -> np.ndarray:
    """Synthesise one drumstick click.

    Deterministic for a given *seed*, so a rendered count-in is reproducible;
    the seed is varied per hit so four clicks in a row are not four copies of
    the same sample, which is what makes a synthetic count-in sound mechanical.
    """
    rng = np.random.default_rng(1000 + seed)
    length = max(8, int(length_ms / 1000.0 * sample_rate))
    noise = rng.standard_normal(length).astype(np.float32)
    t = np.arange(length, dtype=np.float32) / sample_rate

    # Beat 1 is a little brighter and rings a touch longer, the way a drummer
    # naturally leans on it.
    low = STICK_LOW_HZ * (1.08 if accent else 1.0)
    high = STICK_HIGH_HZ * (1.05 if accent else 1.0)
    tau = (decay_ms * (1.15 if accent else 1.0)) / 1000.0

    body = _biquad_bandpass(noise, sample_rate, low, 1.1)
    crack = _biquad_bandpass(noise, sample_rate, high, 2.2)
    # The initial transient is what reads as "wood hitting wood" rather than
    # "filtered noise" — but it has to be band-limited, or its top end pushes
    # the whole hit into hi-hat territory.
    transient = _biquad_bandpass(noise, sample_rate, 6200.0, 0.7)
    transient *= np.exp(-t / 0.0008, dtype=np.float32)

    mixed = body + 0.45 * crack + 0.18 * transient
    # Roll off above the wood: real sticks have very little above ~9 kHz, and
    # what is up there just reads as hiss over a PA.
    mixed = _one_pole_lowpass(mixed, sample_rate, 9000.0)
    envelope = np.exp(-t / tau, dtype=np.float32)
    # A very short attack ramp stops the first sample being a click of its own.
    attack = min(length, max(2, int(0.0004 * sample_rate)))
    envelope[:attack] *= np.linspace(0.0, 1.0, attack, dtype=np.float32)

    hit = mixed * envelope
    peak = float(np.max(np.abs(hit)))
    return (hit / peak).astype(np.float32) if peak > 0 else hit


def render_sticks(
    timeline: Timeline,
    bars: int | None = None,
    *,
    sample_rate: int = 48000,
    level_db: float = -8.0,
    accent_downbeat: bool = True,
    seed: int = 0,
    tail_seconds: float = 0.15,
    sample: np.ndarray | None = None,
) -> np.ndarray:
    """Render the count-in as drumsticks. Covers the count-in only.

    Sample zero is the first stick. The result is exactly as long as the
    count-in (plus a short tail for the last hit to decay), so on a timeline it
    sits at 0 and the backing track starts where it ends.

    *sample* replaces the synthesised hit with a real recording, which will
    always sound better than a synth — a phone recording of the drummer's own
    sticks is enough. Pass a mono float array at *sample_rate*; the accent on
    beat 1 then comes from level alone.
    """
    count_in = bars if bars is not None else timeline.count_in_bars
    if count_in < 1:
        raise ValueError("a count-in needs at least one bar")

    total = sum(timeline.bar_length_seconds(max(bar, 1))
                for bar in range(1 - count_in, 1))
    buffer = np.zeros(int((total + tail_seconds) * sample_rate) + 1, dtype=np.float32)
    gain = _db(level_db)

    index = 0
    offset = 0.0
    for bar in range(1 - count_in, 1):
        sig = timeline.time_signature_at(max(bar, 1))
        beat_seconds = timeline.bar_length_seconds(max(bar, 1)) / sig[0]
        for beat in range(sig[0]):
            start = int((offset + beat * beat_seconds) * sample_rate)
            if start >= len(buffer):
                continue
            accent = accent_downbeat and beat == 0
            voice = sample if sample is not None else stick_hit(
                sample_rate, accent=accent, seed=seed * 97 + index
            )
            hit = voice * gain * (1.25 if accent else 1.0)
            end = min(len(buffer), start + len(hit))
            buffer[start:end] += hit[: end - start]
            index += 1
        offset += timeline.bar_length_seconds(max(bar, 1))

    return _guard(buffer)


# ── song click ───────────────────────────────────────────────────────────
def render_click(
    timeline: Timeline,
    bars: int,
    *,
    sample_rate: int = 48000,
    accent_downbeat: bool = True,
    click_ms: float = 28.0,
    level_db: float = -9.0,
    accent_db: float = -4.0,
    tail_seconds: float = 1.0,
) -> np.ndarray:
    """Render the click for the song proper: bar 1 to bar *bars* inclusive.

    Sample zero is bar 1 beat 1 — **not** the start of the count-in. The
    count-in is the sticks stem's job, and keeping the two non-overlapping is
    what lets either be muted without leaving a gap or doubling a beat.
    """
    if bars < 1:
        raise ValueError("bars must be at least 1")

    total = timeline.bar_beat_to_seconds(bars + 1, 1.0) + tail_seconds
    buffer = np.zeros(int(total * sample_rate) + 1, dtype=np.float32)

    for _bar, beat, seconds in timeline.beats_between(1, bars + 1):
        index = int(seconds * sample_rate)
        if index < 0 or index >= len(buffer):
            continue
        downbeat = beat == 1 and accent_downbeat
        tone = _tick(
            DOWNBEAT_HZ if downbeat else BEAT_HZ, click_ms, sample_rate
        ) * _db(accent_db if downbeat else level_db)
        end = min(len(buffer), index + len(tone))
        buffer[index:end] += tone[: end - index]

    return _guard(buffer)


# ── files ────────────────────────────────────────────────────────────────
def render_sticks_file(path, timeline: Timeline, bars: int | None = None, **kwargs):
    """Render and write the count-in stem. Returns the path."""
    sample_rate = kwargs.pop("sample_rate", 48000)
    samples = render_sticks(timeline, bars, sample_rate=sample_rate, **kwargs)
    return write_wav(path, samples, sample_rate, bit_depth=24)


def render_click_file(path, timeline: Timeline, bars: int, **kwargs):
    """Render and write the song click. Returns the path."""
    sample_rate = kwargs.pop("sample_rate", 48000)
    samples = render_click(timeline, bars, sample_rate=sample_rate, **kwargs)
    return write_wav(path, samples, sample_rate, bit_depth=24)


# ── helpers ──────────────────────────────────────────────────────────────
def _tick(frequency: float, click_ms: float, sample_rate: int) -> np.ndarray:
    """A short sine burst with a fast decay — cuts through a mix."""
    length = max(4, int(click_ms / 1000.0 * sample_rate))
    t = np.arange(length, dtype=np.float32) / sample_rate
    envelope = np.exp(-t * (4000.0 / click_ms))
    # A touch of second harmonic makes it audible through in-ear monitors.
    wave = np.sin(2 * np.pi * frequency * t) + 0.3 * np.sin(4 * np.pi * frequency * t)
    return (wave * envelope).astype(np.float32)


def _one_pole_lowpass(
    signal: np.ndarray, sample_rate: int, cutoff: float
) -> np.ndarray:
    """Gentle 6 dB/octave roll-off, enough to take the hiss off a noise burst."""
    alpha = float(np.exp(-2.0 * np.pi * cutoff / sample_rate))
    out = np.zeros_like(signal)
    previous = 0.0
    for index, sample in enumerate(signal):
        previous = (1.0 - alpha) * sample + alpha * previous
        out[index] = previous
    return out


def _guard(buffer: np.ndarray) -> np.ndarray:
    peak = float(np.max(np.abs(buffer))) if buffer.size else 0.0
    if peak > 0.99:
        buffer = buffer * (0.99 / peak)
    return buffer.astype(np.float32)


def _db(value: float) -> float:
    return float(10.0 ** (value / 20.0))
