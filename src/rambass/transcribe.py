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
    #: "hat" runs the open/closed test on an isolated hi-hat stem.
    articulation: str = ""


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

#: A snare-stem hit with less than this share of its energy in the shell's own
#: band is a **side-stick** — a stick laid across the rim, tip on the head. The
#: stroke never reaches the shell, so there is no body to it at all.
#:
#: Measured on Manlio at the 202 snare-stem detections, classified against the
#: section list: the 24 verse backbeats sit at a median 0.21 and the 69 real
#: snare backbeats at 0.57. 0.35 is between them, not on either.
SIDESTICK_BODY_SHARE = 0.35
#: ...and it must also be **louder in the hi-hat stem than in the snare stem**,
#: which is a thing a snare cannot be: all of a rim click's energy is a
#: high-frequency transient, so a separator hands most of it to the hat.
#:
#: Same measurement: -17.8 dB median for the verse backbeats against +28.6 dB
#: for the real ones. This is a sign flip across a 46 dB gap rather than a tuned
#: threshold, which is why it is 0 and not a number.
SIDESTICK_HAT_MARGIN_DB = 0.0
#: The window both features are measured over, relative to the detected attack.
SIDESTICK_WINDOW = (0.015, 0.045)


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
    #: Where bar 1 was measured to be, and how convincingly.
    anchor_seconds: float = 0.0
    anchor_report: dict = field(default_factory=dict)
    #: analyze.grid_confidence on the finished part — the coarse-grid check.
    confidence: dict = field(default_factory=dict)
    #: Set when crash bleed was removed from the other parts.
    bleed_note: str = ""
    #: Set when cymbal-stem runs were relabelled as open hi-hats.
    cymbal_note: str = ""
    #: Set when the song's own pattern resolved kick/snare collisions.
    collision_note: str = ""
    #: The per-slot census resolve_collisions worked from, for --report.
    collision_report: dict = field(default_factory=dict)
    #: Set when hits needing a third hand were dropped.
    playability_note: str = ""
    #: Set when snare-stem hits were renamed as side-sticks.
    sidestick_note: str = ""

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
        if self.sidestick_note:
            lines.append(self.sidestick_note)
        if self.bleed_note:
            lines.append(self.bleed_note)
        if self.cymbal_note:
            lines.append(self.cymbal_note)
        if self.collision_note:
            lines.append(self.collision_note)
        if self.playability_note:
            lines.append(self.playability_note)
        if self.anchor_report:
            r = self.anchor_report
            lines.append(
                f"anchor          bar 1 at {self.anchor_seconds:.3f} s in the recording  "
                f"({100 * r.get('on_beat', 0):.0f}% of onsets on a beat, "
                f"runner-up {100 * r.get('runner_up_on_beat', 0):.0f}%)")
        if self.confidence:
            c = self.confidence
            verdict = "good" if c.get("ratio", 0) >= 1.8 else (
                "WEAK — the anchor is probably wrong, see docs/drums.md" )
            lines.append(
                f"placement       {100 * c.get('on_beat', 0):.0f}% of hits on a beat "
                f"vs {100 * c.get('chance', 0):.0f}% by chance "
                f"({c.get('ratio', 0):.2f}x) — {verdict}")
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
    levels: list[float] = []

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

        seen_frames: set[int] = set()
        # How far forward the attack may be. Tied to the band's own minimum gap
        # rather than a constant: the picker can fire well before the stroke it
        # found (78 ms on one measured snare), and a window sized for one example
        # silently drops hits whose rise is slower. 1.8x the minimum gap is as
        # far as we can look without stepping onto the next stroke.
        look_ahead = max(2, int(max(0.05, band.min_gap * 1.8) * sr / hop))
        for frame in np.atleast_1d(peaks):
            frame = int(frame)
            if frame >= len(times):
                continue
            # The picker fires on the rising edge as often as on the attack, and
            # then its own `wait` window suppresses the real peak a few frames
            # later — so the hit lands early and, worse, a level measured there
            # reads the silence *before* the stroke. Slide forward to the actual
            # maximum. Measured on Tutti in Fila: this is why obvious snares were
            # vanishing, and why the ones that survived had erratic velocities.
            ahead = min(len(flux), frame + look_ahead)
            if ahead > frame:
                frame += int(np.argmax(flux[frame:ahead]))
            if frame in seen_frames or frame >= len(times):
                continue
            seen_frames.add(frame)
            strength = float(flux[frame])
            if strength < band.delta * 0.5:
                report.dropped_quiet += 1
                continue
            share = float(band_energy[frame] / total_energy[frame])
            if share < band.dominance:
                report.dropped_not_dominant += 1
                continue

            instrument = band.instrument
            if band.articulation == "hat":
                instrument = _classify_hat(samples, sr, float(times[frame]))
            elif detect_cymbals and band.instrument == "hihat_closed":
                instrument = _classify_high_band(
                    band_energy, times, frame, sr, hop
                )

            # Level, not flux. Flux measures how fast the spectrum *changed*,
            # which is a poor stand-in for how hard the drum was hit and a noisy
            # one — and dividing by the loudest moment in the whole song
            # squashes everything else. On an isolated part stem the honest
            # measurement is right there: the peak of the attack itself.
            # Centred on the attack, not starting at it: a window that begins
            # at the detected frame can end before the stroke it is supposed to
            # be measuring.
            start = max(0, int(times[frame] * sr) - int(0.015 * sr))
            window = samples[start:start + int(0.075 * sr)]
            level = float(np.abs(window).max()) if window.size else 0.0
            hits.append(Hit(instrument, float(placement[frame]) - offset, 100))
            levels.append(level)
            report.per_instrument[instrument] = report.per_instrument.get(instrument, 0) + 1

    hits, levels, gated = gate_quiet_hits(hits, levels)
    if gated:
        report.dropped_quiet += gated
        for instrument in list(report.per_instrument):
            report.per_instrument[instrument] = sum(
                1 for hit in hits if hit.instrument == instrument)
            if not report.per_instrument[instrument]:
                del report.per_instrument[instrument]
    hits = scale_velocities(hits, levels, floor=velocity_floor, ceiling=velocity_ceiling)

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
    from .analyze import grid_confidence
    report.confidence = grid_confidence(
        [h.time for h in hits if h.instrument in ("kick", "snare")], timeline.bpm)
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
    # No articulation test: see _classify_hat for why open-vs-closed does not
    # survive on this stem. Every hat comes out closed and you open them by hand.
    "hihat": Band("hihat_closed", 1500, 16000, delta=0.12, min_gap=0.040),
    "cymbals": Band("crash", 800, 16000, delta=0.16, min_gap=0.200),
}


