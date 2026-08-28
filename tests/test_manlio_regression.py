"""Score the rebuilt drum part against Manlio's review pass.

This is the tripwire the tuning phases are judged by. Manlio is the one song on
*Tutti in Fila* that has been through a full review pass, so its four tracked
MIDI files are the only ground truth in the repo:

======================== ====================================================
``drums-quantized.mid``  input to :func:`~rambass.quantize.consolidate`
``drums-consolidated.mid`` what the pipeline produces today
``drums-restored.mid``   **ground truth** — Paolo's promoted edits applied
======================== ====================================================

Two things this module is careful about, because both are ways the numbers
below could quietly stop meaning anything:

**It scores the whole song, all fifteen sections.** Scoring a hand-picked
subset makes every figure scope-dependent and invites cherry-picking a
threshold that happens to work on the sections it was tuned against. The cost
is that the ground truth is *partial* outside the sections Paolo has marked
done, so the absolute figure is a tripwire and the **deltas** are the evidence.

**It matches on instrument class, not instrument.** Placement and voicing are
separate questions: whether a hat belongs at a slot is Stage 6's business, and
which hat it is is Stage 7's. So the five hat-family instruments collapse to
``hat``, the crash family to ``cymbal``, snare and side-stick to ``snare``, the
six toms to ``tom``, and each ``(class, slot)`` counts once however many
instruments of that class landed there. A chorus bar carrying a closed hat and
an open hat at one slot is one hat as far as placement goes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("mido")

from rambass.drummap import GENERAL_MIDI  # noqa: E402
from rambass.manifest import load_song  # noqa: E402
from rambass.midiio import DrumPerformance, read_drum_midi  # noqa: E402
from rambass.quantize import ConsolidateSettings, consolidate  # noqa: E402

MANLIO = Path(__file__).resolve().parents[1] / "songs" / "tutti-in-fila" / "09-manlio"

#: Instrument → placement class. See the module docstring: this is what makes
#: the score a measurement of *where the hits are* rather than of the voicing,
#: which the declarations in Stage 7 answer separately.
CLASS: dict[str, str] = {
    "kick": "kick",
    "snare": "snare", "sidestick": "snare",
    "hihat_closed": "hat", "hihat_open": "hat", "hihat_pedal": "hat",
    "ride": "hat", "ride_bell": "hat",
    "crash": "cymbal", "crash_2": "cymbal", "splash": "cymbal",
    "tom_low": "tom", "tom_low_mid": "tom", "tom_mid": "tom",
    "tom_high_mid": "tom", "tom_high": "tom", "tom_highest": "tom",
}

#: Today's score, measured 28 Aug 2026 against the committed fixtures.
#:
#: A tripwire, not a target: if this moves and nobody changed
#: :func:`~rambass.quantize.consolidate`, either a fixture was regenerated or
#: the scorer drifted, and every delta the tuning phases assert stops meaning
#: anything.
BASELINE = (961, 126, 28)


def _fixtures_present() -> bool:
    return all((MANLIO / "midi" / name).exists() for name in (
        "drums-quantized.mid", "drums-consolidated.mid", "drums-restored.mid",
    )) and (MANLIO / "song.yaml").exists()


pytestmark = pytest.mark.skipif(
    not _fixtures_present(),
    reason="Manlio's tracked MIDI is not in this checkout",
)


def load(variant: str):
    """The song and one of its MIDI variants, on the song's own timeline.

    ``read_drum_midi`` reconstructs a timeline from the file's tempo map, which
    is close but is not the manifest's — and every bar/beat conversion here has
    to agree with ``song.yaml`` or the section spans land in the wrong place.
    """
    song = load_song(MANLIO)
    performance = read_drum_midi(MANLIO / "midi" / f"{variant}.mid", GENERAL_MIDI)
    performance.timeline = song.timeline()
    return song, performance


def _slots(song, performance: DrumPerformance) -> set[tuple[str, int]]:
    """``{(class, grid slot)}`` for every hit inside the song's sections.

    Matching is per grid slot rather than by proximity in seconds, which is the
    same choice :func:`~rambass.restore.missing_hits` makes and for the same
    reason: both variants are quantised to the same grid, so a hit a whole slot
    away really is a different hit, and one that shares a slot is the same hit
    wherever inside it the two files put it.

    On Manlio a slot is 333 ms (60 BPM, ``subdivision: 3``), so the 60 ms
    tolerance the plan asks for is subsumed — anything within 60 ms of a grid
    line is in that line's slot. It is *not* the same as matching at 60 ms
    proximity, and ``test_proximity_matching_is_not_the_same_measurement``
    pins the difference rather than leaving it to be rediscovered.
    """
    timeline = song.timeline()
    spans = song.consolidation_spans()
    low = min(timeline.bar_beat_to_seconds(s.start_bar, s.start_beat) for s in spans)
    high = max(timeline.bar_beat_to_seconds(s.end_bar, s.end_beat) for s in spans)
    step = 60.0 / timeline.bpm / max(song.drum_subdivision, 1)
    out: set[tuple[str, int]] = set()
    for hit in performance.hits:
        if not (low - 1e-9 <= hit.time < high - 1e-9):
            continue
        out.add((CLASS[hit.instrument], int(round(hit.time / step))))
    return out


def score(song, produced: DrumPerformance, truth: DrumPerformance) -> tuple[int, int, int]:
    """``(tp, missing, extra)`` for *produced* against *truth*."""
    a, b = _slots(song, produced), _slots(song, truth)
    return len(a & b), len(b - a), len(a - b)


def errors(song, produced: DrumPerformance, truth: DrumPerformance) -> int:
    _, missing, extra = score(song, produced, truth)
    return missing + extra


def consolidated_today(song, quantized, **overrides) -> DrumPerformance:
    """``drums consolidate``'s own defaults, as the CLI passes them."""
    settings = ConsolidateSettings(
        subdivision=song.drum_subdivision, threshold=0.55, unit_bars=0,
        **overrides)
    return consolidate(quantized, song.consolidation_spans(), settings=settings)[0]


