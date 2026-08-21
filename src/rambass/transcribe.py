"""Drum-stem -> MIDI transcription.

Approach: instead of detecting onsets once and then guessing what each one was,
we run onset detection **independently in several frequency bands**. A kick and
a hi-hat played together are one onset in the full-band signal but two clearly
separate onsets in a 20-120 Hz band and a 6-16 kHz band, which is exactly the
case that matters — it is most of a rock drum part.

What this does well:  kick, snare, hi-hat, and cymbal crashes.
What it does badly:   toms, fills, ghost notes, ride patterns, anything with the
                      hats and a crash ringing at once.

So treat the output as a first pass that saves an hour of hand-entry per song,
not as a finished part. Open it in Reaper, play it against the original stem, and
fix the fills by hand — that is the workflow this repo is built around, and
``rambass drums transcribe --report`` prints the hit counts so an obviously wrong
result is caught before it reaches the DAW.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .audio import load_mono, require_module
from .midiio import DrumPerformance, Hit
from .timeline import Timeline


@dataclass(frozen=True)
class Band:
    """One detection band."""

    instrument: str
    low_hz: float
    high_hz: float
    #: Peak-picking sensitivity — higher means fewer, more confident hits.
    delta: float = 0.12
    #: Minimum gap between two hits of this instrument, in seconds.
    min_gap: float = 0.05
    #: Reject an onset unless this band holds at least this share of the
    #: total energy at that instant. Stops a snare from also firing the kick.
    dominance: float = 0.0


#: Tuned against dense mid-tempo rock, which is what both albums are.
DEFAULT_BANDS: tuple[Band, ...] = (
    Band("kick", 25, 120, delta=0.14, min_gap=0.055, dominance=0.30),
    Band("snare", 170, 1200, delta=0.16, min_gap=0.055, dominance=0.22),
    Band("hihat_closed", 6000, 16000, delta=0.13, min_gap=0.040, dominance=0.10),
)

#: A high-band hit whose energy is still ringing this long after the attack is a
#: crash, not a hat. Measured as a fraction of the attack energy.
CRASH_SUSTAIN_SECONDS = 0.35
CRASH_SUSTAIN_RATIO = 0.45
OPEN_HAT_SUSTAIN_RATIO = 0.22


@dataclass
class TranscriptionReport:
    """Diagnostics printed after a transcription run."""

    per_instrument: dict[str, int] = field(default_factory=dict)
    duration: float = 0.0
    dropped_quiet: int = 0
    dropped_not_dominant: int = 0

    def summary(self) -> str:
        lines = [f"duration {self.duration:.2f} s", "hits per instrument:"]
        for instrument, count in sorted(self.per_instrument.items(), key=lambda kv: -kv[1]):
            per_minute = count / max(self.duration / 60.0, 1e-9)
            lines.append(f"  {instrument:<16} {count:6d}   ({per_minute:6.1f}/min)")
        lines.append(f"rejected: {self.dropped_quiet} too quiet, "
                     f"{self.dropped_not_dominant} not dominant in band")
        if not self.per_instrument:
            lines.append("  nothing found — is this really a drum stem?")
        return "\n".join(lines)


def transcribe_drums(
    path,
    *,
    timeline: Timeline | None = None,
    sample_rate: int = 22050,
    bands: tuple[Band, ...] = DEFAULT_BANDS,
    offset: float = 0.0,
    detect_cymbals: bool = True,
    velocity_floor: int = 45,
    velocity_ceiling: int = 122,
) -> tuple[DrumPerformance, TranscriptionReport]:
    """Transcribe a drum stem into a :class:`DrumPerformance`.

    *offset* is subtracted from every detected time, so pass the first-downbeat
    time from ``rambass analyze`` and the resulting MIDI starts at bar 1 beat 1
    instead of wherever the audio file happens to begin.
    """
    librosa = require_module("librosa", "audio")
    samples, sr = load_mono(path, sample_rate)
    duration = len(samples) / sr

    hop = 256
    spectrogram = np.abs(librosa.stft(samples, n_fft=2048, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    times = librosa.frames_to_time(np.arange(spectrogram.shape[1]), sr=sr, hop_length=hop)
    total_energy = spectrogram.sum(axis=0) + 1e-9

    report = TranscriptionReport(duration=duration)
    hits: list[Hit] = []

    for band in bands:
        mask = (freqs >= band.low_hz) & (freqs <= band.high_hz)
        if not mask.any():
            continue
        band_energy = spectrogram[mask].sum(axis=0)
        flux = _spectral_flux(spectrogram[mask])
        if flux.max() <= 0:
            continue
        flux = flux / flux.max()

        peaks = librosa.util.peak_pick(
            flux,
            pre_max=int(0.03 * sr / hop),
            post_max=int(0.03 * sr / hop),
            pre_avg=int(0.10 * sr / hop),
            post_avg=int(0.10 * sr / hop),
            delta=band.delta,
            wait=max(1, int(band.min_gap * sr / hop)),
        )

        for frame in np.atleast_1d(peaks):
            frame = int(frame)
            if frame >= len(times):
                continue
            strength = float(flux[frame])
            if strength < band.delta * 0.5:
                report.dropped_quiet += 1
                continue
            share = float(band_energy[frame] / total_energy[frame])
            if share < band.dominance:
                report.dropped_not_dominant += 1
                continue

            instrument = band.instrument
            if detect_cymbals and band.instrument == "hihat_closed":
                instrument = _classify_high_band(
                    band_energy, times, frame, sr, hop
                )

            velocity = int(round(
                velocity_floor + strength * (velocity_ceiling - velocity_floor)
            ))
            hits.append(Hit(instrument, float(times[frame]) - offset, velocity))
            report.per_instrument[instrument] = report.per_instrument.get(instrument, 0) + 1

    timeline = timeline or Timeline(bpm=120.0)
    performance = DrumPerformance(hits=hits, timeline=timeline, name="Drums (transcribed)")
    return performance, report


def _spectral_flux(band: np.ndarray) -> np.ndarray:
    """Half-wave-rectified energy increase per frame — a plain onset function."""
    energy = band.sum(axis=0)
    energy = np.log1p(energy)
    flux = np.diff(energy, prepend=energy[:1])
    return np.maximum(flux, 0.0)


def _classify_high_band(
    band_energy: np.ndarray,
    times: np.ndarray,
    frame: int,
    sr: int,
    hop: int,
) -> str:
    """Tell a closed hat from an open hat from a crash by how long it rings."""
    attack = float(band_energy[frame]) + 1e-9
    later_frame = frame + int(CRASH_SUSTAIN_SECONDS * sr / hop)
    if later_frame >= len(band_energy):
        return "hihat_closed"
    tail = float(band_energy[later_frame])
    ratio = tail / attack
    if ratio >= CRASH_SUSTAIN_RATIO:
        return "crash"
    if ratio >= OPEN_HAT_SUSTAIN_RATIO:
        return "hihat_open"
    return "hihat_closed"