def bands_with_hat_delta(
    delta: float | None,
    bands: dict[str, Band] | None = None,
) -> dict[str, Band]:
    """Copy of *bands* with the hi-hat picker's sensitivity changed.

    Exposed as a knob rather than retuned in :data:`PART_BANDS`, because the
    trade-off is real and only measurable by ear. Measured on Manlio at
    triplet-8ths, against the whole pipeline so the timing corrections are in:

    ======  ====  ==========  =============================
    delta   hats  on the grid precision of the hits it adds
    ======  ====  ==========  =============================
    0.12     648         81%  -- the shipping default
    0.09     691         79%  49%
    0.07     726         78%  57%
    0.05     775         75%  31% -- chance is 33%
    ======  ====  ==========  =============================

    So the subtle hat Paolo hears missing is genuinely there and recoverable
    down to about 0.07 — a census of the hi-hat stem against every triplet slot
    found 182 empty slots carrying energy at or above the weakest tenth of the
    hits that were kept — but the marginal strokes arrive at about half the
    precision of the ones already found, and below 0.07 they are noise.

    Lowering the default would also change Tutti in Fila, and one drummer and
    one kit across that album (see CLAUDE.md) means that is a real consequence
    rather than a hypothetical one. Decide it per song, by ear.
    """
    table = dict(bands or PART_BANDS)
    if delta is None:
        return table
    table["hihat"] = replace(table["hihat"], delta=delta)
    return table


