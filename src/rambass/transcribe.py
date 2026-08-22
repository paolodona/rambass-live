"""Drum-stem -> MIDI transcription.

Approach: instead of detecting onsets once and then guessing what each one was,
we run onset detection **independently in several frequency bands**. A kick and
a hi-hat played together are one onset in the full-band signal but two clearly
separate onsets in a 20-120 Hz band and a 6-16 kHz band, which is exactly the
case that matters — it is most of a rock drum part.

What this does well:  kick, snare, hi-hat.
What it does badly:   toms, fills, ghost notes, ride patterns, and — measured,
                      not guessed — **telling a crash from a hat on dense
                      material**. The test in ``_classify_high_band`` asks
                      whether the 6-16 kHz band is still ringing 0.35 s after the
                      attack, which assumes it falls quiet in between. On Tutti
                      in Fila it never does: continuous cymbal wash made 67% of
                      high-band hits read as crashes (598 of them, against 126
                      kicks). Measuring the decay against the local floor instead
                      of against zero only brought it to 54%, so the ring test is
                      not rescuable on this material — it is the wrong question,
                      not a badly tuned threshold.

                      So for anything cymbal-heavy, pass ``--no-cymbals`` and get
                      a clean hat line, then place the crashes by hand. There are
                      only a handful per song and they sit at section boundaries,
                      which is the easiest edit in the whole pipeline.

So treat the output as a first pass that saves an hour of hand-entry per song,
not as a finished part. Open it in Reaper, play it against the original stem, and
fix the fills by hand — that is the workflow this repo is built around, and
``rambass drums transcribe --report`` prints the hit counts so an obviously wrong
result is caught before it reaches the DAW.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

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
#:
#: The kick's dominance was 0.30 until it was measured against a real one. On
#: Tutti in Fila that threshold threw away 686 of the 812 kick-band peaks — a
#: five-minute metal song reduced to 22 kicks a minute — because in a bright,
#: cymbal-heavy mix the 25-120 Hz band simply never holds 30% of the total
#: energy. 0.18 keeps a part with about the density the snare implies, and the
#: hits it adds land on the grid just as well as the ones it kept (measured: see
#: docs/drums.md).
DEFAULT_BANDS: tuple[Band, ...] = (
    Band("kick", 25, 120, delta=0.14, min_gap=0.055, dominance=0.18),
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
    #: Largest local correction applied when following the performance, in ms.
    followed_ms: float = 0.0
    #: Constant nudge applied to land the most hits on a subdivision, in ms.
    anchor_shift_ms: float = 0.0
    #: Whole-subdivision correction from where the backbeat sits, in ms.
    parity_shift_ms: float = 0.0
    #: Set when a parity shift looked likely but was not certain enough to apply.
    parity_note: str = ""

    def summary(self) -> str:
        lines = [f"duration {self.duration:.2f} s"]
        if self.followed_ms:
            lines.append(f"followed the take by up to {self.followed_ms:.0f} ms "
                         f"before placing hits on the fixed grid")
        if self.parity_shift_ms:
            lines.append(f"MOVED the anchor {self.parity_shift_ms:+.0f} ms — a whole "
                         f"subdivision — to put the kick and snare on beats")
        if self.anchor_shift_ms:
            lines.append(f"nudged the anchor {self.anchor_shift_ms:+.0f} ms to land the "
                         f"most hits on a subdivision")
        if self.parity_note:
            lines.append(self.parity_note)
        lines.append("hits per instrument:")
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
    follow: bool = True,
    follow_window: float = 10.0,
    snap_anchor: bool = True,
    velocity_floor: int = 45,
    velocity_ceiling: int = 122,
) -> tuple[DrumPerformance, TranscriptionReport]:
    """Transcribe a drum stem into a :class:`DrumPerformance`.

    *offset* is subtracted from every detected time, so pass the first-downbeat
    time from ``rambass analyze`` and the resulting MIDI starts at bar 1 beat 1
    instead of wherever the audio file happens to begin.

    *follow* corrects each detected time by however far the band had drifted
    from the fixed grid just there — see :func:`dewander`. The result is still
    metronomic; it is the reading of the performance that stops being wrong.
    Turn it off for a take that really was played to a click.

    *snap_anchor* then corrects a systematic error in *offset* itself — see
    :func:`best_anchor_shift`. A fitted downbeat can be tens of milliseconds out,
    which is enough to sit the whole song just off the grid.
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

    placement = times
    if follow and timeline is not None:
        from .analyze import pulse_wander

        envelope = librosa.onset.onset_strength(y=samples, sr=sr, hop_length=hop)
        wander = pulse_wander(
            envelope,
            librosa.times_like(envelope, sr=sr, hop_length=hop),
            timeline.bpm,
            window=follow_window,
        )
        if wander:
            placement = dewander(times, wander)
            report.followed_ms = float(max(abs(ms) for _, ms in wander))
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
            hits.append(Hit(instrument, float(placement[frame]) - offset, velocity))
            report.per_instrument[instrument] = report.per_instrument.get(instrument, 0) + 1

    timeline = timeline or Timeline(bpm=120.0)
    if snap_anchor and hits:
        shift = best_anchor_shift([hit.time for hit in hits], 60.0 / timeline.bpm / 4)
        if shift:
            hits = [replace(hit, time=hit.time + shift) for hit in hits]
            report.anchor_shift_ms = shift * 1000.0
        parity, advice = parity_advice(hits, 60.0 / timeline.bpm)
        if parity:
            hits = [replace(hit, time=hit.time + parity) for hit in hits]
            report.parity_shift_ms = parity * 1000.0
        report.parity_note = advice
    performance = DrumPerformance(hits=hits, timeline=timeline, name="Drums (transcribed)")
    return performance, report


