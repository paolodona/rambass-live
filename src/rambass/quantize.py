"""Cleaning up a drum performance: de-flam, quantise, shape velocities.

This is where an extracted or loosely-played take becomes a backing track. The
operations are deliberately separate and each is a pure function over a
:class:`~rambass.midiio.DrumPerformance`, so you can run them in any order,
re-run them, and diff the results.

Everything is deterministic: humanising takes a seed, so re-running the pipeline
after tweaking one setting does not reshuffle the whole song.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .midiio import DrumPerformance, Hit
from .timeline import Timeline

#: Instruments that are usually played on a coarser grid than the hats.
DEFAULT_SUBDIVISIONS: dict[str, int] = {
    "kick": 4,            # 16ths in 4/4
    "snare": 4,
    "sidestick": 4,
    "hihat_closed": 4,
    "hihat_open": 4,
    "hihat_pedal": 4,
    "crash": 2,           # cymbals land on 8ths at worst; never snap them tight
    "crash_2": 2,
    "china": 2,
    "splash": 2,
    "ride": 4,
    "ride_bell": 4,
}

#: How far from a grid line a hit may be and still get snapped, as a fraction
#: of the grid step. The furthest a hit can ever be is half a step, so 0.5 means
#: "always snap" and 0.35 leaves the outer 30% of each gap alone — those are
#: taken to be intentional (a fill, a drag, a push) rather than sloppy.
DEFAULT_TOLERANCE_STEPS = 0.35


@dataclass(frozen=True)
class QuantizeSettings:
    """Knobs for :func:`quantize`."""

    subdivision: int = 4              # per beat: 4 = 16th notes in 4/4
    strength: float = 1.0             # 0 = untouched, 1 = fully on the grid
    swing: float = 0.0                # 0 = straight, 0.5 = triplet swing
    tolerance_steps: float = DEFAULT_TOLERANCE_STEPS
    per_instrument: dict[str, int] | None = None
    force: bool = False               # ignore tolerance and snap everything

    def subdivision_for(self, instrument: str) -> int:
        table = self.per_instrument if self.per_instrument is not None else DEFAULT_SUBDIVISIONS
        return table.get(instrument, self.subdivision)


def deflam(
    performance: DrumPerformance,
    window_ms: float = 25.0,
    *,
    keep: str = "loudest",
) -> tuple[DrumPerformance, int]:
    """Collapse duplicate hits of the same instrument inside *window_ms*.

    Onset detection on a separated drum stem double-triggers constantly — a kick
    with a long tail reads as two onsets, a snare with a rattling shell as three.
    Left alone those become machine-gun flams once the MIDI hits a sampled kit.

    Returns the cleaned performance and how many hits were removed.
    """
    if keep not in ("loudest", "first"):
        raise ValueError("keep must be 'loudest' or 'first'")
    window = window_ms / 1000.0
    kept: list[Hit] = []
    removed = 0
    by_instrument: dict[str, list[Hit]] = {}
    for hit in performance.sorted_hits():
        by_instrument.setdefault(hit.instrument, []).append(hit)

    for hits in by_instrument.values():
        cluster: list[Hit] = []
        for hit in hits:
            if cluster and hit.time - cluster[0].time <= window:
                cluster.append(hit)
                continue
            if cluster:
                kept.append(_pick(cluster, keep))
                removed += len(cluster) - 1
            cluster = [hit]
        if cluster:
            kept.append(_pick(cluster, keep))
            removed += len(cluster) - 1

    return DrumPerformance(kept, performance.timeline, performance.name), removed


def _pick(cluster: list[Hit], keep: str) -> Hit:
    if keep == "first":
        return cluster[0]
    loudest = max(cluster, key=lambda h: h.velocity)
    # Keep the loudest velocity but the *earliest* time: the first onset is the
    # real attack, the later ones are the tail being re-detected.
    return loudest.moved_to(cluster[0].time)


def quantize(
    performance: DrumPerformance,
    settings: QuantizeSettings | None = None,
) -> tuple[DrumPerformance, dict]:
    """Snap hits toward the grid. Returns the result and a report."""
    settings = settings or QuantizeSettings()
    if not 0.0 <= settings.strength <= 1.0:
        raise ValueError("strength must be between 0 and 1")
    if not -0.5 <= settings.swing <= 0.5:
        raise ValueError("swing must be between -0.5 and 0.5")

    timeline = performance.timeline
    moved: list[Hit] = []
    total_shift = 0.0
    skipped = 0
    largest = 0.0

    for hit in performance.sorted_hits():
        subdivision = settings.subdivision_for(hit.instrument)
        target = _nearest_grid_time(timeline, hit.time, subdivision, settings.swing)
        distance = abs(target - hit.time)
        step_seconds = _beat_seconds_at(timeline, hit.time) / subdivision
        tolerance = settings.tolerance_steps * step_seconds

        if not settings.force and distance > tolerance:
            skipped += 1
            moved.append(hit)
            continue

        new_time = hit.time + (target - hit.time) * settings.strength
        total_shift += abs(new_time - hit.time)
        largest = max(largest, abs(new_time - hit.time))
        moved.append(hit.moved_to(new_time))

    report = {
        "hits": len(moved),
        "left_alone": skipped,
        "mean_shift_ms": round(1000.0 * total_shift / max(len(moved) - skipped, 1), 2),
        "largest_shift_ms": round(1000.0 * largest, 2),
    }
    return DrumPerformance(moved, timeline, performance.name), report


def _beat_seconds_at(timeline: Timeline, seconds: float) -> float:
    bar, _ = timeline.seconds_to_bar_beat(seconds)
    sig = timeline.time_signature_at(max(bar, 1))
    return timeline.bar_length_seconds(max(bar, 1)) / sig[0]


def _nearest_grid_time(
    timeline: Timeline,
    seconds: float,
    subdivision: int,
    swing: float,
) -> float:
    """Nearest grid position in *seconds*, computed in quarter-note space."""
    quarters = timeline.seconds_to_quarters(seconds)
    step = 1.0 / subdivision
    index = round(quarters / step)
    candidates = {index - 1, index, index + 1}
    best = None
    best_distance = float("inf")
    for candidate in candidates:
        position = candidate * step
        if swing and subdivision >= 2 and candidate % 2 == 1:
            # Push every odd subdivision late: the standard swing/shuffle feel.
            position += swing * step
        candidate_seconds = timeline.quarters_to_seconds(position)
        distance = abs(candidate_seconds - seconds)
        if distance < best_distance:
            best, best_distance = candidate_seconds, distance
    return best if best is not None else seconds


def humanize(
    performance: DrumPerformance,
    *,
    timing_ms: float = 6.0,
    velocity_spread: int = 8,
    seed: int = 0,
    exclude: tuple[str, ...] = ("kick",),
) -> DrumPerformance:
    """Put a controlled amount of life back after a hard quantise.

    Fully-quantised drums with identical velocities read as a drum machine, which
    is fine for some songs and wrong for most of ours. ``exclude`` keeps chosen
    instruments dead on the grid — the kick by default, because that is what the
    bass player locks to.
    """
    rng = random.Random(seed)
    out: list[Hit] = []
    for hit in performance.sorted_hits():
        if hit.instrument in exclude:
            out.append(hit)
            continue
        jitter = rng.uniform(-timing_ms, timing_ms) / 1000.0
        velocity = hit.velocity + rng.randint(-velocity_spread, velocity_spread)
        out.append(hit.moved_to(hit.time + jitter).with_velocity(velocity))
    return DrumPerformance(out, performance.timeline, performance.name)


def shape_velocities(
    performance: DrumPerformance,
    *,
    accents: dict[str, int] | None = None,
    downbeat_boost: int = 0,
    floor: int = 20,
    ceiling: int = 127,
) -> DrumPerformance:
    """Set per-instrument base velocities and optionally accent downbeats.

    Transcribed velocities come from onset strength, which is only loosely
    related to how hard the drummer hit. Flattening them per instrument and then
    accenting musically usually sounds better than trusting the analysis.
    """
    accents = accents or {}
    timeline = performance.timeline
    out: list[Hit] = []
    for hit in performance.sorted_hits():
        velocity = accents.get(hit.instrument, hit.velocity)
        if downbeat_boost:
            _, beat = timeline.seconds_to_bar_beat(hit.time)
            if abs(beat - 1.0) < 0.05:
                velocity += downbeat_boost
        out.append(hit.with_velocity(max(floor, min(ceiling, velocity))))
    return DrumPerformance(out, timeline, performance.name)


def trim_to_bars(
    performance: DrumPerformance,
    first_bar: int = 1,
    last_bar: int | None = None,
) -> DrumPerformance:
    """Drop hits outside a bar range — for cutting count-in noise or long tails."""
    timeline = performance.timeline
    start = timeline.bar_beat_to_seconds(first_bar, 1.0) - 1e-6
    end = timeline.bar_beat_to_seconds(last_bar + 1, 1.0) if last_bar else float("inf")
    kept = [h for h in performance.hits if start <= h.time < end]
    return DrumPerformance(kept, timeline, performance.name)
