"""Reviewing a song's sections — suggestions with their evidence, never edits.

Same philosophy as :mod:`rambass.arrange`, and for the same reason: sectioning a
song is a musical judgement and a tool that guessed at it would be wrong in ways
that are hard to see. What a tool *can* do is check the list a human wrote
against the rules the pipeline downstream actually depends on, and say where the
drums disagree with it.

Every finding names a position in **Reaper's** numbering, so it can be checked
against the ruler on screen without arithmetic (see CLAUDE.md).

The rules being checked come from Paolo:

* identical names are one part — :func:`~rambass.quantize.consolidate` pools them
  and stamps them identically, so differing lengths mean one will come out wrong;
* a name *family* is not a pooling key — ``verse-2`` and ``verse-3`` are
  structurally alike and allowed to differ;
* a section that steps up inside itself needs its own name, because a pattern
  in 4 bars of 12 is 33% against a 0.55 threshold and Stage 6 would delete it.

The pure checks need nothing but the manifest. The pattern checks need a
transcribed :class:`~rambass.midiio.DrumPerformance`, and are skipped without
one rather than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass

from .manifest import Song
from .midiio import DrumPerformance

#: A section needs at least this many repetitions of a one-bar unit before
#: `consolidate` has anything to vote on. Two is the minimum that can disagree.
MIN_VOTABLE_BARS = 2

#: How different two patterns must be before a split is worth suggesting. The
#: score is the total change in per-instrument bar coverage across the boundary,
#: so 0.6 means "one voice appears or disappears almost entirely, or two change
#: by a third each". Measured on Manlio: the snare entering at a verse's bar 9
#: scores about 1.0, and the quietest real boundary in the song is well clear.
SPLIT_SCORE = 0.6

#: Below this the two patterns are the same part as far as the drums can tell.
SAME_SCORE = 0.12


@dataclass(frozen=True)
class Finding:
    """One suggestion, with where it applies and why."""

    kind: str
    reaper: str
    message: str

    def __str__(self) -> str:
        return f"{self.reaper:>8}  {self.kind:<28} {self.message}"


def _reaper(song: Song, bar: int, beat: float = 1.0) -> str:
    return f"{bar + song.count_in_bars}.{beat:g}"


def _coverage(
    performance: DrumPerformance,
    start_bar: int,
    end_bar: int,
    bar_seconds: float,
    zero: float,
) -> dict[str, float]:
    """Per instrument, the share of whole bars in [start, end) it appears in.

    Coverage rather than a hit count, because a count is dominated by the hats:
    a bar with eleven hat strokes and one with six differ by five hits and are
    the same pattern, while a snare going from absent to present is one hit a bar
    and is a different part. This is the measure that found Manlio's verse lifts.
    """
    bars = max(end_bar - start_bar, 0)
    if bars <= 0:
        return {}
    seen: dict[str, int] = {}
    for bar in range(start_bar, end_bar):
        low = zero + (bar - 1) * bar_seconds
        high = low + bar_seconds
        here = {h.instrument for h in performance.hits if low - 1e-9 <= h.time < high - 1e-9}
        for instrument in here:
            seen[instrument] = seen.get(instrument, 0) + 1
    return {name: count / bars for name, count in seen.items()}


def _distance(left: dict, right: dict) -> float:
    """Total change in coverage between two profiles. 0 = identical."""
    return sum(abs(left.get(key, 0.0) - right.get(key, 0.0))
               for key in set(left) | set(right))


#: A split is only as trustworthy as the smaller side is long. Coverage over two
#: bars can only be 0, 50 or 100%, so one stray tom reads as "50% of bars" and
#: scores 0.67 — above the threshold and meaningless. Measured on Manlio, that
#: produced three false splits inside the verses. Scaling the score by the
#: shorter side removes all three and keeps the real one.
SPLIT_CONFIDENCE_BARS = 4

#: Voices that do not vote on where a section changes. A crash lands at the top
#: of a section and nowhere else in it — that is what a crash is for — and a tom
#: is a fill, which is by definition the bar that does not repeat. Both are
#: one-offs, so both look like a boundary to any coverage measure. Measured on
#: Manlio: two crashes in verse-3's first four bars scored 0.75 and proposed
#: splitting a section that does not change. Stage 7 handles accents and fills
#: by hand and this is the same judgement made earlier.
#:
#: What is left is the timekeeping and the backbeat, which is where a real change
#: of part shows: on Manlio's verses the snare arriving is the whole signal.
ACCENT_VOICES: frozenset[str] = frozenset({
    "crash", "crash_2", "china", "splash",
    "tom_low", "tom_mid", "tom_high",
})


def _pattern(
    performance: DrumPerformance,
    start_bar: int,
    end_bar: int,
    bar_seconds: float,
    zero: float,
    slots_per_bar: int,
) -> dict[tuple[str, int], float]:
    """Per ``(instrument, slot)``, the share of bars it appears in.

    The measure for "is this the same part". Per-*instrument* coverage is not:
    two sections can both play kick, snare and hats in every bar and be
    completely different grooves, which is what happened on Manlio — chorus-1
    and theme-finale scored 0.01 apart and are plainly not the same thing. Where
    in the bar the hits land is the part.
    """
    bars = max(end_bar - start_bar, 0)
    if bars <= 0 or slots_per_bar <= 0:
        return {}
    step = bar_seconds / slots_per_bar
    seen: dict[tuple[str, int], int] = {}
    for bar in range(start_bar, end_bar):
        low = zero + (bar - 1) * bar_seconds
        high = low + bar_seconds
        here = {
            (h.instrument, int(round((h.time - low) / step)) % slots_per_bar)
            for h in performance.hits if low - 1e-9 <= h.time < high - 1e-9
        }
        for key in here:
            seen[key] = seen.get(key, 0) + 1
    return {key: count / bars for key, count in seen.items()}


def check_sections(
    song: Song,
    performance: DrumPerformance | None = None,
    *,
    split_score: float = SPLIT_SCORE,
    same_score: float = SAME_SCORE,
) -> list[Finding]:
    """Review *song*'s sections. Returns findings, and changes nothing."""
    spans = song.consolidation_spans()
    if not spans:
        return []

    findings: list[Finding] = []
    timeline = song.timeline()
    bar_seconds = timeline.bar_length_seconds(1)
    zero = timeline.bar_beat_to_seconds(1, 1.0)
    last_bar = song.total_bars() + 1

    # ── pure checks ─────────────────────────────────────────────────────────
    by_name: dict[str, list] = {}
    for span in spans:
        by_name.setdefault(span.name, []).append(span)

    for name, group in sorted(by_name.items()):
        if len(group) < 2:
            continue
        lengths = {round(_span_bars(span), 3) for span in group}
        if len(lengths) > 1:
            shown = ", ".join(f"{x:g}" for x in sorted(lengths))
            findings.append(Finding(
                "same-name-different-length",
                _reaper(song, group[0].start_bar, group[0].start_beat),
                f"{len(group)} sections named {name!r} are {shown} bars long. "
                f"An identical name means one part -- consolidate pools them and "
                f"stamps the same pattern over all of them, so the odd one out "
                f"will come back wrong. Rename it or fix the boundary.",
            ))

    for span in spans:
        whole = int(_span_bars(span))
        if span.start_bar >= last_bar:
            findings.append(Finding(
                "section-past-the-end",
                _reaper(song, span.start_bar, span.start_beat),
                f"{span.name!r} starts at bar {span.start_bar} but the song is "
                f"{song.total_bars()} bars. Fix bars: or the section.",
            ))
        elif whole < MIN_VOTABLE_BARS:
            findings.append(Finding(
                "too-short-to-consolidate",
                _reaper(song, span.start_bar, span.start_beat),
                f"{span.name!r} holds {whole} whole bar(s), so consolidate has "
                f"nothing to vote on and will pass it through untouched. That is "
                f"right for a fill -- Stage 7 by hand -- and wrong if you meant "
                f"it to be a repeating part.",
            ))

    if performance is None or not performance.hits:
        return findings

    # ── pattern checks ──────────────────────────────────────────────────────
    slots_per_bar = song.time_signature[0] * song.drum_subdivision
    profiles: dict[str, dict] = {}
    for span in spans:
        first, end = _whole_bars(span)
        profiles[span.name] = _pattern(performance, first, end, bar_seconds, zero,
                                       slots_per_bar)

    for span in spans:
        first, end = _whole_bars(span)
        if end - first < 2 * MIN_VOTABLE_BARS:
            continue
        best = None
        for cut in range(first + MIN_VOTABLE_BARS, end - MIN_VOTABLE_BARS + 1):
            left = _keep_voices(_coverage(performance, first, cut, bar_seconds, zero))
            right = _keep_voices(_coverage(performance, cut, end, bar_seconds, zero))
            # Discounted by the shorter side: see SPLIT_CONFIDENCE_BARS.
            shorter = min(cut - first, end - cut)
            score = _distance(left, right) * min(
                1.0, shorter / SPLIT_CONFIDENCE_BARS)
            if best is None or score > best[0]:
                best = (score, cut, left, right)
        if best is None or best[0] < split_score:
            continue
        score, cut, left, right = best
        findings.append(Finding(
            "suggest-split",
            _reaper(song, cut),
            f"{span.name!r} changes pattern here (score {score:.2f}): "
            f"{_changes(left, right)}. Consolidating it as one section would "
            f"outvote whichever side is shorter. Split it, or say it is a fill.",
        ))

    ordered = list(spans)
    for index, span in enumerate(ordered):
        for other in ordered[index + 1:]:
            if span.name == other.name:
                continue
            score = _distance(profiles[span.name], profiles[other.name])
            if score > same_score:
                continue
            adjacent = (span.end_bar, span.end_beat) == (other.start_bar, other.start_beat)
            if adjacent:
                findings.append(Finding(
                    "suggest-merge",
                    _reaper(song, other.start_bar, other.start_beat),
                    f"{span.name!r} and {other.name!r} are adjacent and play the "
                    f"same part (score {score:.2f}). Merge them, or give them the "
                    f"same name so consolidate votes them together.",
                ))
            else:
                findings.append(Finding(
                    "suggest-normalise",
                    _reaper(song, other.start_bar, other.start_beat),
                    f"{span.name!r} and {other.name!r} play the same part "
                    f"(score {score:.2f}) but vote separately, so each gets half "
                    f"the evidence. Name them both {span.name!r} to pool them.",
                ))

    return findings


