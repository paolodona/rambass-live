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

from dataclasses import dataclass, replace
from statistics import median

from .drummap import CANONICAL
from .manifest import Addition, Removal  # noqa: F401  (re-exported)
from .midiio import DrumPerformance, Hit
from .project import ProjectError

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


#: A cymbal on a section start must be at least this loud to read as a crash
#: rather than a hi-hat that happened to land on the boundary. A crash is an
#: accent by definition, and the ten Manlio boundary crashes run v83-117 with one
#: outlier at v48.
CRASH_CANDIDATE_VELOCITY = 80
#: How far from a section start a cymbal may be and still be its crash, in beats.
CRASH_CANDIDATE_WINDOW = 0.6

#: The cymbals a section-boundary crash could be hiding inside. ``hihat_open`` is
#: the one that matters: :func:`~rambass.transcribe.split_cymbal_runs` renames
#: ambiguous cymbal-stem hits to it on purpose, so that is where the crashes went.
_CYMBALS = ("hihat_open", "crash", "crash_2", "china", "splash")

#: The instruments a missing-hits ledger reports by default.
#:
#: Not the groove. A hi-hat, kick, snare or side-stick that the vote dropped is
#: :func:`~rambass.quantize.consolidate` doing its job — regularising a section
#: that the transcriber read slightly differently in each bar is the entire point
#: of Stage 6, and putting those on a checklist is asking someone to undo it. The
#: first run on Manlio listed 204 items and about 170 were single hi-hats; the 6
#: crashes and 25 toms that a listener actually notices were buried in them.
#:
#: What a listener notices, and what Stage 7 exists to put back, is the accent and
#: the fill. Pass ``instruments=None`` for everything.
NOTICEABLE = (
    "crash", "crash_2", "crash_choke", "china", "china_choke", "splash",
    "ride", "ride_2", "ride_bell",
    "tom_low", "tom_low_mid", "tom_mid", "tom_high_mid", "tom_high", "tom_highest",
    "cowbell", "tambourine",
)


@dataclass(frozen=True)
class MissingHit:
    """One thing the part should probably have and does not.

    Musical bars, never Reaper's — :func:`checklist` converts once, at the edge.
    ``velocity`` is 0 when nothing was measured there at all, which is the
    difference between "this hit was deleted" and "this position is bare".
    """

    bar: int
    beat: float
    instrument: str
    velocity: int
    section: str
    reason: str

    @property
    def position(self) -> tuple[int, float, str]:
        return (self.bar, self.beat, self.instrument)


def _section_at(sections, bar: int, beat: float) -> str:
    """Name of the section covering a musical position, or the empty string."""
    covering = ""
    for section in sorted(sections, key=lambda s: (s.bar, getattr(s, "beat", 1.0))):
        if (section.bar, getattr(section, "beat", 1.0)) <= (bar, beat):
            covering = section.name
        else:
            break
    return covering


def missing_hits(
    before: DrumPerformance,
    after: DrumPerformance,
    sections,
    *,
    end_bar: int,
    subdivision: int,
    reason: str = "removed by consolidate",
    instruments=NOTICEABLE,
) -> list[MissingHit]:
    """Every hit in *before* that *after* does not have. Exact, and no audio.

    Both variants are quantised to the same grid, so matching is per
    ``(instrument, grid slot)`` rather than by proximity. That matters in both
    directions: :func:`~rambass.quantize.consolidate` places each survivor at its
    group's *modal* slot, which can be a subdivision away from where one
    particular bar played it, and calling that a deletion would bury the real
    losses in noise — while a hit a whole slot away really is a different hit.

    Hits the vote *added* are not reported. The vote filling a bar that missed a
    hit is it working; only the losses belong on a checklist.

    *instruments* defaults to :data:`NOTICEABLE` — the accents and the fills, not
    the groove. ``None`` reports everything.
    """
    timeline = before.timeline
    step = 60.0 / timeline.bpm / max(subdivision, 1) if timeline.bpm > 0 else 1.0

    def slot(hit: Hit) -> tuple[str, int]:
        return (hit.instrument, int(round(hit.time / step)))

    kept = {slot(hit) for hit in after.hits}
    out: list[MissingHit] = []
    for hit in before.hits:
        if slot(hit) in kept:
            continue
        if instruments is not None and hit.instrument not in instruments:
            continue
        bar, beat = timeline.seconds_to_bar_beat(hit.time)
        out.append(MissingHit(
            bar=bar, beat=round(beat, 3), instrument=hit.instrument,
            velocity=hit.velocity, section=_section_at(sections, bar, beat),
            reason=reason))
    return sorted(out, key=lambda item: item.position)


