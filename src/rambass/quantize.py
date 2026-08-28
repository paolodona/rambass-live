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

#: The crash family. Cymbals are never snapped as tight as the rest of the kit:
#: a crash is heard as an event, not as a subdivision, and pulling one 80 ms to
#: a 16th is audible as a flam against whatever it was crashed with.
CYMBALS: frozenset[str] = frozenset({"crash", "crash_2", "china", "splash"})

#: Instruments that are usually played on a coarser grid than the hats.
#:
#: This table is **absolute**, and it lists every instrument except the toms, so
#: it wins over :attr:`QuantizeSettings.subdivision` for practically the whole
#: kit. Callers that want to change the grid must pass ``per_instrument`` --
#: setting ``subdivision`` alone moves nothing but the toms. ``rambass drums
#: clean`` builds that table from its own flags for exactly this reason.
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

#: The instruments :attr:`ConsolidateSettings.phantom_snare_velocity` applies to.
#:
#: **One entry, and the asymmetry is the whole finding.** Velocity-floor hits in
#: the six sections consolidate skipped on Manlio, against the review pass:
#:
#: ============  ====  =======  ====  =============
#: instrument    hits  phantom  real  in the rule?
#: ============  ====  =======  ====  =============
#: snare         7     **6**    1     **yes**
#: hihat_closed  11    0        11    no
#: sidestick     3     1        2     **no**
#: hihat_open    2     0        2     no
#: tom_mid       2     0        2     no
#: kick          1     0        1     no
#: ============  ====  =======  ====  =============
#:
#: The snares Paolo kept in those sections run a median of v107. The hats, kicks
#: and toms down at the floor genuinely play that quietly, and generalising the
#: rule to them scores 155 against 140 — worse than not having it at all.
#:
#: This is the existing "a hi-hat is never loudness evidence" rule pointed the
#: other way (see :func:`~rambass.transcribe.suppress_cross_stem_bleed`, whose
#: ``exclude`` list means "quiet by nature"): **a snare is the one instrument
#: whose own quietness is evidence against it**, because this drummer's soft
#: strokes on that drum are side-sticks and ghost notes that land in other lanes.
#:
#: ``sidestick`` is excluded deliberately and it was measured, not assumed:
#: adding it drops three more hits of which only one is a phantom, and the score
#: gets worse — 141 against 140. A rim click is 25-30 dB below the same drummer's
#: snare (``transcribe.SIDESTICK_BODY_SHARE``, ``drums.backbeat_velocity``), so
#: the velocity floor is where a *real* side-stick lives. It is a quiet
#: instrument, not a quiet stroke, and folding the two together is the obvious
#: "simplification" that costs real hits.
PHANTOM_FLOOR_INSTRUMENTS: tuple[str, ...] = ("snare",)

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