def _span_bars(span) -> float:
    return (span.end_bar + (span.end_beat - 1.0) / 4.0) - (
        span.start_bar + (span.start_beat - 1.0) / 4.0)


def _whole_bars(span) -> tuple[int, int]:
    """The bars wholly inside *span*, for measuring a pattern bar by bar."""
    first = span.start_bar if span.start_beat == 1.0 else span.start_bar + 1
    return first, max(span.end_bar, first)


def _changes(left: dict[str, float], right: dict[str, float]) -> str:
    moved = sorted(
        ((abs(left.get(k, 0.0) - right.get(k, 0.0)), k) for k in set(left) | set(right)),
        reverse=True,
    )
    parts = []
    for delta, name in moved[:3]:
        if delta < 0.2:
            continue
        arrow = "in" if right.get(name, 0.0) > left.get(name, 0.0) else "out"
        parts.append(f"{name} {arrow} ({left.get(name, 0.0):.0%} -> "
                     f"{right.get(name, 0.0):.0%} of bars)")
    return "; ".join(parts) or "several voices shift"


def _keep_voices(coverage: dict[str, float]) -> dict[str, float]:
    """Drop the accents and fills — see :data:`ACCENT_VOICES`."""
    return {name: share for name, share in coverage.items()
            if name not in ACCENT_VOICES}
