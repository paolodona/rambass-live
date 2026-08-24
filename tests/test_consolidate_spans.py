"""Consolidating a section that does not start on a bar line, and name pooling.

Paolo: "it is important that consolidate consolidates within the section,
regardless of where it starts (with odd timing we will rarely fit into a .1
start of section generally)". And on naming: "if the sections are named exactly
the same, use exactly the same part, if they are the same name pattern (eg:
verse-2 vs verse-3) check the structure but should not match exactly."
"""

from __future__ import annotations

import pytest

from rambass.midiio import DrumPerformance, Hit
from rambass.quantize import ConsolidateSettings, SectionSpan, consolidate
from rambass.timeline import Timeline

TIMELINE = Timeline(bpm=120.0)   # beat 0.5 s, bar 2.0 s


def at(bar, beat):
    return TIMELINE.bar_beat_to_seconds(bar, beat)


def backbeat(first_bar, bars, *, velocity=100, first_beat=1.0, last_beat=1.0):
    """Kick on 1 and 3, snare on 2 and 4, clipped to a possibly mid-bar span."""
    lo, hi = at(first_bar, first_beat), at(first_bar + bars, last_beat)
    out = []
    for b in range(first_bar, first_bar + bars + 1):
        for beat, name in ((1.0, "kick"), (2.0, "snare"), (3.0, "kick"), (4.0, "snare")):
            t = at(b, beat)
            if lo - 1e-9 <= t < hi - 1e-9:
                out.append(Hit(name, t, velocity))
    return out


# ── the span type ────────────────────────────────────────────────────────────


def test_a_plain_tuple_still_works():
    """Every existing caller passes (name, first_bar, last_bar)."""
    span = SectionSpan.of(("verse", 1, 5))
    assert (span.name, span.start_bar, span.end_bar) == ("verse", 1, 5)
    assert span.start_beat == span.end_beat == 1.0


def test_a_span_can_start_and_end_mid_bar():
    span = SectionSpan("verse-2", 20, 32, start_beat=3.0, end_beat=3.0)
    assert (span.start_beat, span.end_beat) == (3.0, 3.0)


# ── point 1: consolidate inside the section, wherever it starts ──────────────


def test_a_mid_bar_section_is_consolidated_across_its_whole_extent():
    """Manlio's verse-2 plus its lift spans 20.3 to 32.3. Every hit must be voted on --
    including the ones in the half of bar 20 and the half of bar 32 that belong
    to it. Rounding those away leaves them unconsolidated inside a consolidated
    section, which is the inconsistency Stage 6 exists to remove."""
    hits = backbeat(20, 12, first_beat=3.0, last_beat=3.0)
    span = SectionSpan("verse-2", 20, 32, start_beat=3.0, end_beat=3.0)
    out, report = consolidate(DrumPerformance(hits, TIMELINE), [span])
    times = sorted(h.time for h in out.hits)
    assert times[0] == pytest.approx(at(20, 3.0)), "bar 20 beat 3 is in the section"
    assert times[-1] == pytest.approx(at(32, 2.0)), "bar 32 beat 2 is the last slot"
    assert len(out.hits) == len(hits), "a clean part comes back unchanged in size"


def test_nothing_is_stamped_outside_the_section():
    hits = backbeat(20, 12, first_beat=3.0, last_beat=3.0)
    span = SectionSpan("verse-2", 20, 32, start_beat=3.0, end_beat=3.0)
    out, _ = consolidate(DrumPerformance(hits, TIMELINE), [span])
    lo, hi = at(20, 3.0), at(32, 3.0)
    assert all(lo - 1e-9 <= h.time < hi - 1e-9 for h in out.hits)


def test_a_hit_in_the_partial_first_bar_is_outvoted_like_any_other():
    """The half-bar at the start is part of the pattern, not a special case."""
    hits = backbeat(20, 12, first_beat=3.0, last_beat=3.0)
    hits.append(Hit("crash", at(20, 3.0), 100))     # once only, in the partial bar
    out, _ = consolidate(DrumPerformance(hits, TIMELINE),
                         [SectionSpan("verse-2", 20, 32, start_beat=3.0, end_beat=3.0)])
    assert not [h for h in out.hits if h.instrument == "crash"]


def test_a_slot_is_judged_against_its_own_eligible_repetitions():
    """A slot near the section's edge exists in fewer bars than one in the
    middle, so the same absolute count must not be held to the same share."""
    # A 4-bar section from 1.3 to 5.3, kick on beats 1 and 3. The beat-3 slot is
    # eligible in bars 1-4 and the beat-1 slot in bars 2-5: four chances each,
    # never four out of five. Both survive, and every eligible position is
    # stamped -- eight kicks, not the four you would get by rounding the section
    # to whole bars 2..4.
    hits = backbeat(1, 4, first_beat=3.0, last_beat=3.0)
    out, _ = consolidate(DrumPerformance(hits, TIMELINE),
                         [SectionSpan("v", 1, 5, start_beat=3.0, end_beat=3.0)])
    kicks = sorted(h.time for h in out.hits if h.instrument == "kick")
    assert kicks[0] == pytest.approx(at(1, 3.0)), "the partial first bar is used"
    assert kicks[-1] == pytest.approx(at(5, 1.0)), "so is the partial last one"
    assert len(kicks) == 8


