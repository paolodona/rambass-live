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
from rambass.restore import fill_hat_runs  # noqa: E402

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
    """``drums-consolidated.mid`` is what the code produces, hit for hit.

    If this fails the fixture and the code have already diverged, and every
    measurement in the tuning phases is against the wrong baseline — so it is
    checked on instrument, time and velocity rather than on the score, which
    would hide a re-voicing behind a matching placement.

    The fixture predates both tuning rules, so both are turned off — and this
    is their escape hatches tested on the real thing rather than on a synthetic
    section: turning them off must reproduce the old behaviour *exactly*, or
    the hits they withhold are not the only thing they changed.
    """
    song, quantized = load("drums-quantized")
    _, committed = load("drums-consolidated")
    rebuilt = consolidated_today(song, quantized, stop_beats=0.0,
                                 phantom_snare_velocity=0)

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


# ── Phase 1: consolidate stops stamping over a bar that stopped ──────────

def test_the_stop_rule_removes_ten_phantoms_for_one_real_hit():
    """Measured before the rule was written, and asserted exactly.

    Eleven hits withheld across three bars — all three the last bar of a
    section, which is the whole argument: the last repetition is the one least
    likely to repeat, because it is where the band lifts off into the break.
    Ten of the eleven are phantoms and one is real, so ``extra`` falls by ten
    and ``missing`` rises by one.
    """
    song, quantized = load("drums-quantized")
    _, truth = load("drums-restored")

    # The phantom-snare gate off in both, so this measures the stop rule alone.
    before = consolidated_today(song, quantized, stop_beats=0.0,
                                phantom_snare_velocity=0)
    after = consolidated_today(song, quantized, phantom_snare_velocity=0)

    assert len(before.hits) - len(after.hits) == 11
    assert score(song, before, truth) == BASELINE
    assert score(song, after, truth) == (960, 127, 18)
    assert errors(song, after, truth) == 145


def test_the_stop_rule_fires_only_on_the_last_bar_of_a_section():
    """Bars 17, 32 and 54 — verse-1-lift, verse-2-lift and verse-3-lift."""
    song, quantized = load("drums-quantized")
    report = consolidate(quantized, song.consolidation_spans(),
                         settings=ConsolidateSettings(
                             subdivision=song.drum_subdivision,
                             threshold=0.55, unit_bars=0))[1]
    stopped = {(entry["name"], stop["bar"], stop["beat"])
               for entry in report["sections"]
               for stop in entry.get("stopped", [])}
    assert stopped == {
        ("verse-1-lift", 17, 3.0),
        ("verse-2-lift", 32, 1.0),
        ("verse-3-lift", 54, 3.0),
    }
    for entry in report["sections"]:
        for stop in entry.get("stopped", []):
            last_bar = entry["bars"][1] - 1
            assert stop["bar"] in (last_bar, entry["bars"][1]), (
                f"{entry['name']}: the stop at bar {stop['bar']} is not its last")


def test_the_sweep_that_settled_on_one_and_a_quarter_beats():
    """The whole sweep, as executable evidence, measured 28 Aug 2026.

    ==========  =======  =======  ===========  ======
    stop_beats  dropped  phantom  real killed  errors
    ==========  =======  =======  ===========  ======
    0.0 (off)   0        0        0            154
    0.5         29       10       19           163
    1.0         27       10       17           161
    **1.25**    **11**   **10**   **1**        **145**
    1.5         11       10       1            145
    2.0         11       10       1            145
    ==========  =======  =======  ===========  ======

    Two things this pins that no comment could. **Everything below 1.25 is
    worse than not doing it at all** — it finds the same 10 phantoms and takes
    17 to 19 real hits with them, because at that width the rule stops reading
    "the band stopped" and starts reading "this bar is missing a hit at the
    end", which is the case Stage 6's vote exists to repair. And **1.25 is a
    plateau, not a peak**: 1.25, 1.5 and 2.0 fire on the same three bars, so
    the number is not balanced on a knife edge between two songs.

    ``real killed`` is counted on placement class, the same as the score — the
    one real hit at 1.25 is a ``hihat_closed`` withheld at bar 17 beat 4 where
    the ground truth has a ``hihat_pedal``. Per *instrument* all 11 are
    phantoms, which would flatter the rule by counting a right-place-wrong-drum
    hit as a win.
    """
    song, quantized = load("drums-quantized")
    _, truth = load("drums-restored")
    swept = {stop: errors(song, consolidated_today(song, quantized,
                                                   stop_beats=stop,
                                                   phantom_snare_velocity=0), truth)
             for stop in (0.0, 0.5, 1.0, 1.25, 1.5, 2.0)}
    assert swept == {0.0: 154, 0.5: 163, 1.0: 161, 1.25: 145, 1.5: 145, 2.0: 145}
    assert swept[1.25] < swept[0.0], "the rule has to be worth doing"
    assert swept[1.0] > swept[0.0], "below 1.25 it is worse than not doing it"


