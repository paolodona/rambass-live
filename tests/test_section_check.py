"""The sections checker: suggests, never decides.

Paolo asked for something that "checks the rules above, suggests merging
sections, or normalizing them (eg sections x and y should be normalized to x) or
suggest splits (eg: should we split the intro section at 6.1 where there is a
significant change of drum pattern)".

Same philosophy as arrange.py, which deliberately does not generate a running
order: sectioning is a musical judgement, and what a tool can usefully do is
catch what a human misses in their own list. So every finding here is a
suggestion with its evidence attached, and nothing edits song.yaml.
"""

from __future__ import annotations

import pytest

from rambass.manifest import Section
from rambass.midiio import DrumPerformance, Hit
from rambass.sections import check_sections
from rambass.timeline import Timeline

TIMELINE = Timeline(bpm=120.0)   # beat 0.5 s, bar 2.0 s


def at(bar, beat=1.0):
    return TIMELINE.bar_beat_to_seconds(bar, beat)


def groove(first_bar, bars, *, snare=True, hats=True):
    """Kick on 1 and 3; snare on 2 and 4 if asked; a hat on every beat."""
    out = []
    for b in range(first_bar, first_bar + bars):
        for beat in (1.0, 3.0):
            out.append(Hit("kick", at(b, beat), 100))
        if snare:
            for beat in (2.0, 4.0):
                out.append(Hit("snare", at(b, beat), 105))
        if hats:
            for beat in (1.0, 2.0, 3.0, 4.0):
                out.append(Hit("hihat_closed", at(b, beat), 70))
    return out


def kinds(findings):
    return sorted({f.kind for f in findings})


# ── the checks that need no audio ────────────────────────────────────────────


def test_a_clean_arrangement_has_nothing_to_say(song):
    song.bars = 32
    song.sections = [Section("verse", 1), Section("chorus", 17)]
    assert check_sections(song) == []


def test_sections_sharing_a_name_must_be_the_same_length(song):
    """Same name means one part, and consolidate will force them identical --
    so differing lengths mean one of them is going to be wrong."""
    song.bars = 40
    song.sections = [Section("chorus", 1), Section("verse", 9),
                     Section("chorus", 25)]        # 8 bars, then 16
    findings = check_sections(song)
    assert "same-name-different-length" in kinds(findings)
    assert any("chorus" in f.message for f in findings)


def test_a_section_too_short_to_vote_on_is_reported(song):
    song.bars = 40
    song.sections = [Section("verse", 1), Section("stab", 9), Section("verse-2", 10)]
    findings = check_sections(song)
    assert "too-short-to-consolidate" in kinds(findings)


def test_a_section_past_the_end_of_the_song_is_reported(song):
    song.bars = 8
    song.sections = [Section("verse", 1), Section("outro", 9)]
    findings = check_sections(song)
    assert "section-past-the-end" in kinds(findings)


def test_findings_carry_a_reaper_position(song):
    """Every suggestion has to be checkable against the ruler on screen."""
    song.bars = 40
    song.sections = [Section("verse", 1), Section("stab", 9), Section("verse-2", 10)]
    short = [f for f in check_sections(song) if f.kind == "too-short-to-consolidate"]
    assert short and short[0].reaper == "11.1"      # bar 9 + 2 count-in bars


# ── splits: an internal pattern change ───────────────────────────────────────


def test_a_verse_whose_snare_arrives_halfway_is_offered_a_split(song):
    """Manlio's case: eight bars of hats and kick, then the snare comes in.
    Consolidating the whole thing would delete the snare -- 4 bars of 12 is 33%
    against a 0.55 threshold -- so the split is the finding that matters most."""
    song.bars = 32
    song.sections = [Section("verse-1", 1), Section("chorus", 17)]
    hits = groove(1, 8, snare=False) + groove(9, 8) + groove(17, 16)
    findings = check_sections(song, DrumPerformance(hits, TIMELINE))
    splits = [f for f in findings if f.kind == "suggest-split"]
    assert splits, "the snare entering is exactly what this must catch"
    assert splits[0].reaper == "11.1", "bar 9, in Reaper's numbering"
    assert "snare" in splits[0].message