def test_a_bar_aligned_section_is_unchanged_by_all_this():
    """Regression: the existing behaviour is the special case where every slot
    is eligible in every repetition."""
    hits = backbeat(1, 4)
    plain, _ = consolidate(DrumPerformance(hits, TIMELINE), [("verse", 1, 5)])
    spanned, _ = consolidate(DrumPerformance(hits, TIMELINE),
                             [SectionSpan("verse", 1, 5)])
    assert sorted((h.instrument, round(h.time, 9)) for h in plain.hits) == \
           sorted((h.instrument, round(h.time, 9)) for h in spanned.hits)


# ── point 2: identical names pool, similar names do not ─────────────────────


def test_two_sections_with_the_same_name_come_out_identical():
    """Paolo's convention: same name means the same part."""
    hits = backbeat(1, 4) + backbeat(9, 4)
    # a stray in the second copy only -- pooled voting should reject it
    hits.append(Hit("crash", at(9, 1.0), 100))
    out, _ = consolidate(DrumPerformance(hits, TIMELINE),
                         [("chorus", 1, 5), ("chorus", 9, 13)])
    first = sorted((h.instrument, round(h.time - at(1, 1.0), 6)) for h in out.hits
                   if h.time < at(5, 1.0))
    second = sorted((h.instrument, round(h.time - at(9, 1.0), 6)) for h in out.hits
                    if h.time >= at(9, 1.0))
    assert first == second
    assert not [h for h in out.hits if h.instrument == "crash"]


def test_pooling_gives_a_hit_enough_votes_to_survive():
    """Four bars each is 4 repetitions; pooled it is 8. A hit in 5 of 8 clears
    0.55 where 2 of 4 would not -- this is the point of pooling."""
    hits = backbeat(1, 4) + backbeat(9, 4)
    for b in (1, 2, 9, 10, 11):
        hits.append(Hit("tom_mid", at(b, 4.0) + 0.25, 90))
    out, _ = consolidate(DrumPerformance(hits, TIMELINE),
                         [("chorus", 1, 5), ("chorus", 9, 13)])
    assert len([h for h in out.hits if h.instrument == "tom_mid"]) == 8


def test_the_same_name_family_is_not_pooled():
    """verse-2 and verse-3 are structurally alike but must not be forced equal."""
    hits = backbeat(1, 4) + backbeat(9, 4)
    for b in (9, 10, 11, 12):
        hits.append(Hit("tom_mid", at(b, 4.0) + 0.25, 90))   # verse-3 only
    out, _ = consolidate(DrumPerformance(hits, TIMELINE),
                         [("verse-2", 1, 5), ("verse-3", 9, 13)])
    early = [h for h in out.hits if h.instrument == "tom_mid" and h.time < at(5, 1.0)]
    late = [h for h in out.hits if h.instrument == "tom_mid" and h.time >= at(9, 1.0)]
    assert not early, "verse-2 never played it"
    assert len(late) == 4, "verse-3 played it in every bar and keeps it"


def test_pooled_sections_are_reported_together():
    hits = backbeat(1, 4) + backbeat(9, 4)
    _, report = consolidate(DrumPerformance(hits, TIMELINE),
                            [("chorus", 1, 5), ("chorus", 9, 13)])
    chorus = [e for e in report["sections"] if e["name"] == "chorus"]
    assert len(chorus) == 1, "one entry for the pooled part, not two"
    assert chorus[0]["repeats"] == 8


# ── how many repetitions is a vote ───────────────────────────────────────────


def test_two_repetitions_is_not_enough_to_vote_on():
    """With n=2 and a 0.55 threshold a hit must be in BOTH bars: 1 of 2 is 50%
    and fails. That is unanimity, not agreement, and it deletes anything that
    varies at all. Measured on Manlio's dry run it removed a third to a half of
    every break and fill -- theme-intro-stop went 15 hits to 6 at 40%
    agreement. Below the minimum, a section is passed through untouched, which
    is what Stage 7 wants for a one-off anyway."""
    hits = backbeat(1, 2)
    hits.append(Hit("crash", at(1, 1.0), 100))       # in bar 1 only
    out, report = consolidate(DrumPerformance(hits, TIMELINE), [("break", 1, 3)])
    entry = report["sections"][0]
    assert entry.get("skipped") == "too short to vote on"
    assert len(out.hits) == len(hits), "passed through, not halved"
    assert [h for h in out.hits if h.instrument == "crash"]


def test_four_repetitions_is_enough():
    hits = backbeat(1, 4)
    hits.append(Hit("crash", at(1, 1.0), 100))
    out, report = consolidate(DrumPerformance(hits, TIMELINE), [("verse", 1, 5)])
    assert "skipped" not in report["sections"][0]
    assert not [h for h in out.hits if h.instrument == "crash"]


def test_the_minimum_is_adjustable():
    hits = backbeat(1, 2)
    hits.append(Hit("crash", at(1, 1.0), 100))
    out, report = consolidate(DrumPerformance(hits, TIMELINE), [("break", 1, 3)],
                              settings=ConsolidateSettings(min_repeats=2))
    assert "skipped" not in report["sections"][0]


def test_pooling_can_lift_a_short_section_over_the_minimum():
    """Two 2-bar sections with the same name are four repetitions together --
    which is the other reason identical names pool."""
    hits = backbeat(1, 2) + backbeat(9, 2)
    _, report = consolidate(DrumPerformance(hits, TIMELINE),
                            [("break", 1, 3), ("break", 9, 11)])
    entry = report["sections"][0]
    assert "skipped" not in entry
    assert entry["repeats"] == 4