def test_the_withheld_hits_are_the_eleven_that_were_measured():
    """Named, not counted — bar 17 is the worked example in the plan."""
    song, quantized = load("drums-quantized")
    before = consolidated_today(song, quantized, stop_beats=0.0,
                                phantom_snare_velocity=0)
    after = consolidated_today(song, quantized, phantom_snare_velocity=0)
    timeline = song.timeline()

    def keys(performance):
        return sorted((h.instrument, round(h.time, 4), h.velocity)
                      for h in performance.hits)

    kept = set(keys(after))
    withheld = sorted(
        (*timeline.seconds_to_bar_beat(time), instrument)
        for instrument, time, _velocity in keys(before)
        if (instrument, time, _velocity) not in kept
    )
    rounded = [(bar, round(beat, 3), instrument) for bar, beat, instrument in withheld]
    assert rounded == [
        (17, 3.333, "hihat_closed"),
        (17, 3.667, "hihat_closed"),
        (17, 4.0, "hihat_closed"),
        (17, 4.0, "snare"),
        (17, 4.333, "hihat_closed"),
        (17, 4.667, "hihat_closed"),
        (32, 2.0, "hihat_closed"),
        (32, 2.0, "snare"),
        (32, 2.667, "hihat_closed"),
        (54, 4.0, "hihat_closed"),
        (54, 4.0, "snare"),
    ]


# ── Phase 2: a quiet snare in an unvoted section is a phantom ────────────

def test_the_phantom_snare_gate_drops_six_phantoms_for_one_real_hit():
    """Measured before the rule was written, and asserted exactly."""
    song, quantized = load("drums-quantized")
    _, truth = load("drums-restored")

    before = consolidated_today(song, quantized, phantom_snare_velocity=0)
    after = consolidated_today(song, quantized)

    assert len(before.hits) - len(after.hits) == 7
    assert score(song, before, truth) == (960, 127, 18)
    assert score(song, after, truth) == (959, 128, 12)
    assert errors(song, after, truth) == 140


def test_the_gate_is_the_snare_alone_because_adding_the_sidestick_is_worse():
    """The exclusion as executable evidence, not a comment.

    Folding the side-stick in is the obvious simplification — both are the
    snare drum, both are at the floor. It drops three more hits of which only
    one is a phantom, and the score gets *worse*: 141 against 140. A rim click
    is 25-30 dB below the same drummer's snare, so the velocity floor is where
    a **real** side-stick lives; it is a quiet instrument, not a quiet stroke.

    Generalising to every instrument at the floor is worse still — 155, which
    is worse than not having the rule at all — because the hats, kicks and toms
    down there genuinely play that quietly.
    """
    from rambass import quantize

    song, quantized = load("drums-quantized")
    _, truth = load("drums-restored")

    snare_only = errors(song, consolidated_today(song, quantized), truth)

    def with_instruments(instruments):
        saved = quantize.PHANTOM_FLOOR_INSTRUMENTS
        quantize.PHANTOM_FLOOR_INSTRUMENTS = instruments
        try:
            return errors(song, consolidated_today(song, quantized), truth)
        finally:
            quantize.PHANTOM_FLOOR_INSTRUMENTS = saved

    assert snare_only == 140
    assert with_instruments(("snare", "sidestick")) == 141
    assert with_instruments(("snare", "sidestick", "hihat_closed", "hihat_open",
                             "hihat_pedal", "kick", "tom_mid")) == 155