def transcribe_parts(
    parts: dict[str, object],
    *,
    timeline: Timeline,
    offset: float = 0.0,
    kit_mix=None,
    bands: dict[str, Band] | None = None,
    follow_window: float = 10.0,
    subdivision: int = 4,
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
    kit_samples = None
    if kit_mix is not None:
        samples, sr = load_mono(kit_mix, 22050)
        kit_samples = samples
        envelope = librosa.onset.onset_strength(y=samples, sr=sr, hop_length=256)
        wander = pulse_wander(
            envelope,
            librosa.times_like(envelope, sr=sr, hop_length=256),
            timeline.bpm,
            window=follow_window,
        )

    hits: list[Hit] = []
    sidesticks = 0
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
        if name == "snare" and kit_samples is not None and "hihat" in parts:
            # Before the dewander, while a hit's time still names a moment in
            # the file rather than a position on the grid.
            hat_samples, _ = load_mono(parts["hihat"], 22050)
            snare_samples, _ = load_mono(path, 22050)
            part_hits, renamed = label_sidesticks(part_hits, sidestick_evidence(
                part_hits, kit=kit_samples, snare=snare_samples,
                hat=hat_samples, sample_rate=22050, offset=offset))
            sidesticks += renamed
            if renamed:
                report.per_instrument["sidestick"] = renamed
                report.per_instrument["snare"] = (
                    report.per_instrument.get("snare", renamed) - renamed)
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
        dropped_quiet=sum(r.dropped_quiet for r in reports.values()),
        dropped_not_dominant=sum(r.dropped_not_dominant for r in reports.values()),
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
        # Only possible here: each instrument lives in its own stem, so no
        # per-stem pass can see that a quiet snare and a loud crash are the same
        # event, or that four stems agree on a moment needing four hands.
        hits, notes = clean_merged_hits(hits, timeline, subdivision=subdivision)
        if sidesticks:
            merged.sidestick_note = (
                f"named {sidesticks} snare-stem hits as side-sticks (no shell "
                f"tone, and louder in the hat stem than in the snare stem — see "
                f"transcribe.label_sidesticks) and dropped "
                f"{notes['sidestick_hats']} hi-hat hits that were only the "
                f"click's own leak")
        opened = notes["cymbal_runs"]
        if opened:
            merged.cymbal_note = (f"relabelled {opened} cymbal hits as open hi-hats "
                                  f"(runs of 3+ — see transcribe.split_cymbal_runs); "
                                  f"place the real crashes by hand")
        if notes["hat_pairs"]:
            merged.cymbal_note += f"; merged {notes['hat_pairs']} doubled hat strokes"
        bled = notes["crash_bleed"] + notes["cross_stem_bleed"]
        if bled:
            merged.dropped_quiet += bled
            merged.bleed_note = (
                f"dropped {notes['crash_bleed']} quiet kick/snare hits on a crash "
                f"and {notes['cross_stem_bleed']} more that coincided with a much "
                f"louder stroke in another stem")
        merged.collision_report = notes["collisions"]
        if notes["collisions"]["dropped"]:
            merged.collision_note = (
                f"dropped {notes['collisions']['dropped']} kick/snare collisions "
                f"the song's own pattern resolved the other way — see "
                f"transcribe.resolve_collisions")
        if notes["playability"]:
            merged.playability_note = (
                f"dropped {notes['playability']} hits that would have needed a "
                f"third hand — see transcribe.enforce_playability")
        if bled or notes["collisions"]["dropped"] or notes["playability"]:
            for instrument in list(merged.per_instrument):
                merged.per_instrument[instrument] = sum(
                    1 for hit in hits if hit.instrument == instrument)
    from .analyze import grid_confidence
    merged.confidence = grid_confidence(
        [h.time for h in hits if h.instrument in ("kick", "snare")], timeline.bpm)
    reports["kit"] = merged
    hits.sort(key=lambda h: (h.time, h.instrument))
    return DrumPerformance(hits, timeline, "Drums (transcribed from parts)"), reports


def detect_anchor(path, bpm: float, *, sample_rate: int = 22050) -> tuple[float, dict]:
    """Measure where the grid sits in a recording. The default for --offset.

    Onsets from the whole file, then :func:`analyze.find_grid_anchor`. Use the
    *kit* stem rather than a part stem: the anchor is a property of the
    recording, and the mixture always has the most onsets to fit.
    """
    from .analyze import find_grid_anchor

    librosa = require_module("librosa", "audio")
    samples, sr = load_mono(path, sample_rate)
    env = librosa.onset.onset_strength(y=samples, sr=sr, hop_length=256)
    onsets = librosa.onset.onset_detect(
        onset_envelope=env, sr=sr, hop_length=256, units="time", delta=0.2
    )
    wander = None
    if len(onsets) > 12:
        from .analyze import pulse_wander

        wander = pulse_wander(env, librosa.times_like(env, sr=sr, hop_length=256),
                              bpm, window=10.0)
        if wander:
            onsets = dewander(onsets, wander)
    return find_grid_anchor(onsets, bpm)


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


def split_cymbal_runs(
    hits: list[Hit],
    beat_seconds: float,
    *,
    max_gap_beats: float = 2.1,
    min_run: int = 3,
) -> tuple[list[Hit], int]:
    """Relabel runs of "crashes" as open hi-hats. Returns (hits, relabelled).

    A five-way split gives one stem for *cymbals*, and an open hi-hat lands in it
    — the strike stays in the hat stem, the wash goes here — so everything in
    that stem was coming out as a crash. Paolo: "fundamentally an open hi-hat is
    recognized as a splash/crash instead", with a crash landing on every beat of
    bars 78 and 79.

    **The acoustic route does not work, and the failures are worth recording so
    nobody repeats them.** Against bars identified by ear, a real crash and an
    open hat could not be told apart by: decay at 200/500/900 ms; spectral
    centroid of the attack; coincidence with a hi-hat strike (they *all*
    coincide, since hats play throughout); the cymbal-stem to hat-stem level
    ratio; or long sustain at 0.6-1.2 s. On every one of those the two known
    crashes sat at *opposite ends* of the range with the open hats in between.
    What survives separation is not enough to classify.

    What does separate them is what the parts *do*: an open hat is a pattern —
    beat after beat — and a crash is an accent, sparse by nature. So a run of
    three or more cymbal hits no more than ~2 beats apart is a hi-hat part.

    It errs deliberately toward the open hat, because the two mistakes do not
    cost the same: a crash on every beat is a wash that ruins the track, while a
    missing crash is one obvious accent to add by hand — which Stage 7 of
    docs/drums-rebuild.md expects you to do anyway.
    """
    if beat_seconds <= 0:
        return hits, 0
    indexed = sorted(
        (i for i, hit in enumerate(hits) if hit.instrument.startswith("crash")),
        key=lambda i: hits[i].time,
    )
    if len(indexed) < min_run:
        return hits, 0
    gap = max_gap_beats * beat_seconds
    out = list(hits)
    relabelled = 0
    start = 0
    while start < len(indexed):
        end = start
        while (end + 1 < len(indexed)
               and hits[indexed[end + 1]].time - hits[indexed[end]].time <= gap):
            end += 1
        if end - start + 1 >= min_run:
            for i in indexed[start:end + 1]:
                out[i] = replace(out[i], instrument="hihat_open")
                relabelled += 1
        start = end + 1
    return out, relabelled


def merge_hat_pairs(hits: list[Hit], *, window: float = 0.030) -> tuple[list[Hit], int]:
    """One open hat, not a closed one and an open one. Returns (hits, merged).

    The five-way split puts the hat's *strike* in the hat stem and its *wash* in
    the cymbal stem, so a single open hi-hat is detected twice — once in each —
    and arrives as a closed hat and an open hat on the same beat. It is one
    stroke and the drummer had one hand there.

    The open one wins: it is the articulation that was actually played, and it
    carries the wash that makes it sound open.
    """
    opens = sorted(hit.time for hit in hits if hit.instrument == "hihat_open")
    if not opens:
        return hits, 0
    opens_arr = np.asarray(opens)
    kept, merged = [], 0
    for hit in hits:
        if hit.instrument == "hihat_closed":
            if float(np.min(np.abs(opens_arr - hit.time))) < window:
                merged += 1
                continue
        kept.append(hit)
    return kept, merged


def sidestick_evidence(
    hits: list[Hit],
    *,
    kit,
    snare,
    hat,
    sample_rate: int,
    offset: float = 0.0,
    window: tuple[float, float] = SIDESTICK_WINDOW,
    candidates: tuple[str, ...] = ("snare",),
) -> list[tuple[float, float]]:
    """Measure, per hit, what :func:`label_sidesticks` needs to decide.

    Returns a list parallel to *hits* of ``(body_share, hat_margin_db)``, with
    ``nan`` for anything not measurable — a hit past the end of the audio, or one
    that is not a *candidates* instrument. Same shape as the ``levels`` sequence
    :func:`gate_quiet_hits` takes, for the same reason: the decision stays a pure
    function of numbers, and only the numbers need audio.

    *kit*, *snare* and *hat* are mono arrays at *sample_rate* — the whole drum
    stem and two part stems. The body share is measured on the **kit**, not on
    the separated snare, and that is the whole reason this works. Measured the
    other way first: read off the snare part stem it reports 10 rim clicks in
    Manlio's chorus-1 that are not there, because in a dense bar the separator
    puts the crash in the snare stem and a window looking for the loudest thing
    nearby finds the cymbal. The kit mix is the audio that was actually played;
    the separator is not in the discriminator at all.

    *offset* is where bar 1 beat 1 sits inside the recording, because ``hits``
    are on the musical clock and the arrays are on the audio one. **Pass raw
    detection times**: this must run before :func:`dewander` and before
    :func:`best_anchor_shift`, since after those a hit's time is a grid position
    and no longer names a moment in the file.
    """
    pre, post = window
    length = int((pre + post) * sample_rate)
    kit = np.asarray(kit, dtype=float)
    snare = np.asarray(snare, dtype=float)
    hat = np.asarray(hat, dtype=float)
    freqs = np.fft.rfftfreq(length, 1.0 / sample_rate) if length else np.zeros(0)
    taper = np.hanning(length) if length else np.zeros(0)

    def slice_at(samples: np.ndarray, start: int) -> np.ndarray | None:
        if start < 0 or start + length > samples.size:
            return None
        return samples[start:start + length]

    out: list[tuple[float, float]] = []
    for hit in hits:
        start = int(round((hit.time + offset - pre) * sample_rate))
        pieces = [slice_at(part, start) for part in (kit, snare, hat)]
        if hit.instrument not in candidates or any(p is None or not length
                                                   for p in pieces):
            out.append((float("nan"), float("nan")))
            continue
        kit_seg, snare_seg, hat_seg = pieces
        power = np.abs(np.fft.rfft(kit_seg * taper)) ** 2
        body = float(power[(freqs >= 180.0) & (freqs < 500.0)].sum())
        rest = float(power[(freqs >= 500.0) & (freqs < 10000.0)].sum())
        margin = 20.0 * np.log10(
            (float(np.sqrt((snare_seg ** 2).mean())) + 1e-9)
            / (float(np.sqrt((hat_seg ** 2).mean())) + 1e-9))
        out.append((body / (body + rest + 1e-20), margin))
    return out


def label_sidesticks(
    hits: list[Hit],
    evidence,
    *,
    body_share: float = SIDESTICK_BODY_SHARE,
    hat_margin_db: float = SIDESTICK_HAT_MARGIN_DB,
) -> tuple[list[Hit], int]:
    """Rename the snare hits that are really rim clicks. Returns (hits, count).

    Manlio's verses play the backbeat as a side-stick, and until this existed the
    transcriber had no name for it: ``sidestick`` was in
    :data:`~rambass.drummap.CANONICAL` and in :data:`_KEEP_RANK` and nothing ever
    produced one. So verse-1, verse-2 and verse-3 came out with **zero** snare
    hits — the rim click's transient landed in the 6-16 kHz hat band at full
    velocity, and :func:`suppress_cross_stem_bleed` then used that phantom accent
    as the loud partner that deleted the real stroke.

    Both features must agree, and the asymmetry is deliberate. On Manlio the pair
    catches 23 of the 24 verse backbeats and **none** of the 69 real snare
    backbeats: turning a snare into a rim click is the error that would be heard,
    so the rule is built to never make it, at the price of leaving the odd click
    as a snare. Either feature alone fails — the level test alone takes 3 real
    backbeats (bar 14 beat 2 and bar 51 beat 2 both sit at -2 dB with a full 0.6
    body share), and the body test alone takes 5 ghost detections.

    The 10 hits it does relabel beyond the backbeat are all velocity 76 or below,
    off the beat, in the snare stem's own leakage — spurious either way, and a
    quiet ghost voiced as a rim click rather than a soft snare is not a fault
    worth a third constant to avoid.

    Velocity is untouched, so these land near the floor: they were scaled against
    the song's snares and they are 20-30 dB below them. That is true of the real
    instrument too, and setting a rim click's response is the kit's job at
    Stage 7, not this pass's.
    """
    evidence = list(evidence)
    if not hits or len(evidence) != len(hits):
        return list(hits), 0
    out: list[Hit] = []
    count = 0
    for hit, (body, margin) in zip(hits, evidence, strict=True):
        if (hit.instrument == "snare"
                and body == body and margin == margin      # not nan
                and body < body_share and margin < hat_margin_db):
            out.append(replace(hit, instrument="sidestick"))
            count += 1
        else:
            out.append(hit)
    return out, count


def drop_hats_on_sidesticks(
    hits: list[Hit],
    *,
    window: float = 0.030,
    hats: tuple[str, ...] = ("hihat_closed", "hihat_open", "hihat_pedal"),
) -> tuple[list[Hit], int]:
    """Drop the hi-hat hits that are only a rim click's leak. Returns (hits, n).

    A side-stick is a high-frequency transient and nothing else, so the hat
    stem's own detector fires on it. Measured on Manlio's verses, per triplet
    slot: the hi-hat stem is 13-14 dB louder at beats 2 and 4 than at the
    surrounding triplets, and its 6-16 kHz share collapses from 0.83-0.97 — a
    closed hat is nearly all top end — to 0.30-0.43, which is a stick on a rim.
    What the detector reported there is the click.

    Whether a hat *also* sounded under it is not answerable from the audio, since
    the click masks it. So this drops it, and Paolo's reasoning is what settles
    the tie: a hi-hat note whose whole evidence is a rim click does not belong in
    the part, and the alternative is not a hole but a velocity-122 accented
    closed hat on every backbeat of the song, which is plainly audible and
    plainly wrong. Putting a hat back under the click is a musical decision for
    Stage 7 of docs/drums-rebuild.md, at a velocity somebody chose.

    Runs first in :func:`clean_merged_hits`, before anything reasons about hats,
    so no later pass merges or relabels a hit that is not a hi-hat at all.
    """
    sidesticks = np.asarray(
        sorted(hit.time for hit in hits if hit.instrument == "sidestick"),
        dtype=float)
    if not sidesticks.size:
        return list(hits), 0
    kept = [hit for hit in hits
            if hit.instrument not in hats
            or float(np.min(np.abs(sidesticks - hit.time))) >= window]
    return kept, len(hits) - len(kept)


def suppress_crash_bleed(
    hits: list[Hit],
    *,
    window: float = 0.030,
    quiet_by: int = 13,
    parts: tuple[str, ...] = ("kick", "snare"),
) -> tuple[list[Hit], int]:
    """Drop quiet kick/snare hits that coincide with a crash. Returns (hits, dropped).

    A crash is broadband and loud, and no separator is perfect, so some of it
    lands in the other stems — as a plausible-looking hit exactly on a downbeat,
    which is where crashes live. Paolo heard it immediately: "a phantom snare on
    the one".

    The rule leans on what the music does rather than on levels alone. A snare
    played *under* a crash is an accent — the two land together because the
    drummer hit them together, hard. So a snare that coincides with a crash and
    is markedly quieter than that song's snares is far more likely to be the
    crash leaking than a deliberately soft backbeat.

    Hi-hats are excluded on purpose: they play under crashes constantly and are
    quiet by nature, so the same rule there would delete a real part.
    """
    crashes = [hit.time for hit in hits if hit.instrument.startswith("crash")]
    if not crashes or not hits:
        return hits, 0
    crashes_arr = np.asarray(sorted(crashes))

    thresholds: dict[str, float] = {}
    for part in parts:
        velocities = [h.velocity for h in hits if h.instrument.startswith(part)]
        if len(velocities) >= 8:
            thresholds[part] = float(np.median(velocities)) - quiet_by

    kept: list[Hit] = []
    dropped = 0
    for hit in hits:
        part = next((p for p in thresholds if hit.instrument.startswith(p)), None)
        if part is not None and hit.velocity < thresholds[part]:
            if float(np.min(np.abs(crashes_arr - hit.time))) < window:
                dropped += 1
                continue
        kept.append(hit)
    return kept, dropped


#: Instruments a drummer plays with a foot, and which foot. Everything absent
#: from this table is played with a hand.
FOOT_OF: dict[str, str] = {
    "kick": "right foot",
    "kick_2": "left foot",
    "hihat_pedal": "left foot",
}

#: Tie-break order when a cluster needs a limb it does not have. Lower is kept.
#: On an equal velocity the drums outrank the cymbals, because the cymbal and
#: hat stems are where the spurious detections live: a crash stem fires on
#: anything broadband and the hat stem fires on the wash of everything else.
_KEEP_RANK: dict[str, int] = {
    "snare": 0, "sidestick": 0,
    "tom_low": 1, "tom_mid": 1, "tom_high": 1,
    "ride": 2, "ride_bell": 2,
    "hihat_closed": 3, "hihat_open": 3,
    "crash": 4, "crash_2": 4, "china": 4, "splash": 4,
}


def _clusters(hits: list[Hit], window: float) -> list[list[int]]:
    """Indices of *hits* grouped into simultaneity clusters.

    Bounded from each cluster's **first** hit rather than by single linkage, the
    same way :func:`~rambass.quantize.deflam` does it. Single linkage would let
    a dense hat part chain into one cluster spanning the whole song.
    """
    order = sorted(range(len(hits)), key=lambda i: hits[i].time)
    out: list[list[int]] = []
    for index in order:
        if out and hits[index].time - hits[out[-1][0]].time <= window:
            out[-1].append(index)
        else:
            out.append([index])
    return out


def enforce_playability(
    hits: list[Hit],
    *,
    window: float = 0.030,
    hands: int = 2,
    feet: int = 2,
) -> tuple[list[Hit], int]:
    """Drop hits that would need a third hand. Returns (hits, dropped).

    Five stems detected independently can agree on a moment that no human could
    play: a snare, a closed hat and a crash inside 10 ms is three hands. It
    happens because a loud broadband stroke leaks into every stem at once, and
    each stem's detector believes what it sees — so the error looks like a
    plausible chord rather than like noise, and it survives every gate that
    reasons about one instrument at a time.

    The limb model is the whole point and it is deliberately crude: two hands,
    two feet, :data:`FOOT_OF` says which instruments are feet. **A kick is a
    foot**, so the commonest event in rock drumming — kick, snare and hat
    together — must pass untouched, and there is a test that says so.

    Counted per *distinct instrument*, not per hit: two snares 8 ms apart is one
    hand playing a flam, and collapsing that is
    :func:`~rambass.quantize.deflam`'s job, not this one's. When a cluster is
    over its limit the loudest instruments stay, ties broken by
    :data:`_KEEP_RANK` so the result does not depend on input order.

    This finds a real fault but it is not a substitute for the musical passes
    that run beside it: on Manlio it fires far less often than
    :func:`resolve_collisions`, because most phantom hits arrive in twos, which
    two hands can play.
    """
    if not hits:
        return list(hits), 0

    drop: set[int] = set()
    for cluster in _clusters(hits, window):
        by_instrument: dict[str, list[int]] = {}
        for index in cluster:
            by_instrument.setdefault(hits[index].instrument, []).append(index)

        for limit, group in (
            (hands, [n for n in by_instrument if n not in FOOT_OF]),
            (feet, sorted({FOOT_OF[n] for n in by_instrument if n in FOOT_OF})),
        ):
            if len(group) <= limit:
                continue
            if group and group[0] in FOOT_OF.values():
                # Feet: rank the *limbs*, keeping each limb's loudest stroke.
                names = [[n for n in by_instrument if FOOT_OF.get(n) == limb]
                         for limb in group]
            else:
                names = [[n] for n in group]
            ranked = sorted(
                names,
                key=lambda ns: (
                    -max(hits[i].velocity for n in ns for i in by_instrument[n]),
                    min(_KEEP_RANK.get(n, 50) for n in ns),
                    sorted(ns)[0],
                ),
            )
            for names_out in ranked[limit:]:
                for name in names_out:
                    drop.update(by_instrument[name])

    kept = [hit for i, hit in enumerate(hits) if i not in drop]
    return kept, len(drop)


def resolve_collisions(
    hits: list[Hit],
    timeline: Timeline,
    *,
    subdivision: int = 4,
    window: float = 0.030,
    pairs: tuple[tuple[str, str], ...] = (("kick", "snare"),),
    min_evidence: int = 4,
    ratio: float = 2.0,
    loud_keeps: int = 118,
) -> tuple[list[Hit], dict]:
    """Resolve simultaneous kick/snare hits from the song's own pattern.

    Paolo: *"kick+snare on the same beat are fairly uncommon, is there a way to
    normalize them and say based on the patterns of the song, if you have kick
    and snare on beat 1, it is more likely to be a kick"*. This is that.

    The evidence is the part itself, so nothing has to be assumed about the
    genre. For every metrical slot, count how often each instrument of the pair
    appears **alone** there — those are the bars where the separator did not
    confuse anything and the drummer's intent is unambiguous. Then use that
    census to arbitrate the bars where both fired at once. Measured on Manlio at
    triplet-8ths::

        beat 1   kick alone 49   snare alone  4   together 25
        beat 2   kick alone  4   snare alone 59   together  1
        beat 3   kick alone 56   snare alone  5   together 17
        beat 4   kick alone  5   snare alone 52   together  2

    A textbook shuffle, and 42 of its 52 collisions sit on beats 1 and 3 where
    the kick wins better than ten to one. Those are the phantom snares Paolo
    heard on the downbeat.

    Three refusals keep it from inventing a part:

    * **No verdict without a majority.** A slot must have *min_evidence* solo
      hits and one instrument must lead by *ratio*, or both hits stay. An
      ambiguous slot is a genuine unison as often as it is an error.
    * **Loud hits are kept.** A stroke at *loud_keeps* or above was played, not
      leaked: a crash-accented backbeat lands with the kick because the drummer
      hit them together, hard. Bleed is quiet by nature.
    * **Only the named pair is touched.** Everything else passes through, so a
      hat under a downbeat is never at risk.

    Returns (hits, report) where the report carries ``dropped``, ``kept`` and
    the per-slot census, so ``--report`` can show its working.
    """
    report: dict = {"dropped": 0, "kept": 0, "slots": {}}
    if not hits or subdivision < 1:
        return list(hits), report

    def slot_of(time: float) -> int:
        bar, beat = timeline.seconds_to_bar_beat(time)
        per_bar = timeline.time_signature_at(max(bar, 1))[0] * subdivision
        return int(round((beat - 1.0) * subdivision)) % per_bar

    drop: set[int] = set()
    for first, second in pairs:
        members = {first, second}
        indices = [i for i, h in enumerate(hits) if h.instrument in members]
        if not indices:
            continue
        times = {
            name: np.array(sorted(hits[i].time for i in indices
                                  if hits[i].instrument == name))
            for name in (first, second)
        }

        census: dict[int, dict[str, int]] = {}
        collisions: list[int] = []
        for i in indices:
            hit = hits[i]
            other = times[second if hit.instrument == first else first]
            near = other.size and float(np.min(np.abs(other - hit.time))) < window
            entry = census.setdefault(slot_of(hit.time), {first: 0, second: 0})
            if near:
                collisions.append(i)
            else:
                entry[hit.instrument] += 1

        for i in collisions:
            hit = hits[i]
            if hit.velocity >= loud_keeps:
                continue
            entry = census.get(slot_of(hit.time))
            if not entry:
                continue
            mine, theirs = entry[hit.instrument], entry[
                second if hit.instrument == first else first]
            if theirs >= min_evidence and theirs >= ratio * max(mine, 1):
                drop.add(i)

        report["slots"][f"{first}/{second}"] = {
            str(slot): dict(counts) for slot, counts in sorted(census.items())
        }

    kept = [hit for i, hit in enumerate(hits) if i not in drop]
    report["dropped"] = len(drop)
    report["kept"] = len(kept)
    return kept, report


def suppress_cross_stem_bleed(
    hits: list[Hit],
    *,
    window: float = 0.025,
    quiet_by: int = 13,
    louder_by: int = 10,
    exclude: tuple[str, ...] = ("hihat_closed", "hihat_open", "hihat_pedal",
                                "ride", "sidestick"),
) -> tuple[list[Hit], int]:
    """Drop quiet hits that coincide with a much louder one elsewhere.

    The general form of :func:`suppress_crash_bleed`, which only knew about
    crashes. No separator is perfect, so any loud broadband stroke leaves a
    trace in every other stem, and a per-stem onset detector reports it as a
    hit with a plausible position and a plausible velocity. Manlio bar 1 is the
    clean example: the song opens on a single kick, and the snare stem carries
    that kick at a tenth the level with the same decay envelope.

    Velocity is a fair proxy here **because**
    :func:`scale_velocities` is per instrument against a fixed 30 dB span, so a
    velocity is already "how this stroke compares with this instrument's own
    typical one". It is a proxy and not a measurement, which bounds what this
    can do — see the warning below.

    *exclude* names the instruments that are **quiet by nature**, and that
    disqualifies them from *both* roles — they are neither deleted as bleed nor
    believed as the loud partner that deletes something else. Hats and the ride
    are there for the reason :func:`suppress_crash_bleed` excludes hats: they
    play under everything, so the same rule deletes a real part. A rim click is
    there because it is 20-30 dB below the same drummer's snare by construction.

    **Believing them was the bug.** Measured on Manlio, this pass dropped 136
    hits and 99 of them were drums deleted because a hi-hat beside them read
    louder — 44 kicks and 45 snares, including the entire verse backbeat. A
    closed hat cannot out-shout a kick, so a hat stem that is the louder of the
    two is the drum leaking *into* it, and deleting the drum inverts the
    causality. The argument that stops the pass gating hats is the same argument
    that stops it trusting them, and applying it in only one direction is what
    let a rim click's leak delete the stroke that caused it.

    **Known limit, measured, do not assume this catches everything.** On Manlio
    the snare stem's *median* detection is 20 dB below its real backbeats — the
    part is mostly ghosts and leakage, so the median that "quiet_by" is measured
    against is itself a ghost, and bar 1's phantom snare reads as *above*
    average for its stem. This function does not catch it.
    :func:`resolve_collisions` does, by asking what the song plays there
    instead of how loud it is. Run both; they fail in different places.
    """
    if len(hits) < 4:
        return list(hits), 0
    order = sorted(range(len(hits)), key=lambda i: hits[i].time)
    times = np.array([hits[i].time for i in order], dtype=float)

    medians: dict[str, float] = {}
    for instrument in {hit.instrument for hit in hits}:
        velocities = [h.velocity for h in hits if h.instrument == instrument]
        if len(velocities) >= 8:
            medians[instrument] = float(np.median(velocities))

    drop: set[int] = set()
    for position, index in enumerate(order):
        hit = hits[index]
        if hit.instrument in exclude or hit.instrument not in medians:
            continue
        if hit.velocity >= medians[hit.instrument] - quiet_by:
            continue
        lo = int(np.searchsorted(times, hit.time - window, "left"))
        hi = int(np.searchsorted(times, hit.time + window, "right"))
        for other_position in range(lo, hi):
            if other_position == position:
                continue
            other = hits[order[other_position]]
            if other.instrument == hit.instrument or other.instrument in exclude:
                continue
            if other.velocity - hit.velocity >= louder_by:
                drop.add(index)
                break

    kept = [hit for i, hit in enumerate(hits) if i not in drop]
    return kept, len(drop)


def clean_merged_hits(
    hits: list[Hit],
    timeline: Timeline,
    *,
    subdivision: int = 4,
) -> tuple[list[Hit], dict]:
    """Every pass that can only run once the stems have been merged.

    Extracted from :func:`transcribe_parts` so it can be tested without librosa
    or audio: per-stem detection needs both, this needs neither. The order is
    load-bearing and each step assumes the one before it has run:

    1. :func:`drop_hats_on_sidesticks` — first, before anything reasons about
       hats, because a hat that is only a rim click's leak is not a hat.
    2. :func:`split_cymbal_runs` — decide what the cymbal stem actually is,
       before anything reasons about crashes.
    3. :func:`merge_hat_pairs` — one open hat, not a closed one and an open one.
    4. :func:`suppress_crash_bleed` — needs the crashes to be real crashes,
       hence after step 2.
    5. :func:`suppress_cross_stem_bleed` — the general case, on what is left.
    6. :func:`resolve_collisions` — musical arbitration. After the level-based
       gates, because it should only be asked about collisions they could not
       explain.
    7. :func:`enforce_playability` — last, so it judges the finished part. A
       part that is still unplayable here is one no earlier pass could fix.

    Step 1 does nothing unless the side-sticks have already been named, which
    needs audio and so happens in :func:`transcribe_parts`.

    Returns (hits, notes) where notes counts what each pass did.
    """
    notes: dict = {}
    beat_seconds = 60.0 / timeline.bpm if timeline.bpm > 0 else 0.5
    hits, notes["sidestick_hats"] = drop_hats_on_sidesticks(hits)
    hits, notes["cymbal_runs"] = split_cymbal_runs(hits, beat_seconds)
    hits, notes["hat_pairs"] = merge_hat_pairs(hits)
    hits, notes["crash_bleed"] = suppress_crash_bleed(hits)
    hits, notes["cross_stem_bleed"] = suppress_cross_stem_bleed(hits)
    hits, notes["collisions"] = resolve_collisions(
        hits, timeline, subdivision=subdivision)
    hits, notes["playability"] = enforce_playability(hits)
    return hits, notes


def gate_quiet_hits(hits: list[Hit], levels, *, gate_db: float = 25.0):
    """Drop detections with no attack behind them. Returns (hits, levels, dropped).

    An onset detector run on an isolated stem finds things that are not hits:
    bleed, the tail of the previous note, separation artifacts. They are easy to
    miss because they arrive with a plausible velocity and a plausible position
    — but measured against the instrument's own typical attack they are 30, 40,
    50 dB down. On Tutti in Fila the snare's "hits" ran to -54 dB against a -15
    dB median, and each one was being voiced at the velocity floor, so the part
    was full of ghost strokes the drummer never played.

    Per instrument, because the parts have different levels, and in dB relative
    to the median rather than an absolute threshold, so a quietly recorded stem
    is not wiped out entirely.
    """
    if not hits:
        return hits, levels, 0
    levels = np.asarray(list(levels), dtype=float)
    if len(levels) != len(hits):
        return hits, levels, 0
    db = 20.0 * np.log10(np.maximum(levels, 1e-6))
    keep = np.ones(len(hits), dtype=bool)
    for instrument in {hit.instrument for hit in hits}:
        picked = np.array([i for i, hit in enumerate(hits) if hit.instrument == instrument])
        median = float(np.median(db[picked]))
        keep[picked] = db[picked] >= median - gate_db
    dropped = int((~keep).sum())
    return ([hit for hit, k in zip(hits, keep, strict=True) if k],
            levels[keep], dropped)


def scale_velocities(
    hits: list[Hit],
    levels,
    *,
    floor: int = 45,
    ceiling: int = 122,
    sensitivity_db: float = 30.0,
) -> list[Hit]:
    """Turn measured attack levels into velocities, one instrument at a time.

    Per instrument, because the parts do not share a scale: a hi-hat stem peaks
    far below a snare stem, and scaling them together would flatten the hats to
    nothing and leave the snare pinned at the top.

    *sensitivity_db* is deliberately gentle. A separated stem does not recover
    every stroke equally well, so part of any level difference is the separator,
    not the drummer: on Tutti in Fila one backbeat came back 4.3 dB down on its
    neighbours purely as an artifact, and at a steep slope that became 26
    velocity units — plainly audible, and wrong. 30 dB across the velocity range
    keeps ghost notes obviously quieter than accents while refusing to make a
    performance out of separation noise.

    In dB, and with a **fixed sensitivity** rather than stretched to fit:
    *sensitivity_db* of range maps to the whole velocity span, centred on the
    instrument's median. Stretching whatever spread happens to be there — the
    obvious approach, and the one tried first — guarantees a wide result even
    when the part was played dead even, because it scales up the measurement
    noise to fill the range. A backbeat that really is even should come out
    even, and this is what makes that possible.

    The contour is kept, not flattened. docs/drums-rebuild.md Stage 5 is
    explicit that replacing every velocity with one number throws away the ghost
    notes and the push into the chorus, and that is the dynamics the whole
    exercise is trying to keep.
    """
    if not hits:
        return hits
    levels = np.asarray(list(levels), dtype=float)
    if len(levels) != len(hits):
        return hits
    db = 20.0 * np.log10(np.maximum(levels, 1e-6))
    out = list(hits)
    for instrument in {hit.instrument for hit in hits}:
        picked = [i for i, hit in enumerate(hits) if hit.instrument == instrument]
        median = float(np.median(db[picked]))
        mid = (floor + ceiling) / 2.0
        per_db = (ceiling - floor) / sensitivity_db
        for i in picked:
            velocity = mid + (db[i] - median) * per_db
            out[i] = out[i].with_velocity(int(round(min(ceiling, max(floor, velocity)))))
    return out


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


#: Decay in dB from the attack to 120-180 ms later, above which a hi-hat would be
#: called open — **not used**, and the measurement behind that is worth keeping.
#:
#: docs/drums-rebuild.md predicts this test should start working once it is given
#: an isolated hat stem instead of a mixture. It does not, and the reason looks
#: structural: an open hat's *wash* is exactly the sound LarsNet routes to the
#: cymbals stem, so the hat stem keeps the strike and loses the ring that the
#: test is trying to measure. Against bars Paolo identified by ear, open hats
#: read -8.1, -1.9, -0.3 dB and a closed-hat bar read -6.9 to -2.8 dB: the two
#: groups overlap, so no threshold separates them.
#:
#: (A first attempt appeared to separate them cleanly. It was measuring at
#: *quantised* hit times, which sit up to 87 ms from the real strike — start in
#: the wash and a swell looks like decay, start before it and decay looks like a
#: swell. Calibrating an articulation test on quantised times will always
#: produce a confident, wrong answer.)
#:
#: So every hat comes out closed, and Stage 7 opens them by hand where the part
#: opens up — which is a texture decision the doc already assigns to a human.
OPEN_HAT_DECAY_DB = 2.0


def _classify_hat(samples: np.ndarray, sr: int, at: float,
                  threshold_db: float = OPEN_HAT_DECAY_DB) -> str:
    """Open or closed from how long the stroke rings. Kept, but not wired up.

    See OPEN_HAT_DECAY_DB: on the stems this pipeline produces the two
    populations overlap. Enable it by giving the hihat band
    ``articulation="hat"`` if a better separator ever produces a hat stem that
    keeps its wash.
    """
    start = int(at * sr)
    attack = samples[max(0, start - int(0.010 * sr)): start + int(0.030 * sr)]
    tail = samples[start + int(0.120 * sr): start + int(0.180 * sr)]
    if attack.size == 0 or tail.size == 0:
        return "hihat_closed"
    attack_db = 20 * np.log10(max(float(np.abs(attack).max()), 1e-6))
    tail_db = 20 * np.log10(max(float(np.abs(tail).max()), 1e-6))
    return "hihat_open" if tail_db - attack_db > threshold_db else "hihat_closed"


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