def test_a_uniform_section_is_not_offered_a_split(song):
    song.bars = 32
    song.sections = [Section("verse", 1), Section("chorus", 17)]
    hits = groove(1, 16) + groove(17, 16)
    findings = check_sections(song, DrumPerformance(hits, TIMELINE))
    assert "suggest-split" not in kinds(findings)


def test_a_split_is_never_offered_where_it_would_leave_a_stub(song):
    """Both halves have to be long enough to vote on, or the suggestion is
    trading one unconsolidatable section for two."""
    song.bars = 32
    song.sections = [Section("verse", 1), Section("chorus", 17)]
    hits = groove(1, 15, snare=False) + groove(16, 1) + groove(17, 16)
    findings = check_sections(song, DrumPerformance(hits, TIMELINE))
    assert not [f for f in findings if f.kind == "suggest-split"]


def test_a_split_respects_a_mid_bar_section_start(song):
    song.bars = 40
    song.sections = [Section("verse-2", 4, beat=3.0), Section("chorus", 20)]
    hits = groove(5, 8, snare=False) + groove(13, 7) + groove(20, 20)
    findings = check_sections(song, DrumPerformance(hits, TIMELINE))
    splits = [f for f in findings if f.kind == "suggest-split"]
    assert splits, "a mid-bar section must still be checked internally"


# ── merges and normalising ───────────────────────────────────────────────────


def test_two_differently_named_sections_playing_the_same_part_are_flagged(song):
    """Paolo: "sections x and y should be normalized to x". Same part, two
    names, so consolidate votes them separately and gets half the evidence."""
    song.bars = 40
    song.sections = [Section("verse-2", 1), Section("bridge", 9),
                     Section("verse-3", 17)]
    hits = groove(1, 8) + groove(9, 8, hats=False) + groove(17, 24)
    findings = check_sections(song, DrumPerformance(hits, TIMELINE))
    same = [f for f in findings if f.kind == "suggest-normalise"]
    assert same, "verse-2 and verse-3 play the same thing"
    assert "verse-2" in same[0].message and "verse-3" in same[0].message


def test_adjacent_sections_playing_the_same_part_are_offered_a_merge(song):
    song.bars = 40
    song.sections = [Section("verse", 1), Section("bridge", 17)]
    hits = groove(1, 16) + groove(17, 24)
    findings = check_sections(song, DrumPerformance(hits, TIMELINE))
    assert "suggest-merge" in kinds(findings)


def test_a_real_difference_is_left_alone(song):
    """verse-2 and verse-3 are alike but not equal, and that is allowed."""
    song.bars = 40
    song.sections = [Section("verse-2", 1), Section("verse-3", 17)]
    hits = groove(1, 16) + groove(17, 24, hats=False)
    findings = check_sections(song, DrumPerformance(hits, TIMELINE))
    assert "suggest-normalise" not in kinds(findings)
    assert "suggest-merge" not in kinds(findings)


def test_nothing_is_suggested_without_a_performance(song):
    """The pure checks still run; the pattern-based ones need the MIDI."""
    song.bars = 40
    song.sections = [Section("verse", 1), Section("bridge", 17)]
    findings = check_sections(song)
    assert "suggest-merge" not in kinds(findings)
    assert "suggest-split" not in kinds(findings)


def test_the_checker_never_edits_the_song(song):
    song.bars = 32
    song.sections = [Section("verse-1", 1), Section("chorus", 17)]
    before = [(s.name, s.bar, s.beat) for s in song.sections]
    check_sections(song, DrumPerformance(groove(1, 32), TIMELINE))
    assert [(s.name, s.bar, s.beat) for s in song.sections] == before