def test_the_floor_snares_in_the_unvoted_sections_are_the_seven_measured():
    """Six phantoms and one real, named. The real one is bar 41 beat 2.181."""
    song, quantized = load("drums-quantized")
    _, truth = load("drums-restored")
    timeline = song.timeline()
    step = 60.0 / timeline.bpm / song.drum_subdivision
    wanted = {(CLASS[h.instrument], int(round(h.time / step))) for h in truth.hits}

    before = consolidated_today(song, quantized, phantom_snare_velocity=0)
    after = consolidated_today(song, quantized)
    kept = sorted((h.instrument, round(h.time, 4), h.velocity) for h in after.hits)
    dropped = [h for h in before.hits
               if (h.instrument, round(h.time, 4), h.velocity) not in kept]

    named = sorted(
        (*timeline.seconds_to_bar_beat(h.time), h.velocity,
         (CLASS[h.instrument], int(round(h.time / step))) in wanted)
        for h in dropped)
    assert [(bar, round(beat, 3), velocity, real)
            for bar, beat, velocity, real in named] == [
        (18, 2.667, 45, False),
        (19, 3.667, 45, False),
        (19, 4.496, 45, False),
        (20, 1.0, 47, False),
        (41, 2.181, 45, True),
        (63, 4.0, 45, False),
        (64, 4.667, 45, False),
    ]
    assert all(h.instrument == "snare" for h in dropped)


def test_fifty_is_the_floor_plus_slack_and_not_the_best_score_on_this_song():
    """Why the constant is not tuned to the last error on one song.

    A floor of 55 also catches the v53 phantom at bar 19 beat 4.809 and scores
    139 — one better. It is deliberately not taken. ``scale_velocities`` puts
    this song's floor at v45, so "at or below 50" means *at the floor*, which
    is a boundary with a reason; 55 starts including strokes that carry a
    measured level, and one drummer and one kit means this number has to hold
    for the other ten songs of the album, not just for Manlio's last error.
    """
    song, quantized = load("drums-quantized")
    _, truth = load("drums-restored")
    swept = {floor: errors(song,
                           consolidated_today(song, quantized,
                                              phantom_snare_velocity=floor),
                           truth)
             for floor in (0, 40, 45, 50, 55, 70)}
    assert swept == {0: 145, 40: 145, 45: 141, 50: 140, 55: 139, 70: 139}


def test_the_two_rules_together_land_on_a_hundred_and_forty():
    """What ``drums consolidate`` produces today, with nothing declared.

    154 → 145 (the stop rule) → 140 (the phantom-snare gate). Both are pure
    Stage 6 changes and neither needs a line of ``song.yaml``, which is what
    makes them worth having: the declared hi-hat pattern is worth more but it
    is Paolo's ear that fills it in.
    """
    song, quantized = load("drums-quantized")
    _, truth = load("drums-restored")
    assert errors(song, consolidated_today(song, quantized), truth) == 140


# ── Phase 3: a declared hi-hat pattern ───────────────────────────────────

def test_declaring_hats_run_on_three_sections_recovers_most_of_the_hat_holes():
    """The phase's real evidence, and it needs no manifest edit.

    ``hats:`` is unset on every section of the committed ``song.yaml`` — it is
    Paolo's to fill in by ear — so the fixture score cannot move. This
    *simulates* the declaration in memory instead: the measurements already
    settled ``chorus-1: run``, ``verse-1: run`` and ``verse-2: run``, and their
    ground truth is a hat on all twelve triplet slots.

    Counted as missing *hats* in those three sections, not as whole-song errors,
    because that is the claim: the field is worth about 89 notes and these three
    lines are most of it.
    """
    song, quantized = load("drums-quantized")
    _, truth = load("drums-restored")
    consolidated = consolidated_today(song, quantized)
    declared = ("chorus-1", "verse-1", "verse-2")

    def missing_hats_in(performance, sections):
        timeline = song.timeline()
        step = 60.0 / timeline.bpm / song.drum_subdivision
        spans = {s.name: s for s in song.consolidation_spans()}
        have = {(CLASS[h.instrument], int(round(h.time / step)))
                for h in performance.hits}
        count = 0
        for name in sections:
            span = spans[name]
            low = timeline.bar_beat_to_seconds(span.start_bar, span.start_beat)
            high = timeline.bar_beat_to_seconds(span.end_bar, span.end_beat)
            for hit in truth.hits:
                if not (low - 1e-9 <= hit.time < high - 1e-9):
                    continue
                if CLASS[hit.instrument] != "hat":
                    continue
                if ("hat", int(round(hit.time / step))) not in have:
                    count += 1
        return count

    before = missing_hats_in(consolidated, declared)

    for section in song.sections:
        if section.name in declared:
            section.hats = "run"
    filled, report = fill_hat_runs(
        consolidated, song.sections,
        end_bar=(song.bars or song.total_bars()) + 1,
        subdivision=song.drum_subdivision)

    after = missing_hats_in(filled, declared)
    assert before - after >= 80, f"only recovered {before - after} of {before}"
    assert {entry["name"] for entry in report["sections"]} == set(declared)
    # And it did not have to invent a velocity anywhere: all three sections
    # already carry hats the transcriber found.
    assert {entry["velocity_from"] for entry in report["sections"]} == {"section"}