def test_every_instrument_in_the_fixtures_has_a_placement_class():
    """A new instrument must be classified, not silently dropped from the score."""
    song, truth = load("drums-restored")
    _, consolidated = load("drums-consolidated")
    for performance in (truth, consolidated):
        unmapped = {h.instrument for h in performance.hits} - set(CLASS)
        assert not unmapped, f"no placement class for {sorted(unmapped)}"


def test_the_committed_consolidated_part_scores_the_baseline():
    """The tripwire. See :data:`BASELINE`."""
    song, consolidated = load("drums-consolidated")
    _, truth = load("drums-restored")
    assert score(song, consolidated, truth) == BASELINE


def test_consolidate_still_reproduces_the_committed_fixture():
    """``drums-consolidated.mid`` is what today's code produces, hit for hit.

    If this fails the fixture and the code have already diverged, and every
    measurement in the tuning phases is against the wrong baseline — so it is
    checked on instrument, time and velocity rather than on the score, which
    would hide a re-voicing behind a matching placement.
    """
    song, quantized = load("drums-quantized")
    _, committed = load("drums-consolidated")
    rebuilt = consolidated_today(song, quantized)

    def key(performance):
        return sorted((h.instrument, round(h.time, 3), h.velocity)
                      for h in performance.hits)

    assert key(rebuilt) == key(committed)


def test_the_six_sections_too_short_to_vote_on_are_the_ones_stage_7_expects():
    """Which sections ``consolidate`` passes through, named rather than counted.

    ``min_repeats: 4`` is what decides this, and the phantom-snare gate applies
    *only* here — so if this list changes, that rule's scope changed with it.
    """
    song, quantized = load("drums-quantized")
    report = consolidate(quantized, song.consolidation_spans(),
                         settings=ConsolidateSettings(
                             subdivision=song.drum_subdivision,
                             threshold=0.55, unit_bars=0))[1]
    skipped = {e["name"] for e in report["sections"] if "skipped" in e}
    assert skipped == {"theme-intro", "theme-intro-stop", "break-1", "break-2",
                       "break-3", "closing-fill"}


def test_proximity_matching_is_not_the_same_measurement():
    """Why the scorer works in grid slots and not at 60 ms proximity.

    Collapsing each class at 60 ms and matching one-to-one scores 163 errors
    against the grid-slot form's 154. The nine are hand-placed additions in
    ``drums-restored.mid`` that sit off the triplet grid — bar 15 beat 4.167,
    4.5, 4.833 and the tom run in bar 63 — which are 167 ms from their
    neighbour and so are two events at 60 ms and one slot on the grid. The
    grid is the right unit here because both files were quantised to it; this
    test exists so that "tolerance 60 ms" is not read as an invitation to swap
    the two and quietly move every number in the tuning phases by nine.
    """
    song, consolidated = load("drums-consolidated")
    _, truth = load("drums-restored")

    def collapsed(performance, tolerance=0.060):
        timeline = song.timeline()
        spans = song.consolidation_spans()
        low = min(timeline.bar_beat_to_seconds(s.start_bar, s.start_beat) for s in spans)
        high = max(timeline.bar_beat_to_seconds(s.end_bar, s.end_beat) for s in spans)
        by_class: dict[str, list[float]] = {}
        for hit in performance.hits:
            if low - 1e-9 <= hit.time < high - 1e-9:
                by_class.setdefault(CLASS[hit.instrument], []).append(hit.time)
        out: dict[str, list[float]] = {}
        for cls, times in by_class.items():
            kept: list[float] = []
            for time in sorted(times):
                if not kept or time - kept[-1] > tolerance:
                    kept.append(time)
            out[cls] = kept
        return out

    produced, wanted = collapsed(consolidated), collapsed(truth)
    missing = extra = 0
    for cls in set(produced) | set(wanted):
        available = list(produced.get(cls, []))
        used = [False] * len(available)
        for time in wanted.get(cls, []):
            best, best_distance = None, 0.060 + 1
            for index, candidate in enumerate(available):
                if used[index]:
                    continue
                distance = abs(candidate - time)
                if distance <= 0.060 and distance < best_distance:
                    best, best_distance = index, distance
            if best is None:
                missing += 1
            else:
                used[best] = True
        extra += used.count(False)

    assert (missing, extra) == (135, 28)
    assert missing + extra == 163
    assert sum(BASELINE[1:]) == 154