def test_no_sections_is_not_a_crash(song):
    song.sections = []
    assert check_sections(song, DrumPerformance([], TIMELINE)) == []


# ── the two ways the first cut of this got it wrong ───────────────────────────


def test_one_stray_hit_in_a_two_bar_window_is_not_a_split(song):
    """Coverage over 2 bars quantises to 0/50/100%, so a single tom reads as
    "50% of bars" and scored 0.67 -- above the threshold, and meaningless.
    Measured on Manlio: this produced three false splits inside the verses."""
    song.bars = 32
    song.sections = [Section("verse-1", 1), Section("chorus", 17)]
    hits = groove(1, 16) + groove(17, 16)
    hits.append(Hit("tom_mid", at(15, 4.0) + 0.25, 90))    # exactly one
    findings = check_sections(song, DrumPerformance(hits, TIMELINE))
    assert not [f for f in findings if f.kind == "suggest-split"]


def test_two_different_grooves_using_the_same_kit_are_not_the_same_part(song):
    """chorus-1 and theme-finale both play kick, snare and hats in every bar, so
    per-instrument coverage called them identical at score 0.01. They are not:
    the hits are in different places. Sameness has to be judged per slot."""
    song.bars = 40
    straight = []
    for b in range(1, 17):
        for beat in (1.0, 3.0):
            straight.append(Hit("kick", at(b, beat), 100))
        for beat in (2.0, 4.0):
            straight.append(Hit("snare", at(b, beat), 105))
    shifted = []
    for b in range(17, 41):
        for beat in (2.0, 4.0):                 # kick and snare swapped over
            shifted.append(Hit("kick", at(b, beat), 100))
        for beat in (1.0, 3.0):
            shifted.append(Hit("snare", at(b, beat), 105))
    findings = check_sections(song, DrumPerformance(straight + shifted, TIMELINE))
    song.sections = [Section("chorus-1", 1), Section("theme-finale", 17)]
    findings = check_sections(song, DrumPerformance(straight + shifted, TIMELINE))
    assert "suggest-normalise" not in kinds(findings)
    assert "suggest-merge" not in kinds(findings)


def test_an_opening_crash_is_not_an_internal_boundary(song):
    """A crash lands at the top of a section and nowhere else in it -- that is
    what a crash is for. Measured on Manlio, two crashes in verse-3's first
    four bars scored 0.75 and suggested splitting a section that does not
    change. Accents and fills are excluded from the split metric: crashes are
    one-offs by nature and toms are fills, and Stage 7 handles both by hand."""
    song.bars = 40
    song.sections = [Section("verse-3", 1), Section("chorus", 17)]
    hits = groove(1, 16) + groove(17, 24)
    hits += [Hit("crash", at(1, 1.0), 100), Hit("crash", at(2, 1.0), 100)]
    hits += [Hit("tom_mid", at(3, 4.0) + 0.25, 90)]
    findings = check_sections(song, DrumPerformance(hits, TIMELINE))
    assert not [f for f in findings if f.kind == "suggest-split"]


def test_the_timekeeping_voices_still_trigger_a_split(song):
    """Excluding accents must not blind it to the thing it is for."""
    song.bars = 40
    song.sections = [Section("verse-1", 1), Section("chorus", 17)]
    hits = groove(1, 8, snare=False) + groove(9, 8) + groove(17, 24)
    hits.append(Hit("crash", at(1, 1.0), 100))
    findings = check_sections(song, DrumPerformance(hits, TIMELINE))
    splits = [f for f in findings if f.kind == "suggest-split"]
    assert splits and splits[0].reaper == "11.1"


# ── the command ──────────────────────────────────────────────────────────────