def test_the_declaration_never_overwrites_a_hat_the_transcriber_found():
    """chorus-1's own hats run v86-113; filling must not flatten them."""
    song, quantized = load("drums-quantized")
    consolidated = consolidated_today(song, quantized)
    kept = {(h.instrument, round(h.time, 4), h.velocity) for h in consolidated.hits}

    for section in song.sections:
        if section.name == "chorus-1":
            section.hats = "run"
    filled, _ = fill_hat_runs(
        consolidated, song.sections,
        end_bar=(song.bars or song.total_bars()) + 1,
        subdivision=song.drum_subdivision)

    after = {(h.instrument, round(h.time, 4), h.velocity) for h in filled.hits}
    assert kept <= after, "an existing hit was moved, re-voiced or dropped"


def test_declaring_hats_does_not_undo_the_stop_rule():
    """verse-1-lift's bar 17 must not gain the hats consolidate just withheld."""
    song, quantized = load("drums-quantized")
    consolidated = consolidated_today(song, quantized)
    timeline = song.timeline()

    for section in song.sections:
        if section.name == "verse-1-lift":
            section.hats = "run"
    filled, report = fill_hat_runs(
        consolidated, song.sections,
        end_bar=(song.bars or song.total_bars()) + 1,
        subdivision=song.drum_subdivision)

    tail = [h for h in filled.hits
            if timeline.bar_beat_to_seconds(17, 3.0) + 1e-9 < h.time
            < timeline.bar_beat_to_seconds(18, 1.0) - 1e-9]
    assert not tail, f"bar 17 gained {[h.instrument for h in tail]} after the stop"
    entry = next(e for e in report["sections"] if e["name"] == "verse-1-lift")
    assert {"bar": 17, "beat": 3.0} in entry["stopped"]


def test_what_three_declarations_are_worth_and_what_they_cost():
    """``chorus-1``, ``verse-1`` and ``verse-2`` at ``hats: run``, whole song.

    140 → **78** errors: every one of the 81 hat holes in those three sections
    is recovered (``missing`` 128 → 47), at the cost of 19 hats the review pass
    does not have (``extra`` 12 → 31).

    Where those 19 are matters more than the count, and they are not scattered:
    16 are in ``chorus-1`` and 3 in ``verse-2``, and almost all sit in the
    **partial bars at either end of a section** — bar 20 beat 3.333 is two
    slots after verse-2 begins at 20.3, and bars 39-40 are where chorus-1 runs
    out at 40.3. The mask tiles whole bars, so a section that starts or ends
    mid-bar gets the pattern across the part of the bar it owns, and whether the
    band really plays the run from the section's very first slot is an ear
    question rather than something the field can know. That is a caution for
    filling the field in, not a reason to round the spans to bar lines — see
    ``quantize.consolidate``'s docstring for why rounding them is worse.
    """
    song, quantized = load("drums-quantized")
    _, truth = load("drums-restored")
    consolidated = consolidated_today(song, quantized)
    assert errors(song, consolidated, truth) == 140

    for section in song.sections:
        if section.name in ("chorus-1", "verse-1", "verse-2"):
            section.hats = "run"
    filled, report = fill_hat_runs(
        consolidated, song.sections,
        end_bar=(song.bars or song.total_bars()) + 1,
        subdivision=song.drum_subdivision)

    assert report["added"] == 100
    assert score(song, filled, truth) == (1040, 47, 31)
    assert errors(song, filled, truth) == 78


# ── Phase 4: a boundary hat rule, measured and rejected ──────────────────

