"""Stage 6 of docs/drums-rebuild.md: consolidating a section to one pattern."""

import pytest

from rambass.midiio import DrumPerformance, Hit
from rambass.quantize import ConsolidateSettings, consolidate
from rambass.timeline import Timeline

TIMELINE = Timeline(bpm=120.0)  # a beat is 0.5 s, a bar 2.0 s, a 16th 0.125 s


def bar(n):
    return TIMELINE.bar_beat_to_seconds(n, 1.0)


def backbeat(first_bar, bars, *, velocity=100):
    """Kick on 1 and 3, snare on 2 and 4, for each bar."""
    out = []
    for b in range(first_bar, first_bar + bars):
        for beat, name in ((1.0, "kick"), (2.0, "snare"), (3.0, "kick"), (4.0, "snare")):
            out.append(Hit(name, TIMELINE.bar_beat_to_seconds(b, beat), velocity))
    return out


def test_a_hit_missing_from_one_bar_is_restored():
    """The verse loses a hat in bar 3 — the other bars outvote it."""
    hits = backbeat(1, 4)
    hits = [h for h in hits if not (h.instrument == "snare" and abs(h.time - bar(3) - 0.5) < 1e-9)]
    performance = DrumPerformance(hits, TIMELINE)
    out, report = consolidate(performance, [("verse", 1, 5)])
    snares = [h for h in out.hits if h.instrument == "snare"]
    assert len(snares) == 8  # two per bar, all four bars
    assert report["sections"][0]["repeats"] == 4


def test_a_phantom_hit_in_one_bar_is_dropped():
    """A tom the detector invented in one bar of four does not survive."""
    hits = backbeat(1, 4) + [Hit("tom_high", TIMELINE.bar_beat_to_seconds(2, 3.5), 90)]
    out, _ = consolidate(DrumPerformance(hits, TIMELINE), [("verse", 1, 5)])
    assert not [h for h in out.hits if h.instrument == "tom_high"]


def test_something_played_in_most_bars_survives():
    hits = backbeat(1, 4)
    for b in (1, 2, 4):  # three of four
        hits.append(Hit("hihat_open", TIMELINE.bar_beat_to_seconds(b, 4.5), 80))
    out, _ = consolidate(DrumPerformance(hits, TIMELINE), [("verse", 1, 5)])
    opens = [h for h in out.hits if h.instrument == "hihat_open"]
    assert len(opens) == 4  # stamped across every bar, including the one that missed it


def test_velocity_is_the_median_of_the_group():
    hits = []
    for b, velocity in ((1, 60), (2, 100), (3, 80), (4, 127)):
        hits.append(Hit("kick", TIMELINE.bar_beat_to_seconds(b, 1.0), velocity))
    out, _ = consolidate(DrumPerformance(hits, TIMELINE), [("verse", 1, 5)])
    assert {h.velocity for h in out.hits} == {100}


def test_a_two_bar_pattern_is_not_flattened_to_one():
    """The crash every other bar is the pattern, not an inconsistency."""
    hits = backbeat(1, 8)
    for b in (1, 3, 5, 7):
        hits.append(Hit("crash", TIMELINE.bar_beat_to_seconds(b, 1.0), 110))
    out, report = consolidate(DrumPerformance(hits, TIMELINE), [("verse", 1, 9)])
    crashes = sorted(h.time for h in out.hits if h.instrument == "crash")
    assert report["sections"][0]["unit_bars"] == 2
    assert crashes == [bar(1), bar(3), bar(5), bar(7)]


def test_a_one_bar_pattern_prefers_the_one_bar_unit():
    out, report = consolidate(DrumPerformance(backbeat(1, 8), TIMELINE), [("verse", 1, 9)])
    assert report["sections"][0]["unit_bars"] == 1


def test_hits_outside_every_section_are_left_alone():
    hits = backbeat(1, 4) + [Hit("crash", bar(9), 120)]
    out, report = consolidate(DrumPerformance(hits, TIMELINE), [("verse", 1, 5)])
    assert any(h.instrument == "crash" and h.time == bar(9) for h in out.hits)
    assert report["untouched"] >= 1