#: One detection band per *isolated part stem*, for Stage 2 of
#: docs/drums-rebuild.md. These are not the mixture bands with the numbers
#: tweaked — they are a different problem. `dominance` exists only to stop a
#: snare firing the kick band in a mixture; on a stem there is nothing to cross-
#: trigger, so it is 0 throughout and the bands are wide enough to take the whole
#: instrument rather than the corner of it that was least contested.
PART_BANDS: dict[str, Band] = {
    "kick": Band("kick", 25, 250, delta=0.10, min_gap=0.055),
    "snare": Band("snare", 120, 8000, delta=0.12, min_gap=0.055),
    "toms": Band("tom_mid", 60, 900, delta=0.13, min_gap=0.070),
    "hihat": Band("hihat_closed", 1500, 16000, delta=0.12, min_gap=0.040),
    "cymbals": Band("crash", 800, 16000, delta=0.16, min_gap=0.200),
}


def transcribe_parts(
    parts: dict[str, object],
    *,
    timeline: Timeline,
    offset: float = 0.0,
    kit_mix=None,
    bands: dict[str, Band] | None = None,
    follow_window: float = 10.0,
    **kwargs,
) -> tuple[DrumPerformance, dict[str, TranscriptionReport]]:
    """Transcribe one isolated stem per instrument and merge the result.

    *parts* maps a name in :data:`PART_BANDS` to an audio path. *kit_mix* is the
    whole drum stem, and passing it matters more than it looks:

    **The timing correction has to be measured once, on the kit, and shared.**
    Run the five stems through :func:`transcribe_drums` separately and each one
    measures its own wander and picks its own anchor — so the kick ends up on a
    different grid from the snare, and the part is broken in a way that is
    extremely hard to see in a MIDI editor. Worse, a sparse stem is exactly where
    that measurement is least reliable: a ballad's tom stem has a handful of hits
    and nothing to fit a pulse to. So the correction comes from the mixture,
    which always has the most onsets, and every part gets the same one.
    """
    from .analyze import pulse_wander

    librosa = require_module("librosa", "audio")
    bands = bands or PART_BANDS

    wander: list[tuple[float, float]] = []
    if kit_mix is not None:
        samples, sr = load_mono(kit_mix, 22050)
        envelope = librosa.onset.onset_strength(y=samples, sr=sr, hop_length=256)
        wander = pulse_wander(
            envelope,
            librosa.times_like(envelope, sr=sr, hop_length=256),
            timeline.bpm,
            window=follow_window,
        )

    hits: list[Hit] = []
    reports: dict[str, TranscriptionReport] = {}
    for name, path in parts.items():
        band = bands.get(name)
        if band is None:
            continue
        performance, report = transcribe_drums(
            path,
            timeline=timeline,
            bands=(band,),
            offset=offset,
            detect_cymbals=False,
            follow=False,          # measured once on the kit, below
            snap_anchor=False,     # one anchor for the whole kit, below
            **kwargs,
        )
        part_hits = performance.hits
        if wander:
            moved = dewander([h.time + offset for h in part_hits], wander)
            part_hits = [
                replace(hit, time=float(t) - offset)
                for hit, t in zip(part_hits, moved, strict=True)
            ]
        hits.extend(part_hits)
        reports[name] = report

    merged = TranscriptionReport(
        duration=max((r.duration for r in reports.values()), default=0.0),
        per_instrument={k: v for r in reports.values() for k, v in r.per_instrument.items()},
        followed_ms=float(max((abs(ms) for _, ms in wander), default=0.0)),
    )
    if hits:
        shift = best_anchor_shift([h.time for h in hits], 60.0 / timeline.bpm / 4)
        if shift:
            hits = [replace(hit, time=hit.time + shift) for hit in hits]
            merged.anchor_shift_ms = shift * 1000.0
        parity, advice = parity_advice(hits, 60.0 / timeline.bpm)
        if parity:
            hits = [replace(hit, time=hit.time + parity) for hit in hits]
            merged.parity_shift_ms = parity * 1000.0
        merged.parity_note = advice
    reports["kit"] = merged
    hits.sort(key=lambda h: (h.time, h.instrument))
    return DrumPerformance(hits, timeline, "Drums (transcribed from parts)"), reports


