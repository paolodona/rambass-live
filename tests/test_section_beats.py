"""Sections that do not start on a bar line.

Manlio has a break at Reaper 20.1 lasting 2.5 bars, so verse-2 starts at
Reaper 22.3 -- musical bar 20 beat 3. Paolo: "we use odd measures and sections
do not always fall on the bar". Storage stays musical (CLAUDE.md); the ruler
number belongs only on a Reaper marker label.
"""

from __future__ import annotations

import pytest

from rambass.manifest import Section, load_song, save_song
from rambass.reaper import build_song_script


def _records(script, kind):
    return [r for r in script.records if r[0] == kind]


# ── the dataclass ────────────────────────────────────────────────────────────


def test_a_section_defaults_to_the_downbeat():
    assert Section("intro", 1).beat == 1.0


def test_a_beat_round_trips_through_a_dict():
    section = Section("verse-2", 20, beat=3.0)
    assert Section.from_dict(section.to_dict()) == section


def test_the_downbeat_is_not_written_out():
    """Existing song.yaml files must not all grow a redundant beat: 1.0."""
    assert "beat" not in Section("intro", 1).to_dict()
    assert Section("verse-2", 20, beat=3.0).to_dict()["beat"] == 3.0


def test_an_old_dict_without_a_beat_still_loads():
    assert Section.from_dict({"name": "intro", "bar": 1}).beat == 1.0


# ── ordering and lookup ──────────────────────────────────────────────────────


def test_two_sections_in_one_bar_order_by_beat(song):
    song.sections = [Section("verse-2", 20, beat=3.0), Section("break", 20)]
    song.bars = 40
    ordered = sorted(song.sections, key=lambda s: (s.bar, s.beat))
    assert [s.name for s in ordered] == ["break", "verse-2"]


def test_section_at_asks_about_a_position_not_just_a_bar(song):
    song.bars = 40
    song.sections = [Section("break", 18), Section("verse-2", 20, beat=3.0)]
    # The downbeat of bar 20 is still the break -- the verse has not started.
    assert song.section_at(20).name == "break"
    assert song.section_at(20, beat=2.0).name == "break"
    assert song.section_at(20, beat=3.0).name == "verse-2"
    assert song.section_at(21).name == "verse-2"


def test_two_sections_on_the_same_beat_are_a_problem(song):
    song.bars = 40
    song.sections = [Section("a", 20, beat=3.0), Section("b", 20, beat=3.0)]
    assert any("20" in p for p in song.problems())


def test_two_sections_in_one_bar_on_different_beats_are_fine(song):
    song.bars = 40
    song.sections = [Section("a", 20), Section("b", 20, beat=3.0)]
    assert not [p for p in song.problems() if "both at" in p]
    song.validate()


@pytest.mark.parametrize("beat", [0.0, 0.5, 5.0, 9.0])
def test_a_beat_outside_the_bar_is_a_problem(song, beat):
    song.bars = 40
    song.sections = [Section("a", 20, beat=beat)]
    assert any("beat" in p for p in song.problems())


def test_a_beat_on_a_subdivision_is_allowed(song):
    """3.5 is the second eighth of beat 3 -- legal, and 12/8 songs need it."""
    song.bars = 40
    song.sections = [Section("a", 20, beat=3.5)]
    assert not [p for p in song.problems() if "beat" in p]


# ── through the build script ─────────────────────────────────────────────────


def test_a_mid_bar_section_starts_where_the_beat_says(song):
    """120 BPM 4/4: half a second a beat, two bars of count-in = 4.0 s."""
    song.bars = 40
    song.sections = [Section("break", 18), Section("verse-2", 20, beat=3.0)]
    save_song(song, song.dir)
    script = build_song_script(load_song(song.dir))
    markers = {r[2]: float(r[1]) for r in _records(script, "MARKER")}
    # bar 20 beat 3 = 19 bars + 2 beats after musical zero, plus the count-in
    assert markers["22.3 verse-2"] == pytest.approx(4.0 + 19 * 2.0 + 2 * 0.5)


def test_a_region_ends_where_the_next_section_begins_even_mid_bar(song):
    song.bars = 40
    song.sections = [Section("break", 18), Section("verse-2", 20, beat=3.0)]
    save_song(song, song.dir)
    script = build_song_script(load_song(song.dir))
    regions = {r[3]: (float(r[1]), float(r[2])) for r in _records(script, "REGION")}
    start, end = regions["break"]
    assert end - start == pytest.approx(2.5 * 2.0), "the break is 2.5 bars long"
    assert regions["verse-2"][0] == pytest.approx(end)


def test_marker_labels_carry_the_beat_in_reaper_numbering(song):
    song.bars = 40
    song.sections = [Section("intro", 1), Section("verse-2", 20, beat=3.0)]
    save_song(song, song.dir)
    labels = [r[2] for r in _records(build_song_script(load_song(song.dir)), "MARKER")]
    assert "3.1 intro" in labels
    assert "22.3 verse-2" in labels


# ── the CLI accepts Reaper's bar.beat notation ───────────────────────────────


def test_parse_position_reads_bar_dot_beat():
    from rambass.cli import parse_position

    assert parse_position("22.3") == (22, 3.0)
    assert parse_position("20") == (20, 1.0)
    assert parse_position("20.1") == (20, 1.0)
    assert parse_position("22.3.5") == (22, 3.5)


