"""Stage 7: put back what the pipeline removed, or never named correctly.

Everything upstream of here is a detector with a bias, and every one of those
biases is deliberate. :func:`~rambass.transcribe.split_cymbal_runs` calls an
ambiguous cymbal an open hi-hat because a phantom crash on every beat ruins a
track and a missing crash is one accent to add. :func:`~rambass.quantize.consolidate`
deletes the fills because a fill is by definition the bar that does not repeat.
:func:`~rambass.transcribe.label_sidesticks` refuses to name a side-stick unless
two independent features agree, so it leaves some as snares.

The cost of all that is a part that is *coherent* and *incomplete*, and the thing
that makes it workable is that the omissions are knowable. This module is where
they are named, written down and put back — so that hand work survives the next
re-transcription instead of being drawn again in Reaper every time.

Pure functions over a :class:`~rambass.midiio.DrumPerformance`, no heavy deps,
same shape as ``quantize.py``.
"""

from __future__ import annotations

from dataclasses import replace
from statistics import median

from .midiio import DrumPerformance, Hit

#: Instruments a hand plays, loudest-wins among these at a declared backbeat.
#: A kick is a foot and is never the backbeat, whatever it coincides with.
_HANDS = (
    "snare", "snare_electric", "sidestick", "clap",
    "hihat_closed", "hihat_open",
    "crash", "crash_2", "china", "splash", "ride", "ride_2", "ride_bell",
)


def voice_backbeats(
    performance: DrumPerformance,
    sections,
    *,
    end_bar: int,
    velocity: int | None = None,
    window: float = 0.12,
    beats: tuple[int, ...] = (2, 4),
) -> tuple[DrumPerformance, dict]:
    """Apply each section's declared ``backbeat`` articulation.

    Paolo, on verse-2: *"some sound different than others but they are all the
    same concept: side stick rather than standard snares."* That is the fact this
    encodes, and it is declared rather than measured because measuring it does
    not work. Across all 941 hi-hat and cymbal stem detections on Manlio the
    hi-hat stem's 5-11 kHz share is 0.17 at a verse backbeat against 0.85-0.96
    everywhere else — a clean split, and it gets verse-1 and verse-3. In verse-2
    the same clicks read 0.84-1.00, which *is* a hi-hat's spectrum: Reaper 23.2,
    one of Paolo's own examples, is 0.84. No threshold recovers those without
    eating the real hi-hat part, and the reason is not a badly tuned constant —
    it is that a quiet cross-stick under a hi-hat leaves the separator nothing
    to hand over.

    So: for every bar of a declaring section, at each of *beats*, the loudest
    **hand** hit within *window* beats becomes the declared articulation and the
    other hand hits in that slot are dropped. Same reasoning as
    :func:`~rambass.transcribe.drop_hats_on_sidesticks`: what the detector
    reported at that instant is the stroke Paolo can hear, and keeping the rest
    leaves a loud accented hi-hat on every backbeat of the section.

    A kick is a foot, so it is never a candidate and never dropped. An empty slot
    is left empty and counted: :func:`~rambass.quantize.consolidate` stamps the
    section's agreed pattern across it, which puts the backbeat back from the
    bars that did have one — and does it at the slot the section actually plays,
    which is more than this function knows.

    *end_bar* is exclusive and bounds the last section, since a section list says
    where each part starts and nothing about where the last one stops.

    **The declared backbeat comes out even, and that is not laziness.** Measured
    on Manlio the first time this ran, verse-2's side-sticks were ``[45, 45, 45,
    60, 118, 118, 121, 122 x9]``. That spread is not dynamics, it is two
    incompatible scales in one section:
    :func:`~rambass.transcribe.scale_velocities` works per instrument against
    that instrument's own median, so a hit detected in the hi-hat stem carries a
    number meaning "loud for a hi-hat" — and once it is renamed to a rim click
    that number means nothing. The only population measured on the right
    instrument is the one the detector named from the snare stem (34 hits, median
    45, range 45-69), so its median is the reference and every declared slot gets
    it. 45 is the velocity floor and it is *right* on this scale: a rim click on
    this kit is 25-30 dB below the same drummer's snare, which is more than the
    whole velocity range at the 30 dB span ``scale_velocities`` uses. How loud it
    actually sounds is a Stage 8 decision about the kit's rim-click samples —
    pass *velocity* (``drums clean --backbeat-velocity``) to set it by ear.

    With no reference population at all the velocities are left alone and
    ``reference_velocity`` is ``None``, because there is no honest number to use.

    Returns (performance, report) with ``renamed``, ``dropped``, ``empty_slots``,
    ``reference_velocity`` and a per-section breakdown.
    """
    timeline = performance.timeline
    ordered = sorted(sections, key=lambda s: (s.bar, getattr(s, "beat", 1.0)))
    report: dict = {"renamed": 0, "dropped": 0, "empty_slots": 0,
                    "reference_velocity": velocity, "sections": []}
    if not ordered or not performance.hits:
        return performance, report

    hits = list(performance.hits)
    rename: dict[int, str] = {}
    revelocity: dict[int, int] = {}
    drop: set[int] = set()

    def reference_for(articulation: str) -> int | None:
        if velocity is not None:
            return int(velocity)
        already = [hit.velocity for hit in hits if hit.instrument == articulation]
        return round(median(already)) if already else None

    for index, section in enumerate(ordered):
        articulation = getattr(section, "backbeat", "") or ""
        if not articulation:
            continue
        start = timeline.bar_beat_to_seconds(section.bar, getattr(section, "beat", 1.0))
        if index + 1 < len(ordered):
            following = ordered[index + 1]
            stop = timeline.bar_beat_to_seconds(
                following.bar, getattr(following, "beat", 1.0))
        else:
            stop = timeline.bar_beat_to_seconds(end_bar, 1.0)

        reference = reference_for(articulation)
        report["reference_velocity"] = reference
        entry = {"name": section.name, "articulation": articulation,
                 "renamed": 0, "dropped": 0, "empty_slots": 0,
                 "velocity": reference}
        last_bar = end_bar if index + 1 >= len(ordered) else ordered[index + 1].bar + 1
        for bar in range(section.bar, max(last_bar, section.bar + 1)):
            for beat in beats:
                slot = timeline.bar_beat_to_seconds(bar, float(beat))
                if not (start - 1e-9 <= slot < stop - 1e-9):
                    continue
                reach = window * 60.0 / timeline.bpm if timeline.bpm > 0 else window
                here = [i for i, hit in enumerate(hits)
                        if hit.instrument in _HANDS
                        and abs(hit.time - slot) <= reach
                        and i not in drop]
                if not here:
                    entry["empty_slots"] += 1
                    continue
                keep = max(here, key=lambda i: (hits[i].velocity, -abs(hits[i].time - slot)))
                for i in here:
                    if i == keep:
                        continue
                    drop.add(i)
                    entry["dropped"] += 1
                if hits[keep].instrument != articulation:
                    rename[keep] = articulation
                    entry["renamed"] += 1
                if reference is not None and hits[keep].velocity != reference:
                    revelocity[keep] = reference
        for key in ("renamed", "dropped", "empty_slots"):
            report[key] += entry[key]
        report["sections"].append(entry)

    if not rename and not drop and not revelocity:
        return performance, report
    out: list[Hit] = []
    for i, hit in enumerate(hits):
        if i in drop:
            continue
        if i in rename:
            hit = replace(hit, instrument=rename[i])
        if i in revelocity:
            hit = hit.with_velocity(revelocity[i])
        out.append(hit)
    return DrumPerformance(out, timeline, performance.name), report