def test_a_boundary_hat_candidate_rule_is_not_precise_enough_to_ship():
    """Why there is no ``restore.hat_candidates``. Measured 28 Aug 2026.

    Ten of Manlio's leftover notes are an open hat or a foot splash near a
    section change — ``hihat_open`` at bars 13.4, 17.3, 24.2.667, 32.1,
    44.2.667 and 54.3, ``hihat_pedal`` at 17.4, 39.4, 40.1 and 40.2 — and the
    obvious move is to copy :func:`~rambass.restore.crash_candidates`, which
    makes exactly this argument for cymbals: what separates a boundary crash
    from a wash is musical position, not spectrum.

    **It does not transfer, and the reason is precision.** Two of the ten
    (24.2.667 and 44.2.667) sit 16 and 26 beats from the nearest change, so no
    boundary rule can reach them at all. For the other eight, every formulation
    tried lands between 0% and 28%:

    ====================================================  ==========  =====
    rule                                                  candidates  right
    ====================================================  ==========  =====
    the last hat within 1 beat of the start               9           0
    the last hat within 2 beats                           14          4
    every hat within 1 beat                               14          0
    every hat within 2 beats                              43          5
    every hat within 3 beats                              69          7
    every hat within 2 beats, v >= 83                     18          5
    ====================================================  ==========  =====

    The best of them is 18 candidates for 5 right, and that velocity gate is
    fitted to five hits — the boundary hats that really are open run v83-97
    against v45-121 for the 38 that are not, so the distributions overlap
    almost completely. ``CRASH_CANDIDATE_VELOCITY`` had a 23 dB decay
    separation behind it *and* nine of ten at v83+; there is no acoustic story
    here at all.

    And the cost of shipping it is known: :data:`~rambass.restore.NOTICEABLE`
    exists because the first Manlio ledger "listed 204 items and about 170 were
    single hi-hats; the 6 crashes and 25 toms that a listener actually notices
    were buried in them". An 18-item hi-hat checklist at 28% precision puts
    them straight back.

    What is real is the *observation*: eight of the ten are in the last bar
    before a change, so the last bar before each change is worth a listen. That
    belongs in docs/transcription-lessons.md, which is where it is, and a hat
    somebody hears is an ordinary ``swap-hit`` note in the review console.
    """
    from rambass.restore import HAT_FAMILY

    song, quantized = load("drums-quantized")
    _, truth = load("drums-restored")
    consolidated = consolidated_today(song, quantized)
    timeline = song.timeline()
    beat = 60.0 / timeline.bpm
    step = beat / song.drum_subdivision
    ordered = sorted(song.sections, key=lambda s: (s.bar, s.beat))
    opened = {int(round(h.time / step)) for h in truth.hits
              if h.instrument in ("hihat_open", "hihat_pedal")}

    def candidates(*, beats, last_only=False, min_velocity=0):
        out = []
        for section in ordered:
            start = timeline.bar_beat_to_seconds(section.bar, section.beat)
            near = [h for h in consolidated.hits
                    if h.instrument in HAT_FAMILY
                    and start - beats * beat - 1e-9 <= h.time < start - 1e-9
                    and h.velocity >= min_velocity]
            if not near:
                continue
            out += [max(near, key=lambda h: h.time)] if last_only else near
        return out

    def right(found):
        return sum(1 for h in found if int(round(h.time / step)) in opened)

    for beats, last_only, gate, wanted, hits in (
            (1.0, True, 0, 9, 0),
            (2.0, True, 0, 14, 4),
            (1.0, False, 0, 14, 0),
            (2.0, False, 0, 43, 5),
            (3.0, False, 0, 69, 7),
            (2.0, False, 83, 18, 5),
    ):
        found = candidates(beats=beats, last_only=last_only, min_velocity=gate)
        assert (len(found), right(found)) == (wanted, hits), (
            f"{beats} beats, last_only={last_only}, v>={gate}: "
            f"{len(found)} candidates, {right(found)} right")

    # Nothing better than 28%, against crash_candidates' one-per-section.
    best = candidates(beats=2.0, min_velocity=83)
    assert right(best) / len(best) < 0.30

    # And the rule is not in the module, deliberately.
    import rambass.restore as restore
    assert not hasattr(restore, "hat_candidates"), (
        "if this ships, the measurement above has to be redone first")


def test_two_of_the_ten_are_not_near_a_boundary_at_all():
    """The premise's own limit: 24.2.667 and 44.2.667 are mid-section."""
    song, _ = load("drums-quantized")
    timeline = song.timeline()
    beat = 60.0 / timeline.bpm
    starts = [timeline.bar_beat_to_seconds(s.bar, s.beat) for s in song.sections]

    def beats_to_next_change(bar, at_beat):
        when = timeline.bar_beat_to_seconds(bar, at_beat)
        following = [s - when for s in starts if s > when + 1e-9]
        return min(following) / beat if following else float("inf")

    assert round(beats_to_next_change(24, 2.667), 2) == 16.33
    assert round(beats_to_next_change(44, 2.667), 2) == 26.33
    for bar, at_beat in ((13, 4.0), (17, 3.0), (17, 4.0), (32, 1.0),
                         (39, 4.0), (40, 1.0), (40, 2.0), (54, 3.0)):
        assert beats_to_next_change(bar, at_beat) <= 3.0
