"""Tempo and beat analysis of an existing recording.

For *Tutti in Fila* this is the first real step: before any drum can be
transcribed we need to know what tempo the band actually played at.

**The tempo question is settled, and this module assumes the answer.** The
programmed drums are the product: they go to the gig as a click-locked backing
track and the band plays to them. The album mix is only ever the *source of the
pattern* — it is not mixed into anything and is not played live — so a take that
breathes is not a problem to be followed with a tempo map. Every song is
re-programmed to one fixed tempo. See docs/workflow.md.

That changes what this module has to report. Not "did they hold it, and should
you bend the grid" — that fork is closed — but two numbers that the settled
approach actually needs:

* **the tempo, precisely.** ``librosa`` reports tempo from a tempogram whose
  bins are ``60 * sr / (hop * k)`` for integer *k*, so near 117 BPM the only
  values it can *return* are about 112.4, 117.5 and 123.1 — five BPM apart. A
  bin centre is not a measurement, and a tempo that is 1% wrong slides three
  seconds over a five-minute song, which destroys the transcription. So the
  coarse estimate is refined against the recording itself in
  :func:`refine_tempo`, to about a hundredth of a BPM.
* **how far the recording wanders from that fixed grid**, in milliseconds, from
  :func:`pulse_wander`. Nobody has to act on it — the grid is fixed either way —
  but it predicts how much hand-correction the transcription will need: a hit
  lands on the wrong subdivision once the wander exceeds half a sixteenth, which
  is :attr:`TempoAnalysis.subdivision_budget_ms`.

The maths in ``refine_tempo`` / ``pulse_wander`` is plain numpy over an onset
envelope, so it is unit-testable without librosa installed.
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
    duration: float = 0.0
    #: ``(time_seconds, offset_ms)`` — where the recording's pulse sat against a
    #: perfectly even grid at ``bpm``, window by window, mean removed.
    wander: list[tuple[float, float]] = field(default_factory=list)

    @property
    def wander_pp(self) -> float:
        """Peak-to-peak wander in milliseconds."""
        if not self.wander:
            return 0.0
        offsets = [ms for _, ms in self.wander]
        return float(max(offsets) - min(offsets))

    @property
    def wander_rms(self) -> float:
        if not self.wander:
            return 0.0
        return float(np.std([ms for _, ms in self.wander]))

    @property
    def subdivision_budget_ms(self) -> float:
        """Half a sixteenth note: how far a hit can sit before it snaps wrong."""
        if self.bpm <= 0:
            return 0.0
        return 7500.0 / self.bpm

    @property
    def steady(self) -> bool:
        """Whether the whole take stays inside that budget."""
        return self.wander_pp <= self.subdivision_budget_ms

    @property
    def estimated_bars(self) -> int:
        """Bars of 4/4 from the first downbeat to the end of the audio."""
        if self.bpm <= 0 or self.duration <= 0:
            return 0
        bar = 4 * 60.0 / self.bpm
        return max(1, int(round((self.duration - self.downbeat_time) / bar)))

    def summary(self) -> str:
        budget = self.subdivision_budget_ms
        if self.steady:
            verdict = "holds the fixed grid — transcription should land clean"
        else:
            verdict = ("wanders past half a sixteenth — expect a few hits snapped to "
                       "the wrong subdivision")
        lines = [
            f"duration        {self.duration:8.2f} s",
            f"tempo           {self.bpm:8.2f} BPM  (rounded: {self.bpm_rounded:g})",
            f"beats found     {len(self.beat_times):8d}",
            f"first downbeat  {self.downbeat_time:8.3f} s",
            f"bars (4/4)      {self.estimated_bars:8d}",
            f"wander          {self.wander_pp:8.0f} ms peak-to-peak, "
            f"{self.wander_rms:.0f} ms rms  (budget {budget:.0f} ms)",
            f"verdict         {verdict}",
        ]
        if self.wander:
            lines.append("pulse against the fixed grid:")
            for start, ms in self.wander:
                lines.append(f"    {start:7.1f}s  {ms:+7.1f} ms")
        return "\n".join(lines)


# ── the two measurements, as pure numpy ──────────────────────────────────
def _comb(env: np.ndarray, times: np.ndarray, bpms: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Magnitude and phase of the onset envelope at each candidate pulse rate.

    This is a DFT evaluated at arbitrary frequencies rather than FFT bins, which
    is the whole point: we need a tempo resolution far finer than any bin grid.
    Batched so a fine search over a long song does not allocate a huge matrix.
    """
    env = np.asarray(env, dtype=float)
    env = env - env.mean()
    times = np.asarray(times, dtype=float)
    bpms = np.atleast_1d(np.asarray(bpms, dtype=float))
    mag = np.empty(len(bpms))
    phase = np.empty(len(bpms))
    for start in range(0, len(bpms), 64):
        chunk = bpms[start:start + 64, None] / 60.0
        z = (env[None, :] * np.exp(-2j * np.pi * chunk * times[None, :])).sum(axis=1)
        mag[start:start + 64] = np.abs(z)
        phase[start:start + 64] = np.angle(z)
    return mag, phase