def test_the_sections_command_lists_and_checks(song, project, monkeypatch, capsys):
    from rambass.cli import main
    from rambass.manifest import save_song

    monkeypatch.chdir(project.root)
    song.bars = 40
    song.sections = [Section("verse", 1), Section("stab", 9), Section("chorus", 10)]
    save_song(song, song.dir)
    assert main(["sections", song.slug]) == 0
    out = capsys.readouterr().out
    assert "verse" in out and "chorus" in out
    assert "3.1" in out, "positions are shown in Reaper's numbering"
    assert "too-short-to-consolidate" in out


def test_the_sections_command_says_when_there_is_nothing_wrong(song, project,
                                                              monkeypatch, capsys):
    from rambass.cli import main
    from rambass.manifest import save_song

    monkeypatch.chdir(project.root)
    song.bars = 32
    song.sections = [Section("verse", 1), Section("chorus", 17)]
    save_song(song, song.dir)
    assert main(["sections", song.slug]) == 0
    assert "nothing to suggest" in capsys.readouterr().out


def test_the_sections_command_never_writes(song, project, monkeypatch):
    from rambass.cli import main
    from rambass.manifest import load_song, save_song

    monkeypatch.chdir(project.root)
    song.bars = 40
    song.sections = [Section("verse", 1), Section("stab", 9), Section("chorus", 10)]
    save_song(song, song.dir)
    before = (song.dir / "song.yaml").read_text(encoding="utf-8")
    main(["sections", song.slug])
    assert (song.dir / "song.yaml").read_text(encoding="utf-8") == before
    assert len(load_song(song.dir).sections) == 3


# ── editing the section list ─────────────────────────────────────────────────
#
# `rambass section` could add or replace and had no way to remove, and the
# replace rebuilt the Section from scratch -- so re-marking a section to fix its
# name dropped `backbeat` and `note` with it. On Manlio that silently
# un-declares three verses of side-sticks, which CLAUDE.md records as
# arrangement structure settled by ear once, not a threshold. These are the
# edits both `rambass section` and the console's sections screen go through.


def _sectioned(song, *pairs):
    """*song* with exactly these (bar, beat, name) sections."""
    from rambass.manifest import Section

    song.sections = [Section(name=name, bar=bar, beat=beat)
                     for bar, beat, name in pairs]
    return song


def test_replacing_a_section_keeps_its_backbeat_and_note(song):
    """The bug. Manlio's verse-2 carries `backbeat: sidestick`, and re-marking
    it to correct a typo threw that away -- changing the rendered part, with
    nothing in the diff explaining why the clicks had gone."""
    from rambass.manifest import Section
    from rambass.sections import add_section

    song.sections = [Section(name="verse-2", bar=20, beat=3.0,
                             backbeat="sidestick", note="cross-stick throughout")]

    add_section(song, bar=20, beat=3.0, name="verse-two")

    assert len(song.sections) == 1
    kept = song.sections[0]
    assert kept.name == "verse-two"
    assert kept.backbeat == "sidestick"
    assert kept.note == "cross-stick throughout"


def test_an_explicit_backbeat_still_wins_over_the_one_carried_forward(song):
    """Carrying it forward must not make it unsettable -- a verse re-heard as
    played on the head has to be able to say so."""
    from rambass.manifest import Section
    from rambass.sections import add_section

    song.sections = [Section(name="verse-2", bar=20, beat=3.0,
                             backbeat="sidestick")]

    add_section(song, bar=20, beat=3.0, name="verse-2", backbeat="")
    assert song.sections[0].backbeat == ""


def test_replacing_matches_on_bar_and_beat_together(song):
    """A 2.5-bar break puts two sections in one bar -- Manlio has exactly
    that -- so replacing by bar alone would delete the neighbour."""
    from rambass.sections import add_section

    _sectioned(song, (20, 1.0, "break"), (20, 3.0, "verse-2"))

    add_section(song, bar=20, beat=3.0, name="verse-two")

    assert [(s.bar, s.beat, s.name) for s in song.sections] == [
        (20, 1.0, "break"), (20, 3.0, "verse-two")]