def test_parse_position_rejects_nonsense():
    from rambass.cli import parse_position
    from rambass.project import ProjectError

    for text in ("", "x", "1.2.3.4", "-3.1", "1.x"):
        with pytest.raises(ProjectError):
            parse_position(text)


def test_section_command_takes_a_reaper_position(cwd_song):
    from rambass.cli import main

    song = cwd_song
    assert main(["section", song.slug, "22.3", "verse-2", "--reaper-bar"]) == 0
    reloaded = load_song(song.dir)
    added = [s for s in reloaded.sections if s.name == "verse-2"][0]
    assert (added.bar, added.beat) == (20, 3.0)     # 22 - 2 bars of count-in


def test_section_command_without_reaper_bar_is_musical(cwd_song):
    from rambass.cli import main

    song = cwd_song
    assert main(["section", song.slug, "22.3", "verse-2"]) == 0
    added = [s for s in load_song(song.dir).sections if s.name == "verse-2"][0]
    assert (added.bar, added.beat) == (22, 3.0)


def test_replacing_a_section_matches_bar_and_beat(cwd_song):
    from rambass.cli import main

    song = cwd_song
    main(["section", song.slug, "20.1", "break"])
    main(["section", song.slug, "20.3", "verse-2"])
    names = [s.name for s in load_song(song.dir).sections if s.bar == 20]
    assert sorted(names) == ["break", "verse-2"], "beat 3 must not replace beat 1"
    main(["section", song.slug, "20.3", "renamed"])
    names = [s.name for s in load_song(song.dir).sections if s.bar == 20]
    assert sorted(names) == ["break", "renamed"]


def test_a_reaper_position_inside_the_count_in_is_refused(cwd_song):
    from rambass.cli import main

    assert main(["section", cwd_song.slug, "2.1", "nope", "--reaper-bar"]) == 2


# ── handing sections to consolidate ──────────────────────────────────────────
#
# Paolo: "it is important that consolidate consolidates within the section,
# regardless of where it starts (with odd timing we will rarely fit into a .1
# start of section generally)". So these spans are the sections' EXACT extents.
# An earlier version rounded them to whole bars, which both weakened the vote
# and left the discarded half-bars unconsolidated inside a consolidated section
# -- consolidate handles the partial repetitions itself, per slot.


def test_spans_are_the_exact_extents_not_rounded_to_bars(song):
    song.bars = 40
    song.sections = [Section("a", 1), Section("b", 9), Section("c", 17)]
    spans = song.consolidation_spans()
    assert [(s.name, s.start_bar, s.end_bar) for s in spans] == [
        ("a", 1, 9), ("b", 9, 17), ("c", 17, 41)
    ]
    assert all(s.start_beat == 1.0 and s.end_beat == 1.0 for s in spans)


def test_a_mid_bar_section_keeps_its_beat_on_both_edges(song):
    """Manlio's break-1 ends where verse-2 begins: bar 20 beat 3, not bar 20 or
    bar 21. Both sections get the half of bar 20 that is theirs."""
    song.bars = 40
    song.sections = [Section("break-1", 18), Section("verse-2", 20, beat=3.0)]
    spans = {s.name: s for s in song.consolidation_spans()}
    assert (spans["break-1"].start_bar, spans["break-1"].start_beat) == (18, 1.0)
    assert (spans["break-1"].end_bar, spans["break-1"].end_beat) == (20, 3.0)
    assert (spans["verse-2"].start_bar, spans["verse-2"].start_beat) == (20, 3.0)


def test_the_spans_tile_the_song_without_gaps_or_overlaps(song):
    """Every span ends exactly where the next begins -- no bar is dropped and
    none is voted into two sections."""
    song.bars = 80
    song.sections = [
        Section("theme-intro", 1), Section("verse-1", 6), Section("break-1", 18),
        Section("verse-2", 20, beat=3.0), Section("chorus-1", 32, beat=3.0),
        Section("break-2", 40, beat=3.0), Section("verse-3", 43),
    ]
    spans = song.consolidation_spans()
    for current, following in zip(spans, spans[1:], strict=False):
        assert (current.end_bar, current.end_beat) == \
               (following.start_bar, following.start_beat)
    assert (spans[-1].end_bar, spans[-1].end_beat) == (81, 1.0)


def test_a_very_short_section_is_still_handed_over(song):
    """Whether there is enough to vote on is consolidate's judgement, not ours
    -- it reports 'too short to vote on' and passes the hits through, where
    dropping the span here would silently hide the section from the report."""
    song.bars = 40
    song.sections = [Section("a", 1), Section("blink", 9, beat=3.0), Section("b", 10)]
    names = [s.name for s in song.consolidation_spans()]
    assert names == ["a", "blink", "b"]


def test_duplicate_names_are_preserved_for_pooling(song):
    """Same name means the same part, and consolidate pools them. Making them
    unique here would defeat that."""
    song.bars = 40
    song.sections = [Section("chorus", 5), Section("verse", 13),
                     Section("chorus", 21)]
    assert [s.name for s in song.consolidation_spans()] == \
           ["chorus", "verse", "chorus"]


def test_no_sections_means_no_spans(song):
    song.sections = []
    assert song.consolidation_spans() == []