def refine_tempo(
    env: np.ndarray,
    times: np.ndarray,
    coarse_bpm: float,
    *,
    span: float = 0.04,
    coarse_step: float = 0.01,
    fine_step: float = 0.0005,
) -> float:
    """Sharpen a coarse tempo estimate against the recording.

    *span* is fractional, and has to be wider than a tempogram bin is: the
    coarse estimate can be a whole bin out, which near 117 BPM is 5 BPM, so the
    default searches +-4%. Beyond that lies the next metrical level and the
    search would be free to lock onto half or double time.
    """
    times = np.asarray(times, dtype=float)
    if coarse_bpm <= 0 or len(times) < 8:
        return float(coarse_bpm)
    # Refining needs several pulses to average over; a fragment shorter than
    # about eight beats has nothing to say and the peak would be noise.
    if times[-1] - times[0] < 8 * 60.0 / coarse_bpm:
        return float(coarse_bpm)
    lo, hi = coarse_bpm * (1 - span), coarse_bpm * (1 + span)
    grid = np.arange(lo, hi, coarse_step)
    mag, _ = _comb(env, times, grid)
    peak = float(grid[int(np.argmax(mag))])
    fine = np.arange(peak - 2 * coarse_step, peak + 2 * coarse_step, fine_step)
    mag, _ = _comb(env, times, fine)
    return float(fine[int(np.argmax(mag))])


def pulse_wander(
    env: np.ndarray,
    times: np.ndarray,
    bpm: float,
    *,
    window: float = 20.0,
) -> list[tuple[float, float]]:
    """Where the recording's pulse sits against a perfectly even grid.

    One measurement per *window* seconds, in milliseconds, mean removed — so the
    numbers read as "the band was here relative to the click", not as absolute
    phase. **Positive means behind the grid**, which is worth stating because the
    phase of the transform runs the other way and the sign is easy to flip by
    accident. A click-locked record reads a few ms; a band playing without one
    reads tens.
    """
    times = np.asarray(times, dtype=float)
    env = np.asarray(env, dtype=float)
    if bpm <= 0 or len(times) < 8:
        return []
    period = 60.0 / bpm
    env = np.maximum(env - np.median(env), 0.0)
    mids: list[float] = []
    phases: list[float] = []
    start = float(times[0])
    while start + window <= float(times[-1]) + 1e-9:
        inside = (times >= start) & (times < start + window)
        if inside.sum() >= 8:
            z = (env[inside] * np.exp(-2j * np.pi * times[inside] / period)).sum()
            mids.append(start + window / 2)
            phases.append(float(np.angle(z)))
        start += window
    if not mids:
        return []
    # Wrapped into +-half a beat, deliberately **not** unwrapped. Unwrapping
    # invents whole-beat jumps out of a noisy window, and a whole beat of
    # "wander" is not a thing a band does — it is a measurement artifact that
    # would move a passage of the transcription a beat early. Half a beat is
    # also all any caller can act on.
    centred = np.array(phases)
    centred = centred - _circular_mean(centred)
    centred = (centred + np.pi) % (2 * np.pi) - np.pi
    offsets = -centred / (2 * np.pi) * period * 1000.0
    return [(float(m), float(ms)) for m, ms in zip(mids, offsets, strict=True)]


def _circular_mean(angles: np.ndarray) -> float:
    """Mean of angles, done on the circle so it survives the +-pi seam."""
    return float(np.angle(np.exp(1j * np.asarray(angles, dtype=float)).mean()))


def analyze_tempo(
    path,
    *,
    sample_rate: int = 22050,
    segment_seconds: float = 20.0,
    round_to: float = 0.01,
) -> TempoAnalysis:
    """Estimate the fixed tempo, the beat grid and the wander for an audio file."""
    librosa = require_module("librosa", "audio")
    samples, sr = load_mono(path, sample_rate)
    duration = len(samples) / sr

    onset_env = librosa.onset.onset_strength(y=samples, sr=sr, aggregate=np.median)
    times = librosa.times_like(onset_env, sr=sr)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, units="time")
    coarse = float(np.atleast_1d(tempo)[0])
    beat_times = [float(t) for t in np.atleast_1d(beats)]

    bpm = refine_tempo(onset_env, times, coarse)
    wander = pulse_wander(onset_env, times, bpm, window=segment_seconds)
    downbeat = _find_downbeat(samples, sr, beat_times, librosa)

    return TempoAnalysis(
        bpm=bpm,
        bpm_rounded=round(bpm / round_to) * round_to if round_to else bpm,
        beat_times=beat_times,
        downbeat_time=downbeat,
        duration=duration,
        wander=wander,
    )


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