def beat_parity_shift(
    hits: list[Hit],
    beat_seconds: float,
    *,
    subdivision: int = 4,
    tolerance: float = 0.040,
    margin: float = 3.0,
) -> float:
    """Whole-subdivision displacement of the anchor, from where the backbeat sits.

    :func:`best_anchor_shift` is bounded to half a subdivision so that it can
    never move a hit onto a different note. That bound leaves a hole: if the
    anchor is out by a *whole* subdivision, every hit is still perfectly on the
    grid — just on the wrong sixteenth — and no amount of sub-subdivision
    nudging can see it. The result is a part where the kick plays the "e" of
    every beat. It sounds wrong immediately and looks fine in the editor.

    Filling the hole needs one musical assumption, and only one: **kick and
    snare land on beats more often than between them.** That is not true of
    every bar, but over several hundred hits it is overwhelming — measured on
    Manlio, the true anchor puts 47% of kicks on a beat and the displaced one
    0.8%. So try every whole-subdivision shift, score it on kick and snare
    alone, and take the winner only if it wins by *margin* times, which keeps a
    genuinely syncopated song from being shoved around by a weak preference.

    Returns seconds to add to every hit time; 0.0 when the anchor is already
    right or the evidence is too thin to act on.
    """
    backbeat = [h.time for h in hits if h.instrument in ("kick", "snare")]
    if len(backbeat) < 20 or beat_seconds <= 0:
        return 0.0
    times = np.asarray(backbeat, dtype=float)
    step = beat_seconds / subdivision

    def on_beat(shift: float) -> float:
        off = ((times + shift + beat_seconds / 2) % beat_seconds) - beat_seconds / 2
        return float((np.abs(off) < tolerance).mean())

    scores = [(on_beat(k * step), k) for k in range(subdivision)]
    best_score, best_k = max(scores)
    if best_k == 0:
        return 0.0
    if best_score < margin * max(scores[0][0], 1e-6):
        # Suggestive but not conclusive. Refuse — a genuinely syncopated part
        # (metal kicks live on 16th offbeats) would be *given* this fault by a
        # shift, not cured of it. The caller reports the near miss so a human
        # can decide by ear, which is the only instrument that can.
        raise _ParityUnsure(best_k * step, best_score, scores[0][0])
    return best_k * step


