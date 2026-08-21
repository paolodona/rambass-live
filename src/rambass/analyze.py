"""Tempo and beat analysis of an existing recording.

For *Tutti in Fila* this is the first real step: before any drum can be
transcribed we need to know what tempo the band actually played at, and whether
they held it. The answer decides the whole approach for that song:

* steady (drift under ~1 BPM) — pick the rounded tempo, quantise hard, the
  original recording will still line up with the new grid;
* drifting — either follow the performance with a tempo map, or accept that the
  original take will not line up and treat the transcription as a starting point
  for a re-programmed part played to a fixed click.

``rambass analyze`` prints both numbers so the choice is made with eyes open
rather than discovered three songs into the rehearsal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .audio import load_mono, require_module


@dataclass
class TempoAnalysis:
    """What we learned about a recording's pulse."""

    bpm: float
    bpm_rounded: float
    beat_times: list[float] = field(default_factory=list)
    downbeat_time: float = 0.0
    drift_bpm: float = 0.0
    steady: bool = True
    duration: float = 0.0
    segment_bpms: list[tuple[float, float]] = field(default_factory=list)

    @property
    def estimated_bars(self) -> int:
        if not self.beat_times:
            return 0
        return max(1, int(len(self.beat_times) // 4))

    def summary(self) -> str:
        verdict = "steady — safe to quantise to a fixed grid" if self.steady else (
            "drifts — decide between a tempo map and re-programming to a click"
        )
        lines = [
            f"duration        {self.duration:8.2f} s",
            f"tempo           {self.bpm:8.2f} BPM  (rounded: {self.bpm_rounded:g})",
            f"beats found     {len(self.beat_times):8d}",
            f"first downbeat  {self.downbeat_time:8.3f} s",
            f"drift           {self.drift_bpm:8.2f} BPM peak-to-peak",
            f"verdict         {verdict}",
        ]
        if self.segment_bpms:
            lines.append("tempo over time:")
            for start, bpm in self.segment_bpms:
                lines.append(f"    {start:7.1f}s  {bpm:7.2f} BPM")
        return "\n".join(lines)


def analyze_tempo(
    path,
    *,
    sample_rate: int = 22050,
    segment_seconds: float = 20.0,
    round_to: float = 0.5,
) -> TempoAnalysis:
    """Estimate tempo, beat grid and drift for an audio file."""
    librosa = require_module("librosa", "audio")
    samples, sr = load_mono(path, sample_rate)
    duration = len(samples) / sr

    onset_env = librosa.onset.onset_strength(y=samples, sr=sr, aggregate=np.median)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, units="time")
    tempo = float(np.atleast_1d(tempo)[0])
    beat_times = [float(t) for t in np.atleast_1d(beats)]

    # Drift: compare tempo estimated independently over successive windows.
    segment_bpms: list[tuple[float, float]] = []
    window = int(segment_seconds * sr)
    if window > 0 and len(samples) > window:
        for start in range(0, len(samples) - window // 2, window):
            chunk = samples[start:start + window]
            if len(chunk) < sr * 4:
                break
            chunk_env = librosa.onset.onset_strength(y=chunk, sr=sr, aggregate=np.median)
            chunk_tempo = librosa.feature.tempo(onset_envelope=chunk_env, sr=sr)
            segment_bpms.append((start / sr, float(np.atleast_1d(chunk_tempo)[0])))

    drift = 0.0
    if len(segment_bpms) > 1:
        values = _fold_octaves([bpm for _, bpm in segment_bpms], tempo)
        drift = float(max(values) - min(values))

    downbeat = _find_downbeat(samples, sr, beat_times, librosa)

    return TempoAnalysis(
        bpm=tempo,
        bpm_rounded=round(tempo / round_to) * round_to,
        beat_times=beat_times,
        downbeat_time=downbeat,
        drift_bpm=drift,
        steady=drift <= 1.0,
        duration=duration,
        segment_bpms=segment_bpms,
    )


def _fold_octaves(values: list[float], reference: float) -> list[float]:
    """Beat trackers happily report half or double tempo; fold those in.

    Without this, one window reporting 65 BPM against a 130 BPM song looks like
    catastrophic drift when the performance is actually rock solid.
    """
    folded: list[float] = []
    for value in values:
        best = value
        for factor in (0.5, 1.0, 2.0):
            candidate = value * factor
            if abs(candidate - reference) < abs(best - reference):
                best = candidate
        folded.append(best)
    return folded


def _find_downbeat(samples: np.ndarray, sr: int, beat_times: list[float], librosa) -> float:
    """Guess which beat is beat 1 by looking for the strongest low-frequency hit.

    A crude but effective proxy: in the music we play, bar one starts on the
    kick. Only the first eight beats are considered, which is where an intro
    downbeat will be.
    """
    if not beat_times:
        return 0.0
    low = librosa.onset.onset_strength(
        y=samples, sr=sr, fmax=200, aggregate=np.median
    )
    times = librosa.times_like(low, sr=sr)
    best_time = beat_times[0]
    best_strength = -1.0
    for beat in beat_times[:8]:
        index = int(np.argmin(np.abs(times - beat)))
        window = low[max(0, index - 1):index + 2]
        strength = float(window.max()) if window.size else 0.0
        if strength > best_strength:
            best_strength, best_time = strength, beat
    return float(best_time)
