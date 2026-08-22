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