def test_adding_keeps_the_list_in_position_order(song):
    from rambass.sections import add_section

    _sectioned(song, (1, 1.0, "intro"), (17, 1.0, "chorus"))
    add_section(song, bar=9, beat=1.0, name="verse")

    assert [s.name for s in song.sections] == ["intro", "verse", "chorus"]


def test_an_invalid_position_is_refused_before_it_is_saved(song):
    """`song.problems()` already knows the rules -- beats are 1-based and the
    last one is under beats-per-bar + 1 -- so this surfaces them rather than
    growing a second validator."""
    from rambass.project import ProjectError
    from rambass.sections import add_section

    _sectioned(song, (9, 1.0, "verse"))
    with pytest.raises(ProjectError):
        add_section(song, bar=9, beat=7.0, name="nope")


def test_removing_a_section_returns_the_one_it_took(song):
    from rambass.sections import remove_section

    _sectioned(song, (1, 1.0, "intro"), (20, 3.0, "verse-2"))

    gone = remove_section(song, bar=20, beat=3.0)

    assert gone.name == "verse-2"
    assert [s.name for s in song.sections] == ["intro"]


def test_removing_what_is_not_there_names_what_is(song):
    """In Reaper's numbers, because that is what a human is reading off the
    ruler when they get the position wrong."""
    from rambass.project import ProjectError
    from rambass.sections import remove_section

    _sectioned(song, (1, 1.0, "intro"), (9, 1.0, "verse"))
    with pytest.raises(ProjectError) as caught:
        remove_section(song, bar=13, beat=1.0)

    message = str(caught.value)
    assert "no section at" in message
    # count_in.bars is 2 on the fixture, so musical 1 and 9 are Reaper 3 and 11.
    assert "3.1" in message and "11.1" in message


def test_a_reaper_bar_has_the_count_in_taken_off_it(song):
    """The subtraction happens once, here, at the edge. Paolo reads 22.3 off
    the ruler and the manifest stores musical 20.3."""
    from rambass.sections import to_musical

    assert song.count_in_bars == 2
    assert to_musical(song, 22, reaper=True) == 20
    assert to_musical(song, 22, reaper=False) == 22


def test_a_reaper_bar_inside_the_count_in_is_refused(song):
    from rambass.project import ProjectError
    from rambass.sections import to_musical

    with pytest.raises(ProjectError) as caught:
        to_musical(song, 2, reaper=True)
    assert "count-in" in str(caught.value)


def test_the_section_table_carries_the_ruler_reading_and_the_span(song):
    """One source for the CLI listing and the console screen, so the two cannot
    disagree about where a section is or how long it runs."""
    from rambass.sections import section_table

    _sectioned(song, (1, 1.0, "intro"), (9, 1.0, "verse"), (17, 1.0, "chorus"))
    table = section_table(song)

    assert [row["reaper"] for row in table["sections"]] == ["3.1", "11.1", "19.1"]
    assert [row["span_bars"] for row in table["sections"]] == [8.0, 8.0, 16.0]
    assert table["count_in_bars"] == 2
    assert table["names"] == ["chorus", "intro", "verse"]


def test_the_section_table_reports_the_checkers_own_findings(song):
    """Not a second copy of the wording: `check_sections` is the checker and
    this only carries what it said."""
    from rambass.sections import check_sections, section_table

    # Two sections with one name and different lengths -- the pooling rule.
    _sectioned(song, (1, 1.0, "verse"), (9, 1.0, "verse"), (13, 1.0, "outro"))
    table = section_table(song)

    assert [f["message"] for f in table["findings"]] == [
        finding.message for finding in check_sections(song)]
    assert any(f["kind"] == "same-name-different-length" for f in table["findings"])


def test_the_table_is_not_a_crash_without_any_sections(song):
    from rambass.sections import section_table

    song.sections = []
    table = section_table(song)
    assert table["sections"] == [] and table["names"] == []