class _ParityUnsure(Exception):
    """Raised when a parity shift looks likely but not certain."""

    def __init__(self, shift: float, best: float, current: float) -> None:
        super().__init__("parity unsure")
        self.shift, self.best, self.current = shift, best, current


def parity_advice(hits: list[Hit], beat_seconds: float, **kwargs) -> tuple[float, str]:
    """``(shift_to_apply, note_for_the_human)``. Never raises."""
    try:
        return beat_parity_shift(hits, beat_seconds, **kwargs), ""
    except _ParityUnsure as unsure:
        return 0.0, (
            f"NOT shifted, but check by ear: moving the anchor "
            f"{unsure.shift * 1000:+.0f} ms would put "
            f"{100 * unsure.best:.0f}% of kick/snare on a beat instead of "
            f"{100 * unsure.current:.0f}% — decide whether this part is really "
            f"that syncopated (see transcribe.beat_parity_shift)"
        )


def dewander(times, wander) -> np.ndarray:
    """Pull detected times onto the grid the band was *actually* playing to.

    This is the step that makes a fixed-tempo transcription work on a take that
    breathes, and it is worth being precise about what it does and does not do.
    It does **not** put a tempo map in the manifest and it does not bend the
    click: the output is still placed against the one fixed tempo, so the
    programmed drums stay metronomic, which is the whole point of them. It only
    stops the *measurement* from fighting the drummer.

    Without it, a hit played 60 ms behind the grid is snapped to the next
    sixteenth and the pattern comes out wrong. Measured on Tutti in Fila:
    detected hits sat on a subdivision 30% of the time against the fixed grid —
    *below* the 39% you would get from random noise — and 62% once the local
    wander was removed.
    """
    t = np.asarray(times, dtype=float)
    if not len(wander):
        return t
    at = np.array([w[0] for w in wander], dtype=float)
    offsets = np.array([w[1] for w in wander], dtype=float)
    return t - np.interp(t, at, offsets) / 1000.0


def best_anchor_shift(
    times,
    subdivision: float,
    *,
    tolerance: float = 0.025,
    step: float = 0.001,
) -> float:
    """The small nudge that puts the most hits on a subdivision. Add it to *times*.

    The downbeat anchor comes from a fitted grid, and a fit is allowed to be a
    few tens of milliseconds out — which is enough to sit every hit in the song
    just off the grid, so that quantising rounds half of them the wrong way.
    Rather than hand-tuning ``--offset`` per song, measure it: sweep the shift,
    keep the one where most hits land on a subdivision.

    Bounded to +-half a subdivision **by construction**, so it can only ever fix
    the anchor. It cannot move a hit onto a different note, which is the one
    thing an automatic timing correction must never be free to do.

    Measured on Tutti in Fila: the anchor was 31 ms late, and correcting it took
    hits-on-a-sixteenth from 40% (chance is 39%) to 62%.
    """
    times = np.asarray(times, dtype=float)
    if len(times) < 8 or subdivision <= 0:
        return 0.0
    shifts = np.arange(-subdivision / 2, subdivision / 2, step)
    off = ((times[None, :] + shifts[:, None] + subdivision / 2) % subdivision) - subdivision / 2
    scores = (np.abs(off) < tolerance).mean(axis=1)
    # Every shift within *tolerance* of the right one scores identically, so the
    # best score is a plateau, not a spike. Taking the first of those biases the
    # answer early by up to the tolerance — take the middle instead, and do it on
    # the circle because the plateau can straddle +-half a subdivision.
    winners = shifts[scores >= scores.max() - 1e-12]
    angle = np.angle(np.exp(2j * np.pi * winners / subdivision).mean())
    return float(angle / (2 * np.pi) * subdivision)


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