def test_a_section_too_short_to_vote_is_reported_not_mangled():
    hits = backbeat(1, 1)
    out, report = consolidate(DrumPerformance(hits, TIMELINE), [("intro", 1, 2)])
    assert report["sections"][0]["skipped"]
    assert len(out.hits) == len(hits)


def test_the_threshold_is_adjustable():
    hits = backbeat(1, 4)
    hits += [Hit("ride", TIMELINE.bar_beat_to_seconds(b, 1.0), 70) for b in (1, 2)]
    strict, _ = consolidate(DrumPerformance(hits, TIMELINE), [("v", 1, 5)])
    loose, _ = consolidate(DrumPerformance(hits, TIMELINE), [("v", 1, 5)],
                           settings=ConsolidateSettings(threshold=0.5))
    assert not [h for h in strict.hits if h.instrument == "ride"]
    assert len([h for h in loose.hits if h.instrument == "ride"]) == 4


def test_report_counts_the_whole_performance():
    performance = DrumPerformance(backbeat(1, 4), TIMELINE)
    _, report = consolidate(performance, [("verse", 1, 5)])
    assert report["hits_before"] == 16
    assert report["hits_after"] == 16
    assert report["sections"][0]["coverage"] == pytest.approx(1.0)


def test_a_chord_that_never_reliably_co_occurred_is_not_stamped():
    """Codex's review (Aug 2026): voting independently per instrument can
    combine "the kick from one repetition, the snare ghost note from another,
    the hats from a third" into a chord nobody actually played.

    snare and hihat_open each clear the 0.55 vote on their own at the same
    slot -- snare in 6 of 8 bars, hihat_open in 5 of 8 -- but they only land in
    the *same* bar together 3 times out of 8 (bars 4-6). Stamping both across
    every one of the 8 output bars would invent a chord that happened well
    under half as often as either instrument alone. Only the better-attested
    one -- snare -- should survive, and the drop should be on the record.
    """
    hits = [Hit("kick", bar(b), 100) for b in range(1, 9)]
    hits += [Hit("snare", bar(b) + 0.5, 100) for b in range(1, 7)]        # 6/8
    hits += [Hit("hihat_open", bar(b) + 0.5, 90) for b in range(4, 9)]    # 5/8
    out, report = consolidate(
        DrumPerformance(hits, TIMELINE), [("verse", 1, 9)],
        settings=ConsolidateSettings(unit_bars=1),
    )

    for b in range(1, 9):
        at_beat_2 = {h.instrument for h in out.hits
                     if abs(h.time - (bar(b) + 0.5)) < 1e-6}
        assert at_beat_2 == {"snare"}, f"bar {b}: {at_beat_2}"

    demoted = report["sections"][0]["demoted"]
    assert len(demoted) == 1
    assert demoted[0]["kept"] == "snare"
    assert demoted[0]["dropped"] == ["hihat_open"]
    assert demoted[0]["joint_coverage"] == pytest.approx(3 / 8)


def test_a_chord_that_does_reliably_co_occur_is_stamped_together():
    """The positive case: two instruments that really do land together often
    enough are kept together, not just the louder-voting one of the two."""
    hits = [Hit("kick", bar(b), 100) for b in range(1, 9)]
    hits += [Hit("snare", bar(b) + 0.5, 100) for b in range(1, 9)]          # 8/8
    hits += [Hit("hihat_open", bar(b) + 0.5, 90) for b in (1, 2, 3, 4, 5, 6)]  # 6/8
    out, report = consolidate(
        DrumPerformance(hits, TIMELINE), [("verse", 1, 9)],
        settings=ConsolidateSettings(unit_bars=1),
    )
    for b in range(1, 9):
        at_beat_2 = {h.instrument for h in out.hits
                     if abs(h.time - (bar(b) + 0.5)) < 1e-6}
        assert at_beat_2 == {"snare", "hihat_open"}, f"bar {b}: {at_beat_2}"
    assert "demoted" not in report["sections"][0]
