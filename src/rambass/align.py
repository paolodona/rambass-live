"""Mapping bars to seconds *in the original recording*, and warping onto the grid.

The second deliberate exception to "positions are in bars, never seconds"
(CLAUDE.md). These seconds describe an immutable audio file rather than a
position in the musical grid, and only the **source** side is ever stored: the
target side is computed from :class:`~rambass.timeline.Timeline` at build time,
so a BPM edit keeps re-warping correctly instead of silently stopping.

Why piecewise rather than one offset. Manlio's own ``practice/align.yaml``
already recorded the limit: measured against the drum stem, the band's pulse
drifts **-88 to +258 ms** across the song. A reference laid at a single offset
lines up at the start and flams by a quarter second by the end, which reads as
"the click does not line up with the mix" exactly when someone is trying to judge
timing — and that is the one job this file has.

Scope, from docs/practice-tracks.md and CLAUDE.md: **this must never gate the
gig.** Nothing here writes to ``render/``, nothing here edits a musical field in
``song.yaml``, and no stage of it is in :data:`~rambass.manifest.STAGES`. A
warped reference is allowed to be imperfect; the drums are not.

Pure numpy, so it is testable without librosa, ffmpeg or an audio file. The beat
detection that feeds :func:`fit_anchors` is ``analyze``'s problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from .project import ProjectError
from .timeline import Timeline

#: Refuse a segment that has to stretch or squash by more than this. A bad anchor
#: makes an unlistenable file, and an unlistenable file reads as a tool bug rather
#: than as bad data — so it is an error with the bar number in it. The real drift
#: on this material is a few percent; 25% is far outside anything a band does.
MAX_RATE = 1.25
#: How close a detected beat has to be to a bar line to anchor it, in beats.
ANCHOR_TOLERANCE = 0.35


@dataclass(frozen=True)
class Anchor:
    """One musical position, and where it sits in the recording. Source only.

    ``beat`` exists because a bar is not fine enough. Measured on Manlio, warping
    the drum stem and then asking how far each backbeat lands from its grid line:
    a single offset gives a p90 of 107 ms, an anchor on every bar gives 58 ms, and
    an anchor on every *beat* gives 38 ms — under the 50 ms that
    docs/practice-tracks.md sets as the limit. A bar of this shuffle is four
    seconds long, so a linear segment across it cannot follow where the take put
    beats 2 and 4, and the bar lines being exact does not help the beats between
    them.
    """

    bar: int
    at: float
    beat: float = 1.0

    def to_dict(self) -> dict:
        out: dict = {"bar": self.bar}
        if self.beat != 1.0:
            out["beat"] = round(self.beat, 3)
        out["at"] = round(self.at, 3)
        return out

    @classmethod
    def from_dict(cls, data: dict) -> Anchor:
        return cls(bar=int(data["bar"]), at=float(data["at"]),
                   beat=float(data.get("beat", 1.0)))

    def grid_seconds(self, timeline: Timeline) -> float:
        """Where this anchor is on the *fixed* grid. Computed, never stored."""
        return timeline.bar_beat_to_seconds(self.bar, self.beat)

    @property
    def position(self) -> tuple[int, float]:
        return (self.bar, self.beat)


@dataclass
class AlignMap:
    """The anchors plus what was measured about them."""

    anchors: list = field(default_factory=list)
    source: str = ""
    detected_bpm: float = 0.0
    residual_max_ms: float | None = None
    residual_mean_ms: float | None = None
    note: str = ""

    @property
    def mode(self) -> str:
        """``none``, ``offset`` or ``piecewise`` — what this map can actually do."""
        if not self.anchors:
            return "none"
        return "offset" if len(self.anchors) == 1 else "piecewise"

    @property
    def offset(self) -> float:
        """Where musical bar 1 sits in the recording. Every mode has one."""
        if not self.anchors:
            return 0.0
        first = min(self.anchors, key=lambda a: a.position)
        return first.at

    def source_at(self, grid_seconds: float, timeline: Timeline) -> float:
        """Seconds into the recording for a position on the fixed grid.

        Linear between anchors, and extrapolated at the ends at the nearest
        segment's own rate — the honest thing, since a take that was drifting at
        bar 70 was probably still drifting at bar 78. Interpolating on *grid
        seconds* rather than on bar numbers is what lets an anchor sit mid-bar.
        """
        if not self.anchors:
            return grid_seconds
        ordered = sorted(self.anchors, key=lambda a: a.position)
        if len(ordered) == 1:
            return grid_seconds + ordered[0].at
        xs = np.array([a.grid_seconds(timeline) for a in ordered], dtype=float)
        ys = np.array([a.at for a in ordered], dtype=float)
        first_rate = _rate(xs[0], ys[0], xs[1], ys[1])
        last_rate = _rate(xs[-2], ys[-2], xs[-1], ys[-1])
        return float(np.interp(
            grid_seconds, xs, ys,
            left=ys[0] + (grid_seconds - xs[0]) * first_rate,
            right=ys[-1] + (grid_seconds - xs[-1]) * last_rate))

    def source_time(self, bar: float, timeline: Timeline) -> float:
        """Backwards-compatible bar-only form of :meth:`source_at`."""
        return self.source_at(timeline.bar_beat_to_seconds(int(bar), 1.0), timeline)


def _rate(x0: float, y0: float, x1: float, y1: float) -> float:
    span = x1 - x0
    return (y1 - y0) / span if span else 1.0


def fit_anchors(
    beats,
    timeline: Timeline,
    *,
    bars: int,
    every_beats: int = 4,
    tolerance: float = ANCHOR_TOLERANCE,
    start_at: float | None = None,
) -> list[Anchor]:
    """Anchor every *every_beats* beats to the detected beat nearest that line.

    In **beats**, not bars, and 4 is one anchor per bar in 4/4. Finer is better
    here up to the resolution of the beat detector itself — see :class:`Anchor`
    for the measurement.

    *beats* is a list of beat times in the recording — ``analyze``'s output, or
    anything else that says where the pulse was. Bars with no beat within
    *tolerance* beats of their line are **skipped rather than guessed**: a silent
    stretch has nothing to fit, and an invented anchor there warps audio nobody
    measured. :meth:`AlignMap.source_time` interpolates across the gap, which is
    the same answer with none of the false confidence.

    Each bar is predicted **from the previous anchor at the local rate**, and
    that is the whole design. Two obvious alternatives were tried first and both
    fail on exactly the material this exists for. Searching near each bar's
    *nominal* grid position cannot follow drift: at 1% per bar the take is 3.5
    beats away from the grid by bar 8, far outside any sane tolerance, so
    everything after the first few bars is skipped — a fitter that only works on
    takes that did not need fitting. Indexing straight into the beat list
    (bar *n* starts at beat ``(n-1) * beats_per_bar``) follows drift perfectly and
    breaks on a dropout instead: one missed beat shifts every later bar by a beat
    and the map comes out confidently wrong. Predicting forward and snapping to a
    real beat handles both, because the prediction tracks the take while the
    tolerance still refuses a bar the detector never saw.

    *start_at* is where bar 1 beat 1 is **known** to be, from
    :func:`~rambass.analyze.find_grid_anchor` or from an ear, and it is used
    exactly rather than snapped. Passing it matters more than it looks. The first
    *detected* beat is not bar 1: on Manlio's re-separated drum stem
    ``beat_track`` reports its first beat at **3.831 s**, because the song opens
    on a single kick that the new separation left less prominent, so the tracker
    starts three beats late. Chaining from ``times[0]`` then built the whole map
    3.1 s off — and patching bar 1 back afterwards, which is what the caller used
    to do, left a map whose first segment claimed 4.1 s of recording for one beat
    of grid. Without it, ``times[0]`` is the only thing available and is used, so
    a song nobody has measured yet still gets a usable map.

    Bar 1 is always attempted, because every downstream consumer needs to know
    where the music starts.
    """
    times = np.asarray(sorted(float(b) for b in beats), dtype=float)
    if not times.size or bars < 1:
        return []
    per_bar = timeline.time_signature[0]
    beat_seconds = 60.0 / timeline.bpm if timeline.bpm > 0 else 0.5
    reach = tolerance * beat_seconds
    step = max(int(every_beats), 1)

    out: list[Anchor] = []
    predicted, rate = float(times[0] if start_at is None else start_at), 1.0
    previous_grid: float | None = None
    previous_at = 0.0
    for index_beat in range(0, bars * per_bar, step):
        bar = index_beat // per_bar + 1
        beat = index_beat % per_bar + 1.0
        grid = timeline.bar_beat_to_seconds(bar, beat)
        if previous_grid is not None:
            predicted = previous_at + (grid - previous_grid) * rate
        # The known anchor is a measurement. Snapping it to whatever the tracker
        # happened to find nearby is how the 3.1 s error got in.
        if previous_grid is None and start_at is not None:
            anchor = Anchor(bar=bar, beat=beat, at=float(start_at))
            out.append(anchor)
            previous_grid, previous_at = grid, anchor.at
            continue
        index = int(np.argmin(np.abs(times - predicted)))
        if abs(times[index] - predicted) > reach:
            continue
        anchor = Anchor(bar=bar, beat=beat, at=float(times[index]))
        if previous_grid is not None and grid > previous_grid:
            rate = (anchor.at - previous_at) / (grid - previous_grid)
        out.append(anchor)
        previous_grid, previous_at = grid, anchor.at
    return out


def residual_ms(amap: AlignMap, beats, timeline: Timeline) -> tuple[float, float]:
    """``(worst, mean)`` error in ms between the map and the detected beats.

    The worst case is the number that matters and docs/practice-tracks.md sets 50
    ms as the limit: a mean hides the one bar that is 200 ms out, and that is the
    bar you would hear.
    """
    times = sorted(float(b) for b in beats)
    if not times or not amap.anchors:
        return (0.0, 0.0)
    beat_seconds = 60.0 / timeline.bpm if timeline.bpm > 0 else 0.5
    errors = []
    for at in times:
        # Snap this recorded beat to the grid beat it is nearest, then ask the
        # map where it thinks that grid beat is. The gap is the error.
        grid = round((at - amap.offset) / beat_seconds) * beat_seconds
        errors.append(abs(at - amap.source_at(grid, timeline)) * 1000.0)
    return (max(errors), sum(errors) / len(errors))


def residual_holdout_ms(amap: AlignMap, timeline: Timeline) -> tuple:
    """Leave-one-out error over the anchors. ``(worst, mean)`` in ms, or (None, None).

    :func:`residual_ms` compares the map with the beats it was fitted from, which
    is informative at one anchor every few bars and circular at one anchor per
    beat: the map passes exactly through every one of them. Manlio's first
    per-beat fit duly reported "0 ms worst / 0 ms mean" for 307 anchors, which is
    true and says nothing about whether the map is any good.

    So drop each interior anchor in turn and ask the remaining map where that
    beat should be. That measures the only thing interpolation can get wrong —
    the gap between anchors — and it is the number to compare against the 50 ms
    in docs/practice-tracks.md.

    Needs three anchors: with two there is nothing to leave out and still have a
    line, and refusing beats reporting a zero that means "not measured".
    """
    ordered = sorted(amap.anchors, key=lambda a: a.position)
    if len(ordered) < 3:
        return (None, None)
    errors = []
    for index in range(1, len(ordered) - 1):
        held = ordered[index]
        without = AlignMap(anchors=ordered[:index] + ordered[index + 1:])
        predicted = without.source_at(held.grid_seconds(timeline), timeline)
        errors.append(abs(held.at - predicted) * 1000.0)
    if not errors:
        return (None, None)
    return (max(errors), sum(errors) / len(errors))


@dataclass(frozen=True)
class WarpSegment:
    """One span of the recording, and the span of the grid it becomes."""

    source_start: float
    source_end: float
    target_start: float
    target_end: float

    @property
    def rate(self) -> float:
        """Source seconds per target second. >1 means the take was slower here."""
        span = self.target_end - self.target_start
        return (self.source_end - self.source_start) / span if span else 1.0


def warp_plan(amap: AlignMap, timeline: Timeline, *, bars: int) -> list[WarpSegment]:
    """Turn a map into segments to stretch. Target side computed, never stored.

    The last segment runs to the end of the song rather than to the last anchor,
    because the tail is music too. With a single anchor there is nothing to
    interpolate, so the whole song is one segment at rate 1.0 — an offset, which
    is what a one-anchor map is.
    """
    ordered = sorted(amap.anchors, key=lambda a: a.position)
    if not ordered:
        return []
    end_target = timeline.bar_beat_to_seconds(bars + 1, 1.0)
    if len(ordered) == 1:
        return [WarpSegment(source_start=ordered[0].at,
                            source_end=ordered[0].at + end_target,
                            target_start=0.0, target_end=end_target)]

    out: list[WarpSegment] = []
    edges = [(a.grid_seconds(timeline), a.at, a.position) for a in ordered]
    edges.append((end_target, amap.source_at(end_target, timeline), (bars + 1, 1.0)))
    for (grid, at, position), (next_grid, next_at, _) in zip(edges, edges[1:]):
        if next_grid <= grid:
            continue
        segment = WarpSegment(source_start=at, source_end=next_at,
                              target_start=grid, target_end=next_grid)
        if not 1.0 / MAX_RATE <= segment.rate <= MAX_RATE:
            raise ProjectError(
                f"the anchor at bar {position[0]} beat {position[1]:g} would "
                f"stretch its segment by {segment.rate:.2f}x, which is not "
                f"drift — check practice/align.yaml by ear, or re-fit it"
            )
        out.append(segment)
    return out


def plan_problem(amap: AlignMap, timeline: Timeline, *, bars: int) -> str | None:
    """The message :func:`warp_plan` would raise, or ``None`` if it would not.

    So that a fit can check its own output before overwriting a good map. Hit for
    real: ``align --fit --every-beats 4`` wrote a map whose first segment needed a
    1.77x stretch, the next ``--warp`` refused it, and by then the working map was
    gone. The fit is what knows it produced something unusable.
    """
    try:
        warp_plan(amap, timeline, bars=bars)
    except ProjectError as problem:
        return str(problem)
    return None


#: WSOLA frame length, seconds. Long enough to hold a couple of periods of the
#: lowest note that matters — a bass low E is 41 Hz, 24 ms a period — and short
#: enough that one frame sits inside one drum hit.
FRAME_SECONDS = 0.046
#: How far the analysis position may move to find the waveform that best
#: continues the previous output frame. This is also the most a transient can be
#: displaced by the warp, so it is the accuracy cost of preserving pitch: 10 ms,
#: against the 50 ms that docs/practice-tracks.md sets as the limit.
SEARCH_SECONDS = 0.010


def _source_clock(plan):
    """``target seconds -> source seconds``, continuous across the whole plan.

    The plan already *is* the piecewise-linear map, in the direction the render
    needs it. Rebuilding one interpolator over all of its edges — rather than
    walking segment by segment — is what makes the warp a single continuous pass
    over the output, so 308 segments are 308 rate changes rather than 308 seams.
    Off the ends it extrapolates at the nearest segment's own rate, the same
    choice :meth:`AlignMap.source_at` makes and for the same reason.
    """
    points: list[tuple[float, float]] = []
    for segment in plan:
        points.append((segment.target_start, segment.source_start))
        points.append((segment.target_end, segment.source_end))
    points.sort()
    edges_target, edges_source = [points[0][0]], [points[0][1]]
    for target, source in points[1:]:
        if target > edges_target[-1]:
            edges_target.append(target)
            edges_source.append(source)
    xs = np.asarray(edges_target, dtype=float)
    ys = np.asarray(edges_source, dtype=float)
    first = plan[0].rate
    last = plan[-1].rate

    def clock(target: float) -> float:
        return float(np.interp(
            target, xs, ys,
            left=ys[0] + (target - xs[0]) * first,
            right=ys[-1] + (target - xs[-1]) * last))

    return clock


def _read(samples: np.ndarray, start: int, length: int) -> np.ndarray:
    """*length* samples from *start*, zero-padded off either end.

    Reading past the end of the file yields silence rather than wrapping,
    because a take that stops before the grid does should go quiet, not repeat.
    Reading before the start is the same case: an anchor can sit later in the
    recording than the grid position it maps to.
    """
    out = np.zeros((length,) + samples.shape[1:], dtype=np.float32)
    low = max(start, 0)
    high = min(start + length, len(samples))
    if high > low:
        out[low - start:high - start] = samples[low:high]
    return out


def warp_samples(samples, sample_rate: int, plan, *,
                 frame_seconds: float = FRAME_SECONDS,
                 search_seconds: float = SEARCH_SECONDS) -> np.ndarray:
    """Stretch the recording onto the grid **without moving its pitch**.

    WSOLA: waveform-similarity overlap-add. Output frames go down at a fixed
    synthesis hop, so the output clock is exact; each one is *copied* from the
    recording at whatever position the map says that moment is, so the waveform
    keeps its own period and therefore its pitch; and the copy point is nudged
    within ±:data:`SEARCH_SECONDS` to the offset whose waveform best continues
    the frame already written, so consecutive frames join in phase instead of
    clicking.

    This function used to resample — read the source at ``position * rate`` and
    interpolate — and its docstring argued for that on the grounds that the
    rates are within a few percent of 1.0. That reasoning was about *timing* and
    never considered pitch, and it was wrong: resampling moves rate and pitch
    together, by ``12*log2(rate)`` semitones. Manlio's fitted map spans rates
    0.929–1.091 over 308 per-beat segments, so a 440 Hz tone came out at 408 Hz
    (−1.31 semitones) and at 480 Hz (+1.51), **2.78 semitones peak to peak,
    wobbling once per beat**. Unlistenable, in the one file whose only job is to
    be listened to.

    Why not the obvious alternatives. A phase vocoder is pitch-preserving and
    takes a varying rate naturally, but it smears transients — and this
    reference exists to judge whether programmed drums sit where the band
    played, so attack definition *is* the signal. ffmpeg ``atempo`` sounds good
    but takes one fixed rate per instance, which is 308 invocations and 308
    joins to click at, and it would move the render out of the pure-numpy tier
    so the tests would need ffmpeg. WSOLA is ~80 lines of numpy and keeps this
    module testable with no audio file on disk, which CLAUDE.md asks for.

    Mono ``(n,)`` in, mono out; ``(n, channels)`` in, the same shape out. The
    search runs once on the mixdown and its offset is applied to every channel:
    choosing per channel would decorrelate the sides and smear the image.
    """
    samples = np.asarray(samples, dtype=np.float32)
    if not len(plan):
        return samples
    mono = samples.ndim == 1
    data = samples[:, None] if mono else samples
    out_length = int(round(max(segment.target_end for segment in plan) * sample_rate))
    out = np.zeros((max(out_length, 0), data.shape[1]), dtype=np.float32)
    if out_length <= 0:
        return out[:, 0] if mono else out

    clock = _source_clock(plan)
    frame = max(8, 2 * int(round(frame_seconds * sample_rate / 2.0)))
    hop = frame // 2
    search = max(1, int(round(search_seconds * sample_rate)))
    # Periodic Hann at half-frame hop sums to exactly 1.0, so nothing needs
    # normalising afterwards — and the synthesis positions are fixed, so that
    # stays true however far the analysis offsets move.
    window = np.hanning(frame + 1)[:frame].astype(np.float32)
    probe = data.mean(axis=1)
    offsets = np.arange(-search, search + 1)
    # Break a tie towards the map's own answer. In silence every candidate
    # correlates at zero, and without this the first offset in the list wins.
    penalty = 0.01 * np.abs(offsets) / float(search)

    template: np.ndarray | None = None
    # Start half a frame early and finish half a frame late so the overlap-add
    # covers sample 0 and the final sample fully. Otherwise the first and last
    # 23 ms fade, and a downbeat at grid 0.0 lands inside the fade.
    for start in range(-hop, out_length + hop, hop):
        ideal = int(round(clock(start / sample_rate) * sample_rate))
        if template is None:
            chosen = ideal
        else:
            region = _read(probe, ideal - search, 2 * search + hop)
            scores = np.correlate(region, template, mode="valid")
            energy = np.concatenate(([0.0], np.cumsum(region.astype(float) ** 2)))
            energy = energy[hop:] - energy[:-hop]
            norm = np.sqrt(energy * float(template @ template)) + 1e-12
            chosen = ideal + int(offsets[int(np.argmax(scores / norm - penalty))])
        piece = _read(data, chosen, frame) * window[:, None]
        low, high = max(start, 0), min(start + frame, out_length)
        if high > low:
            out[low:high] += piece[low - start:high - start]
        # What the recording does *next* after the frame just written. The next
        # frame is chosen to continue it, which is the "similarity" in WSOLA.
        template = _read(probe, chosen + hop, hop)
    return out[:, 0] if mono else out


def load_align(path) -> AlignMap:
    """Read ``practice/align.yaml``. A missing file is no map, not an error.

    The pre-existing ``mode: offset`` files with one hand-checked anchor keep
    working: mode is derived from how many anchors there are, so it is read for
    information and never trusted over the data.
    """
    path = Path(path)
    if not path.is_file():
        return AlignMap()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return AlignMap()
    residual = data.get("residual_ms")
    residual = residual if isinstance(residual, dict) else {}
    anchors = []
    for entry in data.get("anchors") or []:
        if not isinstance(entry, dict):
            continue
        try:
            anchors.append(Anchor.from_dict(entry))
        except (TypeError, ValueError, KeyError):
            continue
    return AlignMap(
        anchors=anchors,
        source=str(data.get("source", "")),
        detected_bpm=float(data.get("detected_bpm", 0.0) or 0.0),
        residual_max_ms=(None if residual.get("max") is None
                         else float(residual["max"])),
        residual_mean_ms=(None if residual.get("mean") is None
                          else float(residual["mean"])),
        note=str(residual.get("note", "")),
    )


def save_align(path, amap: AlignMap) -> None:
    """Write ``practice/align.yaml``, source side only, with its own explanation."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Where this song's musical grid sits inside the original recording.",
        "#",
        "# The second deliberate exception to \"positions are in bars, never",
        "# seconds\" (CLAUDE.md, docs/practice-tracks.md): these seconds describe",
        "# an immutable audio file, not a position in the musical grid. Only the",
        "# SOURCE side is stored -- the target side is computed from Timeline at",
        "# build time, so a BPM edit re-warps everything correctly.",
        f'source: "{amap.source}"',
        f"detected_bpm: {amap.detected_bpm:g}",
        f"mode: {amap.mode}             # none | offset | piecewise",
        "anchors:                 # bar -> seconds into the ORIGINAL file",
    ]
    for anchor in sorted(amap.anchors, key=lambda a: a.position):
        beat = "" if anchor.beat == 1.0 else f", beat: {anchor.beat:g}"
        lines.append(f"  - {{bar: {anchor.bar}{beat}, at: {anchor.at:.3f}}}")
    worst = "null" if amap.residual_max_ms is None else f"{amap.residual_max_ms:.1f}"
    mean = "null" if amap.residual_mean_ms is None else f"{amap.residual_mean_ms:.1f}"
    note = amap.note or (
        "worst case is the number that matters -- 50 ms is the limit in "
        "docs/practice-tracks.md")
    lines.append(f'residual_ms: {{max: {worst}, mean: {mean}, note: "{note}"}}')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