def crash_candidates(
    performance: DrumPerformance,
    sections,
    *,
    end_bar: int,
    window: float = CRASH_CANDIDATE_WINDOW,
    min_velocity: int = CRASH_CANDIDATE_VELOCITY,
) -> list[MissingHit]:
    """Section starts whose crash is hiding, or absent. Candidates, not verdicts.

    :func:`~rambass.transcribe.split_cymbal_runs` deliberately calls an ambiguous
    cymbal an open hi-hat, so the crashes marking section boundaries arrive
    labelled ``hihat_open``. The acoustic route to undoing that does not exist —
    three attempts are recorded in that function and a fourth here: decay at
    +250 ms across the 243 measurable cymbal-stem hits on Manlio has a median of
    -16.8 dB and a p90 of +3.0, with 114 of them ringing at or above -14 dB. Long
    ring is the *normal* case in that stem, because an open hat's wash lives there
    too, so no ring threshold can separate the two.

    What separates is musical position. The ten cymbal hits within 0.6 beat of a
    section start decay at a median **+3.4 dB** against **-19.2 dB** for the other
    233 — 23 dB apart — and nine of the ten are v83 or louder. So a loud cymbal on
    a boundary is a crash, and that is what this reports.

    A section start with no cymbal near it is reported too, with velocity 0. Six
    of Manlio's fifteen are like that and they are mostly the breaks, where a
    drummer drops out instead of accenting — a musical judgement, so it is
    reported rather than decided. A quiet cymbal on a boundary is reported the
    same way: a crash is an accent, and a hat that happened to land there is not.

    What this will *not* find is a crash played *into* a change rather than on it
    — Paolo's Reaper 19.3 is two beats before break-1. Widening the window to
    catch those triples the candidates and starts eating the hi-hat part, so they
    stay on the manual list.
    """
    timeline = performance.timeline
    beat_seconds = 60.0 / timeline.bpm if timeline.bpm > 0 else 0.5
    reach = window * beat_seconds
    out: list[MissingHit] = []
    for section in sorted(sections, key=lambda s: (s.bar, getattr(s, "beat", 1.0))):
        beat = getattr(section, "beat", 1.0)
        at = timeline.bar_beat_to_seconds(section.bar, beat)
        near = [hit for hit in performance.hits
                if hit.instrument in _CYMBALS and abs(hit.time - at) <= reach]
        loudest = max(near, key=lambda hit: hit.velocity, default=None)
        if loudest is not None and loudest.instrument.startswith("crash"):
            continue
        if loudest is not None and loudest.velocity >= min_velocity:
            out.append(MissingHit(
                bar=section.bar, beat=round(beat, 3), instrument="crash",
                velocity=loudest.velocity, section=section.name,
                reason=f"section start: a v{loudest.velocity} open hi-hat is here, "
                       f"which is where split_cymbal_runs puts a crash it could "
                       f"not name"))
            continue
        out.append(MissingHit(
            bar=section.bar, beat=round(beat, 3), instrument="crash",
            velocity=0, section=section.name,
            reason="section start: no cymbal here at all — either a crash, or a "
                   "deliberate drop-out"))
    return sorted(out, key=lambda item: item.position)


def checklist(
    title: str,
    items,
    *,
    count_in_bars: int,
    additions=(),
) -> str:
    """The ledger as a markdown checklist, in **Reaper** bar numbers.

    Reaper's numbering is the one place a ruler reading belongs (CLAUDE.md), and
    this is exactly that place: the document exists to be read next to the ruler
    while ticking things off. The stored data stays musical.

    An item already covered by an entry in ``drums.additions`` comes out ticked,
    so re-running the report after doing some of the work shows what is left
    rather than the same list again.
    """
    done = {(a.bar, round(a.beat, 3), a.instrument) for a in additions}
    lines = [f"# {title} — missing hits", ""]
    if not items:
        lines += ["Nothing missing. Every hit in the input variant survived the "
                  "vote, and every section start has its crash.", ""]
        return "\n".join(lines)
    lines += [
        f"Bar numbers are **Reaper's** ruler, with the {count_in_bars}-bar "
        f"count-in included. Tick an item by adding it to `drums.additions` in "
        f"`song.yaml` and running `rambass drums restore`, so that it survives "
        f"the next re-transcription.",
        "",
    ]
    section = None
    for group in group_missing(items):
        if group.section != section:
            section = group.section
            lines += ["", f"## {section or '(outside every section)'}", ""]
        mark = "x" if (group.bar, group.beat, group.instrument) in done else " "
        many = f" x{group.count}" if group.count > 1 else ""
        velocity = f" v{group.velocity}" if group.velocity else ""
        lines.append(
            f"- [{mark}] `{group.bar + count_in_bars}.{group.beat:g}` "
            f"**{group.instrument}**{many}{velocity} — {group.reason}")
    lines.append("")
    return "\n".join(lines)