@dataclass(frozen=True)
class UnresolvedHit:
    """A hit :func:`quantize` left where it was because it sits too far from
    the grid to snap in good conscience.

    Leaving the hit physically untouched was already right -- forcing
    anything the tolerance rejects onto a grid line is how a deliberate push,
    a triplet or a flam gets ironed flat. Leaving it *unnamed* was not:
    nothing above this function ever saw which hit it was or how far off it
    sat, so a transcription error and an intentional syncopation landed in the
    same silent "left alone" count. Every one of these needs a human's call --
    syncopation, a flam, a triplet, or simply wrong -- and this is the list
    that lets them get one instead of riding through to the final MIDI
    unexamined.
    """

    bar: int
    beat: float
    instrument: str
    velocity: int
    distance_ms: float
    tolerance_ms: float


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
    unresolved: list[UnresolvedHit] = []

    for hit in performance.sorted_hits():
        subdivision = settings.subdivision_for(hit.instrument)
        target = _nearest_grid_time(timeline, hit.time, subdivision, settings.swing)
        distance = abs(target - hit.time)
        step_seconds = _beat_seconds_at(timeline, hit.time) / subdivision
        tolerance = settings.tolerance_steps * step_seconds

        if not settings.force and distance > tolerance:
            skipped += 1
            bar, beat = timeline.seconds_to_bar_beat(hit.time)
            unresolved.append(UnresolvedHit(
                bar=bar, beat=round(beat, 3), instrument=hit.instrument,
                velocity=hit.velocity,
                distance_ms=round(distance * 1000.0, 1),
                tolerance_ms=round(tolerance * 1000.0, 1),
            ))
            moved.append(hit)
            continue

        new_time = hit.time + (target - hit.time) * settings.strength
        total_shift += abs(new_time - hit.time)
        largest = max(largest, abs(new_time - hit.time))
        moved.append(hit.moved_to(new_time))

    report = {
        "hits": len(moved),
        "left_alone": skipped,
        "unresolved": unresolved,
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


@dataclass(frozen=True)
class ConsolidateSettings:
    """Knobs for :func:`consolidate`."""

    subdivision: int = 4
    #: Keep a hit only where it appears in at least this share of the
    #: repetitions. docs/drums-rebuild.md Stage 6 argues for 0.5-0.6: what falls
    #: below it is unintended variation from the drummer and transcription
    #: error, neither of which repeats reliably.
    threshold: float = 0.55
    #: Fewest repetitions a section needs before its pattern is voted on at all.
    #:
    #: **Not 2.** At n=2 a hit must appear in *both* repetitions to clear a 0.55
    #: threshold, because 1 of 2 is 50% — that is unanimity, not agreement, and
    #: anything that varies in the slightest is deleted. Measured on Manlio,
    #: where the breaks and fills are 2 bars each, it removed a third to a half
    #: of every one of them: theme-intro-stop went from 15 hits to 6 at 40%
    #: agreement. At 4 a hit in 3 of 4 survives and one in 1 of 4 does not,
    #: which is a vote.
    #:
    #: Below the minimum a section is passed through untouched and reported, and
    #: that is the right outcome: a 2-bar break is a one-off, and Stage 7 of
    #: docs/drums-rebuild.md expects to place those by hand anyway.
    min_repeats: int = 4
    #: 0 = work out whether the section repeats every bar or every two.
    unit_bars: int = 0
    #: Prefer the one-bar unit unless two bars explain the section this much
    #: better. Any one-bar pattern is also a valid two-bar pattern, so without a
    #: margin the longer unit always ties and half the sections come out wrong.
    unit_margin: float = 0.05
    #: Withhold the stamp after a bar's own playing stops, if it stops at least
    #: this many beats before that bar's slots run out. 0 disables the rule.
    #:
    #: :func:`consolidate` owns the fill problem in its own docstring. This is
    #: the mirror case, and it was undocumented: the voted pattern is stamped
    #: into **every** repetition including the last, which is the one least
    #: likely to repeat, because it is where the band lifts off into the break.
    #: Measured on Manlio, bar 17 (last of ``verse-1-lift``): the input's
    #: detections stop dead at beat 3 and the vote stamps 3.333, 3.667, 4.0 hat,
    #: 4.0 snare, 4.333 and 4.667 over the silence. Bars 32 and 54 are the same
    #: bar of ``verse-2-lift`` and ``verse-3-lift``.
    #:
    #: **The constant is 1.25 beats.** The sweep, scored against
    #: ``drums-restored.mid``:
    #:
    #: ==========  =======  =======  ===========  ======
    #: stop_beats  dropped  phantom  real killed  errors
    #: ==========  =======  =======  ===========  ======
    #: 0.0 (off)   0        0        0            154
    #: 0.5         29       10       19           163
    #: 1.0         27       10       17           161
    #: **1.25**    **11**   **10**   **1**        **145**
    #: 1.5         11       10       1            145
    #: 2.0         11       10       1            145
    #: ==========  =======  =======  ===========  ======
    #:
    #: It is not near 1 by accident. Below a beat the rule starts eating the
    #: ordinary one-slot gaps consolidation exists to fill — the drummer who
    #: missed one hat at the end of a bar, which is the whole reason for the
    #: vote. A **whole beat of silence** inside a bar of continuous triplets is
    #: not a dropout, it is a stop, and a drummer who stopped is information
    #: rather than a detection failure: the same argument ``bars`` makes for
    #: bounding the part (CLAUDE.md).
    #:
    #: At 1.25 it fires on exactly three bars of Manlio, all three the last bar
    #: of a section, removing ten phantoms for one real hit lost. It is a
    #: plateau rather than a peak — 1.25, 1.5 and 2.0 fire on the same three
    #: bars — so the number is not balanced between two songs.
    #:
    #: The one thing it assumes is a **dense** grid: Manlio plays twelve hat
    #: slots to the bar, so a beat and a quarter of silence is the band
    #: stopping. On a pattern that only plays the four beats, a bar that merely
    #: loses its beat-4 hit has two beats of silence behind it and reads as a
    #: stop. ``stop_beats=0`` is the escape hatch for such a song, and the
    #: sweep says a smaller number is not (below a beat is worse than not doing
    #: it at all on real material).
    stop_beats: float = 1.25
    #: In a section too short to vote on, drop
    #: :data:`PHANTOM_FLOOR_INSTRUMENTS` hits at or below this velocity.
    #: 0 disables it, and that is **not** velocity 0 — same convention as
    #: ``drums.backbeat_velocity``.
    #:
    #: :attr:`min_repeats` means a 2-bar break is passed through untouched, so
    #: every detection artefact in it survives. On Manlio ``break-1`` and
    #: ``break-3`` are the second-densest edit region in the song — 26 review
    #: notes, 17 of them in bar 63 alone — and seven of those hits are snares at
    #: the velocity floor, six of which are phantoms. See
    #: :data:`PHANTOM_FLOOR_INSTRUMENTS` for the table and for why it is the
    #: snare alone.
    #:
    #: **50, and it is the floor plus slack rather than the best score.**
    #: ``scale_velocities`` puts Manlio's floor at v45, so "at or below 50"
    #: means *at the floor* — a boundary with a reason. 55 would also catch the
    #: v53 phantom at bar 19 beat 4.809 and score 139 against 140, and is
    #: deliberately not taken: one drummer and one kit means this number has to
    #: hold for the other ten songs of the album, and above the floor it starts
    #: deleting strokes that carry a measured level.
    #:
    #: It applies **only** where the section was skipped. A voted section
    #: already has a better instrument than level, namely agreement, and must
    #: not be second-guessed on loudness.
    phantom_snare_velocity: int = 50


@dataclass(frozen=True)
class BarStop:
    """A bar whose own playing stops well before its slots run out.

    ``low``/``high`` are the window the stop was measured in — a bar's extent
    intersected with the section's, since a section may begin or end mid-bar and
    the question is where the playing stopped *inside this part*.
    """

    bar: int
    beat: float
    stopped_at: float
    low: float
    high: float

    def withholds(self, time: float) -> bool:
        """Is *time* inside this window and after the playing stopped?"""
        return (self.low - 1e-9 <= time < self.high - 1e-9
                and time > self.stopped_at + 1e-9)


def stopped_bars(
    timeline: Timeline,
    hits,
    *,
    low: float,
    high: float,
    stop_beats: float,
) -> list[BarStop]:
    """Which bars in ``[low, high)`` stop playing early, and where.

    Shared by :func:`consolidate` (which withholds its stamp after a stop) and
    :func:`~rambass.restore.fill_hat_runs` (which withholds a declared hat
    pattern for the same reason) — deliberately one helper, because a declared
    pattern that ignored the stop would put twelve hats straight back into the
    bar Stage 6 had just cleared.

    The window is a bar intersected with ``[low, high)``, so a section that
    starts on beat 3 asks whether its two beats of that bar stopped early rather
    than measuring against a bar line the section does not reach. Manlio's
    ``chorus-1`` starts at bar 32 beat 3, which makes ``verse-2-lift``'s last
    bar two beats long.

    **A bar with no hits at all is not a stop.** There has to be playing for it
    to stop after: an empty bar is precisely the case Stage 6's vote exists for,
    and withholding the pattern there would delete a whole bar of the part on
    the strength of a detection failure.
    """
    if stop_beats <= 0:
        return []
    times = sorted(hit.time for hit in hits)
    out: list[BarStop] = []
    bar = max(timeline.seconds_to_bar_beat(low)[0], 1)
    while True:
        bar_start = timeline.bar_beat_to_seconds(bar, 1.0)
        if bar_start >= high - 1e-9:
            break
        bar_end = timeline.bar_beat_to_seconds(bar + 1, 1.0)
        window_low, window_high = max(bar_start, low), min(bar_end, high)
        if window_high - window_low > 1e-9:
            beats_per_bar = timeline.time_signature_at(bar)[0]
            beat_seconds = (bar_end - bar_start) / beats_per_bar
            inside = [t for t in times
                      if window_low - 1e-9 <= t < window_high - 1e-9]
            if inside:
                last = inside[-1]
                if window_high - last >= stop_beats * beat_seconds - 1e-9:
                    _, beat = timeline.seconds_to_bar_beat(last)
                    out.append(BarStop(bar=bar, beat=round(beat, 3),
                                       stopped_at=last,
                                       low=window_low, high=window_high))
        bar += 1
    return out


@dataclass(frozen=True)
class SectionSpan:
    """A section's extent for :func:`consolidate`, bar lines optional.

    ``end_bar`` is exclusive. The beats default to the downbeat, so a plain
    ``(name, first_bar, last_bar)`` tuple still describes a section and every
    existing caller keeps working — see :meth:`of`.
    """

    name: str
    start_bar: int
    end_bar: int
    start_beat: float = 1.0
    end_beat: float = 1.0

    @classmethod
    def of(cls, item: SectionSpan | tuple) -> SectionSpan:
        if isinstance(item, SectionSpan):
            return item
        name, first, last = item
        return cls(str(name), int(first), int(last))


def consolidate(
    performance: DrumPerformance,
    sections,
    *,
    settings: ConsolidateSettings | None = None,
) -> tuple[DrumPerformance, dict]:
    """Replace each section with the pattern its repetitions agree on.

    This is Stage 6 of docs/drums-rebuild.md, and it is what decides whether the
    result sounds programmed or transcribed. Quantising fixes *when* a hit
    happens but not *whether* it should be there, so without this every
    unintended inconsistency in the take survives, and every transcription error
    is independent per bar: bar 11 of the verse loses a hat, bar 13 gains a
    phantom tom. On the grid, and incoherent.

    So: overlay every repetition of the section's repeating unit, keep a hit only
    where enough repetitions agree, at the modal slot and the median velocity of
    its group, and stamp that across the section.

    *sections* is a list of :class:`SectionSpan`, or of plain
    ``(name, first_bar, last_bar)`` tuples for a section that begins and ends on
    a bar line. **A span need not do either**, and Paolo's point about that is
    the governing one: "it is important that consolidate consolidates within the
    section, regardless of where it starts (with odd timing we will rarely fit
    into a .1 start of section generally)".

    The repetitions still tile the **bar** grid — a one-bar drum figure repeats
    every bar whichever beat the section began on, because the bar line is where
    beat 1 is and a section boundary does not move it — but the section's own
    edges decide which slots exist in the first and last repetition. A slot is
    therefore judged against the repetitions it *could* have appeared in rather
    than against the total, so the half-bars at either end are voted on like
    everything else instead of being rounded away and left unconsolidated inside
    a consolidated section.

    **Sections sharing a name are pooled**, which is Paolo's other convention:
    "if the sections are named exactly the same, use exactly the same part, if
    they are the same name pattern (eg: verse-2 vs verse-3) check the structure
    but should not match exactly." Two sections both called ``chorus`` vote as
    one and come out identical; ``verse-2`` and ``verse-3`` vote separately and
    are free to differ, because they are structurally alike but the later one
    adds hits for the dynamics of the song. Pooling is on the exact name and
    never on a prefix family.

    Hits outside every section are passed through untouched — as are sections
    too short to vote on, which are reported rather than silently mangled. The
    one exception there is a snare at the velocity floor, which in an unvoted
    section is a phantom six times out of seven: see
    :attr:`ConsolidateSettings.phantom_snare_velocity` and
    :data:`PHANTOM_FLOOR_INSTRUMENTS`, which is one instrument long for reasons
    that were measured.

    **This deliberately removes the fills**, which is why Stage 7 says to put
    them back by hand rather than repair them: a fill is by definition the bar
    that does not repeat, so no threshold can keep it and be doing its job.

    **And it stops stamping where the band stopped**, which is the mirror of
    that and was undocumented until it cost a third of Manlio's extra-hit
    edits: the pattern was stamped into *every* repetition including the last,
    where the band has already lifted off into the break. See
    :attr:`ConsolidateSettings.stop_beats` for the measurement and the sweep,
    and :func:`stopped_bars` for what counts as a stop. Withheld hits are named
    per section in the report's ``stopped`` list, because a silent behaviour
    change here reads as a bug six months later.

    **A slot's vote is per instrument, but the stamp is checked as a chord.**
    Codex's review (Aug 2026) named the risk precisely: voting independently
    per instrument can combine "the kick from one repetition, the snare ghost
    note from another, the hats from a third" into something nobody ever
    played, because two instruments can each individually clear the threshold
    at a slot without ever landing there in the same bar. So when more than
    one instrument survives its own vote at a slot, :func:`_resolve_slot_chord`
    checks whether that combination was actually played together often enough
    to clear the same threshold as a chord; if not, only the single
    best-attested instrument is kept and the rest are named in the section's
    ``demoted`` list rather than stamped on faith. A section with no such
    conflicts reports no ``demoted`` entries at all.
    """
    settings = settings or ConsolidateSettings()
    timeline = performance.timeline
    report: dict = {"sections": [], "hits_before": len(performance.hits), "untouched": 0}

    grouped: dict[str, list[SectionSpan]] = {}
    for item in sections:
        span = SectionSpan.of(item)
        grouped.setdefault(span.name, []).append(span)

    claimed: list[tuple[float, float]] = []
    produced: list[Hit] = []

    for name, group in grouped.items():
        extents = [(timeline.bar_beat_to_seconds(s.start_bar, s.start_beat),
                    timeline.bar_beat_to_seconds(s.end_bar, s.end_beat))
                   for s in group]
        inside = [h for h in performance.hits
                  if any(lo - 1e-9 <= h.time < hi - 1e-9 for lo, hi in extents)]
        entry: dict = {"name": name, "spans": len(group),
                       "bars": (group[0].start_bar, group[0].end_bar),
                       "hits_before": len(inside)}

        best = None
        for unit in ((settings.unit_bars,) if settings.unit_bars else (1, 2)):
            reps = [rep for span in group
                    for rep in _repetitions(timeline, span, unit, settings.subdivision)]
            if len(reps) < max(settings.min_repeats, 2):
                continue
            hits, coverage, demoted = _vote(reps, inside, settings)
            score = coverage - (settings.unit_margin if unit > 1 else 0.0)
            if best is None or score > best[0]:
                best = (score, unit, len(reps), hits, coverage, demoted, reps)

        if best is None:
            entry["skipped"] = "too short to vote on"
            # Nothing was voted on here, so nothing has agreement to stand on
            # and every detection artefact in the section survives. The one
            # thing level *can* settle is a snare at the floor -- see
            # PHANTOM_FLOOR_INSTRUMENTS for why it is the snare and nothing
            # else. Claiming the extents is what lets a hit be dropped: the
            # pass-through at the bottom keeps whatever no section claimed.
            floor = settings.phantom_snare_velocity
            phantoms = [h for h in inside
                        if floor > 0 and h.instrument in PHANTOM_FLOOR_INSTRUMENTS
                        and h.velocity <= floor]
            if phantoms:
                entry["phantom_snares"] = len(phantoms)
                entry["hits_after"] = len(inside) - len(phantoms)
                claimed.extend(extents)
                produced.extend(h for h in inside if h not in phantoms)
                report["untouched"] += len(inside) - len(phantoms)
            else:
                report["untouched"] += len(inside)
            report["sections"].append(entry)
            continue

        _, unit, reps_used, hits, coverage, demoted, reps = best

        # The stamp stops where the playing stopped. Only here, where the vote
        # actually stamps something: a section reported as `skipped` is passed
        # through untouched, so there is nothing to withhold.
        stops = [stop for lo, hi in extents
                 for stop in stopped_bars(timeline, inside, low=lo, high=hi,
                                          stop_beats=settings.stop_beats)]
        if stops:
            withheld: dict[int, int] = {}
            keep_hits: list[Hit] = []
            for hit in hits:
                blocking = next((s for s in stops if s.withholds(hit.time)), None)
                if blocking is None:
                    keep_hits.append(hit)
                else:
                    withheld[blocking.bar] = withheld.get(blocking.bar, 0) + 1
            hits = keep_hits
            reported = [{"bar": s.bar, "beat": s.beat,
                         "withheld": withheld.get(s.bar, 0)}
                        for s in stops if withheld.get(s.bar)]
            if reported:
                entry["stopped"] = reported

        claimed.extend(extents)
        produced.extend(hits)
        entry.update(unit_bars=unit, repeats=reps_used, hits_after=len(hits),
                     coverage=round(coverage, 3))
        if demoted:
            entry["demoted"] = []
            for item in demoted:
                rep = next(r for r in reps if r.eligible[item["slot"]])
                bar_no, beat_no = timeline.seconds_to_bar_beat(rep.grid[item["slot"]])
                entry["demoted"].append({
                    "bar": bar_no, "beat": round(beat_no, 3),
                    "kept": item["kept"], "dropped": item["dropped"],
                    "joint_coverage": item["joint_coverage"],
                })
        report["sections"].append(entry)

    kept = [h for h in performance.hits
            if not any(lo - 1e-9 <= h.time < hi - 1e-9 for lo, hi in claimed)]
    report["untouched"] += len(kept)
    hits = sorted(kept + produced, key=lambda h: (h.time, h.instrument))
    report["hits_after"] = len(hits)
    return DrumPerformance(hits, timeline, performance.name), report


@dataclass(frozen=True)
class _Repetition:
    """One turn of the repeating unit: its grid, and which slots are in bounds."""

    grid: tuple[float, ...]
    eligible: tuple[bool, ...]
    low: float
    high: float


def _repetitions(
    timeline: Timeline,
    span: SectionSpan,
    unit: int,
    subdivision: int,
) -> list[_Repetition]:
    """Tile *span* with bar-aligned units of *unit* bars.

    The tiling starts at the bar **containing** the section start, so the first
    and last repetitions may lie partly outside it. Which of their slots are
    really in the section is recorded per slot rather than trimmed away, because
    those slots are part of the pattern and the section's hits live in them.
    """
    start = timeline.bar_beat_to_seconds(span.start_bar, span.start_beat)
    end = timeline.bar_beat_to_seconds(span.end_bar, span.end_beat)
    whole = span.end_bar - span.start_bar + (1 if span.end_beat > 1.0 else 0)
    count = -(-whole // unit)                      # ceil: tile past the end
    out: list[_Repetition] = []
    for index in range(max(count, 0)):
        first = span.start_bar + index * unit
        grid = timeline.grid_seconds(subdivision, first, first + unit)
        eligible = tuple(start - 1e-9 <= g < end - 1e-9 for g in grid)
        if not any(eligible):
            continue
        out.append(_Repetition(
            tuple(grid), eligible,
            timeline.bar_beat_to_seconds(first, 1.0),
            timeline.bar_beat_to_seconds(first + unit, 1.0),
        ))
    return out


def _resolve_slot_chord(
    instruments: list[str],
    votes: dict[tuple[str, int], list[Hit]],
    chords: list[set[str]],
    chances: int,
    slot: int,
    threshold: float,
) -> tuple[list[str], list[str], float | None]:
    """Which of *instruments* -- each already past its own vote at *slot* --
    were actually played together often enough to be stamped as one chord.

    Each instrument here individually clears the per-slot vote, which is
    exactly how a fabricated combination sneaks through: a snare and a hi-hat
    can each independently be "what usually happens" at a slot without ever
    landing there in the same bar. So the full set is checked against what the
    repetitions actually played together, and if it does not hold up, only the
    single best-attested instrument is kept -- the rest are reported, not
    guessed away. Returns (kept, dropped, joint_coverage); *joint_coverage* is
    ``None`` when there was only ever one instrument to consider.
    """
    if len(instruments) <= 1:
        return list(instruments), [], None
    ranked = sorted(instruments, key=lambda i: len(votes[(i, slot)]), reverse=True)
    needed = set(ranked)
    joint = sum(1 for chord in chords if needed <= chord)
    coverage = joint / chances if chances else 0.0
    if coverage >= threshold:
        return ranked, [], coverage
    return ranked[:1], ranked[1:], coverage


def _vote(
    reps: list[_Repetition],
    hits: list[Hit],
    settings: ConsolidateSettings,
) -> tuple[list[Hit], float, list[dict]]:
    """Overlay the repetitions and keep the slots enough of them agree on."""
    slots = min(len(rep.grid) for rep in reps)
    # How many repetitions each slot could have appeared in. A slot at the edge
    # of a mid-bar section exists in fewer of them, and holding it to the same
    # share as a slot in the middle would delete it for being where it is.
    chances = [sum(1 for rep in reps if rep.eligible[i]) for i in range(slots)]

    votes: dict[tuple[str, int], list[Hit]] = {}
    # Per slot, what each repetition actually played there together -- not
    # just which instruments cleared their own vote, but which of them were
    # ever in the same bar at once. This is what a per-instrument vote alone
    # cannot see.
    chords: list[list[set[str]]] = [[] for _ in range(slots)]
    for rep in reps:
        allowed = [i for i in range(slots) if rep.eligible[i]]
        if not allowed:
            continue
        rep_chord: dict[int, set[str]] = {}
        for hit in hits:
            if not (rep.low - 1e-9 <= hit.time < rep.high - 1e-9):
                continue
            slot = min(allowed, key=lambda i: abs(rep.grid[i] - hit.time))
            votes.setdefault((hit.instrument, slot), []).append(hit)
            rep_chord.setdefault(slot, set()).add(hit.instrument)
        for slot in allowed:
            chords[slot].append(rep_chord.get(slot, set()))

    placed = sum(len(group) for group in votes.values())
    qualifying: dict[int, list[str]] = {}
    for (instrument, slot), group in votes.items():
        # One repetition can hit the same slot twice (a flam the de-flam missed);
        # agreement is about how many *repetitions* played it, not how many hits.
        if not chances[slot] or len(group) / chances[slot] < settings.threshold:
            continue
        qualifying.setdefault(slot, []).append(instrument)

    out: list[Hit] = []
    agreed = 0
    demoted: list[dict] = []
    for slot, instruments in sorted(qualifying.items()):
        keep, dropped, joint_coverage = _resolve_slot_chord(
            instruments, votes, chords[slot], chances[slot], slot, settings.threshold)
        if dropped:
            demoted.append({
                "slot": slot, "kept": keep[0], "dropped": dropped,
                "joint_coverage": round(joint_coverage, 3),
            })
        for instrument in keep:
            group = votes[(instrument, slot)]
            agreed += len(group)
            velocity = sorted(h.velocity for h in group)[len(group) // 2]
            for rep in reps:
                if rep.eligible[slot]:
                    out.append(Hit(instrument, rep.grid[slot], velocity))
    coverage = agreed / placed if placed else 0.0
    return out, coverage, demoted


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