def apply_edits(
    performance: DrumPerformance,
    *,
    additions=(),
    removals=(),
    window: float = 0.030,
) -> tuple[DrumPerformance, dict]:
    """Stage 7: apply the manifest's hand edits. Returns (performance, report).

    **Removals first**, so that replacing a hit is two lines of YAML rather than
    a puzzle about whether the addition survived its own removal. That is the
    commonest edit there is: the open hi-hat that split_cymbal_runs left on a
    section start comes out and a crash goes in.

    Idempotent, because the edits are declarative and the part is rebuilt from
    them: an addition already present is counted and not doubled, so running the
    command twice cannot build a flam. A removal that matches nothing is
    *reported* rather than ignored — a stale removal means the hit it named was
    deleted upstream by some later tuning change, and swallowing that hides the
    fact that the list has drifted away from the part.

    An addition with no velocity gets the median of that instrument's existing
    hits, which is better than any constant this module could pick: a crash added
    beside the ones the transcriber found should sit where those sit.
    """
    report: dict = {"added": 0, "removed": 0, "already_there": 0,
                    "velocity_from_median": 0, "stale_removals": []}
    if not additions and not removals:
        return performance, report

    timeline = performance.timeline
    hits = list(performance.hits)

    for removal in removals:
        at = timeline.bar_beat_to_seconds(removal.bar, removal.beat)
        doomed = [i for i, hit in enumerate(hits)
                  if abs(hit.time - at) <= window
                  and (not removal.instrument or hit.instrument == removal.instrument)]
        if not doomed:
            report["stale_removals"].append(
                (removal.bar, removal.beat, removal.instrument))
            continue
        hits = [hit for i, hit in enumerate(hits) if i not in set(doomed)]
        report["removed"] += len(doomed)

    for addition in additions:
        if addition.instrument not in CANONICAL:
            raise ProjectError(
                f"drums.additions names {addition.instrument!r}, which is not a "
                f"drum instrument; expected one of {', '.join(CANONICAL)}")
        at = timeline.bar_beat_to_seconds(addition.bar, addition.beat)
        if any(abs(hit.time - at) <= window and hit.instrument == addition.instrument
               for hit in hits):
            report["already_there"] += 1
            continue
        velocity = addition.velocity
        if not velocity:
            existing = [hit.velocity for hit in hits
                        if hit.instrument == addition.instrument]
            if existing:
                velocity = round(median(existing))
                report["velocity_from_median"] += 1
            else:
                velocity = 100
        hits.append(Hit(addition.instrument, at, max(1, min(127, int(velocity)))))
        report["added"] += 1

    hits.sort(key=lambda hit: (hit.time, hit.instrument))
    return DrumPerformance(hits, timeline, performance.name), report


@dataclass(frozen=True)
class MissingGroup:
    """Consecutive losses of one instrument in one bar, as one checklist line.

    A fill removed by the vote is eight tom hits and one decision. Listing it
    eight times makes a checklist that nobody can tick off — which was the state
    of the first run on Manlio: 204 items, of which the 6 crashes and 25 toms
    that a listener actually notices were buried.
    """

    bar: int
    beat: float
    instrument: str
    count: int
    velocity: int
    section: str
    reason: str

    @property
    def position(self) -> tuple[int, float, str]:
        return (self.bar, self.beat, self.instrument)


def group_missing(items) -> list[MissingGroup]:
    """Collapse a ledger to one line per (bar, instrument). Ordered by position.

    The beat reported is the earliest the group covers, because that is where you
    put the cursor. The velocity is the loudest, because that is the one that
    tells you whether it mattered.
    """
    buckets: dict[tuple[int, str], list] = {}
    for item in items:
        buckets.setdefault((item.bar, item.instrument), []).append(item)
    out = [
        MissingGroup(
            bar=bar, instrument=instrument,
            beat=min(item.beat for item in group),
            count=len(group),
            velocity=max(item.velocity for item in group),
            section=group[0].section,
            reason=group[0].reason,
        )
        for (bar, instrument), group in buckets.items()
    ]
    return sorted(out, key=lambda group: group.position)
