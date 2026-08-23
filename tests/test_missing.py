"""The ledger: what the pipeline removed, and what it never named.

Paolo: *"you mentioned that some crashes / toms etc are removed by design. We
will have to find a way to highlight where those are missing VS the original
track, so that I dont mistakenly forget some in the process. There needs to be a
clear list of missing hits."*

Every deliberate omission upstream is knowable, and this is where it gets
written down. Two sources, both computable from the MIDI and the section list
with no audio at all:

* **the consolidate diff** — exact. Measured on Manlio: 1109 hits in, 1011 out,
  crashes 10 -> 4 and toms 88 -> 63, because a crash appears once in eight
  repetitions (12%) against a 0.55 threshold and a fill is by definition the bar
  that does not repeat.
* **section-boundary crashes** — candidates.
  :func:`~rambass.transcribe.split_cymbal_runs` calls an ambiguous cymbal an open
  hi-hat on purpose, so the crashes that mark section starts arrive as open hats.
  Measured: of the 243 measurable cymbal-stem hits, the 10 sitting within 0.6
  beat of a section start decay at a median +3.4 dB over 250 ms against -19.2 dB
  for the other 233 — so those ten are crashes, and six section starts have no
  cymbal on them at all, which is worth knowing too.

Bar numbers in a report are **Reaper's**, because that is the ruler Paolo reads
them off. Bar numbers in the data are musical. See CLAUDE.md.
"""

from __future__ import annotations

import pytest

from rambass.manifest import Section
from rambass.midiio import DrumPerformance, Hit
from rambass.restore import crash_candidates, missing_hits
from rambass.timeline import Timeline


def _perf(hits, bpm=60.0):
    return DrumPerformance(sorted(hits, key=lambda h: (h.time, h.instrument)),
                           Timeline(bpm=bpm))


SECTIONS = [Section(name="verse-1", bar=1), Section(name="chorus", bar=3)]


# ── the consolidate diff ─────────────────────────────────────────────────────


def test_a_hit_the_vote_deleted_is_reported_with_its_bar_and_section():
    before = _perf([Hit("kick", 0.0, 100), Hit("crash", 0.0, 105),
                    Hit("kick", 4.0, 100)])
    after = _perf([Hit("kick", 0.0, 100), Hit("kick", 4.0, 100)])
    gone = missing_hits(before, after, SECTIONS, end_bar=5, subdivision=3)
    assert len(gone) == 1
    assert (gone[0].instrument, gone[0].bar, gone[0].beat) == ("crash", 1, 1.0)
    assert gone[0].section == "verse-1"
    assert gone[0].velocity == 105


def test_a_hit_the_vote_kept_is_not_reported():
    before = _perf([Hit("kick", 0.0, 100), Hit("snare", 1.0, 110)])
    assert missing_hits(before, before, SECTIONS, end_bar=5, subdivision=3) == []


def test_a_hit_the_vote_moved_to_its_modal_slot_is_not_missing():
    """consolidate places the survivor at the modal slot, which can be a
    subdivision away from where this particular bar played it. Reporting that as
    a deletion would bury the real ones in noise."""
    before = _perf([Hit("kick", 0.0, 100), Hit("tom_mid", 1.30, 80)])
    after = _perf([Hit("kick", 0.0, 100), Hit("tom_mid", 1.3333, 80)])
    assert missing_hits(before, after, SECTIONS, end_bar=5, subdivision=3) == []


def test_a_hit_a_whole_slot_away_is_a_different_hit():
    before = _perf([Hit("tom_mid", 1.0, 80)])
    after = _perf([Hit("tom_mid", 1.3333, 80)])
    gone = missing_hits(before, after, SECTIONS, end_bar=5, subdivision=3)
    assert len(gone) == 1


def test_two_hits_in_one_slot_do_not_cancel_a_third():
    """Both files quantise to the same grid, so a slot holds one hit per
    instrument and matching is per (instrument, slot). A flam is deflam's job."""
    before = _perf([Hit("tom_mid", 1.0, 80), Hit("tom_mid", 5.0, 70)])
    after = _perf([Hit("tom_mid", 1.0, 80)])
    gone = missing_hits(before, after, SECTIONS, end_bar=5, subdivision=3)
    assert [g.bar for g in gone] == [2]


def test_the_reason_names_the_variant_it_went_missing_from():
    before = _perf([Hit("crash", 0.0, 105)])
    after = _perf([])
    gone = missing_hits(before, after, SECTIONS, end_bar=5, subdivision=3,
                        reason="removed by consolidate")
    assert gone[0].reason == "removed by consolidate"


def test_a_hit_before_the_first_section_still_gets_reported():
    """A section list says where each part *starts*, so the only unsectioned
    music is whatever comes before the first one."""
    before = _perf([Hit("crash", 0.0, 100)])
    after = _perf([])
    gone = missing_hits(before, after, [Section(name="verse-1", bar=3)],
                        end_bar=5, subdivision=3)
    assert len(gone) == 1 and gone[0].section == ""


def test_the_ledger_is_ordered_by_position_then_instrument():
    before = _perf([Hit("tom_mid", 5.0, 80), Hit("crash", 1.0, 100),
                    Hit("ride", 1.0, 90)])
    after = _perf([])
    gone = missing_hits(before, after, SECTIONS, end_bar=5, subdivision=3)
    assert [(g.bar, g.instrument) for g in gone] == [
        (1, "crash"), (1, "ride"), (2, "tom_mid")]


def test_hits_the_vote_added_are_not_in_the_ledger():
    """The vote fills bars that missed a hit, and that is it working. Only the
    losses go on a checklist."""
    before = _perf([Hit("kick", 0.0, 100)])
    after = _perf([Hit("kick", 0.0, 100), Hit("kick", 4.0, 100)])
    assert missing_hits(before, after, SECTIONS, end_bar=5, subdivision=3) == []


# ── section-boundary crashes ─────────────────────────────────────────────────


def test_an_open_hat_on_a_section_start_is_a_crash_candidate():
    performance = _perf([Hit("hihat_open", 8.0, 111), Hit("hihat_open", 8.333, 70)])
    found = crash_candidates(performance, [Section(name="chorus-1", bar=3)],
                             end_bar=5)
    assert len(found) == 1
    assert (found[0].instrument, found[0].bar, found[0].section) == (
        "crash", 3, "chorus-1")
    assert "open hi-hat" in found[0].reason
    assert found[0].velocity == 111


def test_a_crash_already_named_is_not_a_candidate():
    performance = _perf([Hit("crash", 8.0, 111)])
    assert crash_candidates(performance, [Section(name="c", bar=3)], end_bar=5) == []


def test_a_section_start_with_no_cymbal_at_all_is_still_worth_saying():
    """Six of Manlio's fifteen section starts are like this, and they are the
    breaks — where a drummer drops out rather than accents. Report it, do not
    decide it."""
    performance = _perf([Hit("kick", 8.0, 100)])
    found = crash_candidates(performance, [Section(name="break-1", bar=3)],
                             end_bar=5)
    assert len(found) == 1
    assert found[0].velocity == 0
    assert "no cymbal" in found[0].reason


def test_the_loudest_cymbal_in_the_window_is_the_candidate():
    performance = _perf([Hit("hihat_open", 7.9, 80), Hit("hihat_open", 8.05, 118)])
    found = crash_candidates(performance, [Section(name="c", bar=3)], end_bar=5)
    assert found[0].velocity == 118


def test_a_cymbal_past_the_window_does_not_count():
    """Paolo's Reaper 19.3 is two beats before break-1 — a crash *into* the
    change. Widening the window to catch it triples the candidates and starts
    eating the hi-hat part, so those stay on the manual list."""
    performance = _perf([Hit("hihat_open", 6.0, 118)])
    found = crash_candidates(performance, [Section(name="break-1", bar=3)],
                             end_bar=5)
    assert len(found) == 1 and found[0].velocity == 0


def test_a_quiet_cymbal_on_a_section_start_is_not_promoted():
    """A crash is an accent. A hat that happens to land on a boundary is not."""
    performance = _perf([Hit("hihat_open", 8.0, 60)])
    found = crash_candidates(performance, [Section(name="c", bar=3)], end_bar=5)
    assert found[0].velocity == 0, "reported as bare, not promoted"


def test_no_sections_means_no_candidates():
    performance = _perf([Hit("hihat_open", 8.0, 118)])
    assert crash_candidates(performance, [], end_bar=5) == []


# ── the checklist ────────────────────────────────────────────────────────────


def test_the_checklist_numbers_bars_the_way_reaper_does():
    from rambass.restore import checklist

    gone = [Hit("crash", 0.0, 105)]
    items = missing_hits(_perf(gone), _perf([]), SECTIONS, end_bar=5,
                         subdivision=3)
    text = checklist("Manlio", items, count_in_bars=2)
    assert "3.1" in text, "musical bar 1 is Reaper bar 3 with a 2-bar count-in"
    assert "- [ ]" in text
    assert "crash" in text


def test_the_checklist_says_so_when_there_is_nothing_to_do():
    from rambass.restore import checklist

    text = checklist("Manlio", [], count_in_bars=2)
    assert "nothing" in text.lower()


def test_an_item_already_covered_by_an_addition_is_ticked():
    from rambass.restore import Addition, checklist

    items = missing_hits(_perf([Hit("crash", 0.0, 105)]), _perf([]), SECTIONS,
                         end_bar=5, subdivision=3)
    text = checklist("Manlio", items, count_in_bars=2,
                     additions=[Addition(bar=1, beat=1.0, instrument="crash")])
    assert "- [x]" in text
    assert "- [ ]" not in text


# ── a checklist has to be short enough to tick off ───────────────────────────
#
# The first run on Manlio produced 204 items, and about 170 of them were single
# hi-hats the vote had regularised — which is the vote working, not a loss worth
# recording. Buried in there were the 6 crashes and 25 toms that a listener
# actually notices. A list that long is not a checklist, it is the same problem
# it was meant to solve.


def test_the_groove_instruments_are_not_on_the_list_by_default():
    """A hat the vote dropped is the vote doing its job. A crash is not."""
    before = _perf([Hit("hihat_closed", 0.0, 70), Hit("kick", 0.0, 100),
                    Hit("snare", 1.0, 110), Hit("sidestick", 3.0, 45),
                    Hit("crash", 0.0, 105), Hit("tom_mid", 2.0, 88)])
    after = _perf([])
    gone = missing_hits(before, after, SECTIONS, end_bar=5, subdivision=3)
    assert [g.instrument for g in gone] == ["crash", "tom_mid"]


def test_everything_can_still_be_asked_for():
    before = _perf([Hit("hihat_closed", 0.0, 70), Hit("crash", 0.0, 105)])
    after = _perf([])
    gone = missing_hits(before, after, SECTIONS, end_bar=5, subdivision=3,
                        instruments=None)
    assert len(gone) == 2


def test_a_fill_is_one_line_not_eight():
    from rambass.restore import group_missing

    before = _perf([Hit("tom_mid", 4.0 + i / 3.0, 80) for i in range(6)])
    after = _perf([])
    gone = missing_hits(before, after, SECTIONS, end_bar=5, subdivision=3)
    grouped = group_missing(gone)
    assert len(grouped) == 1
    assert grouped[0].count == 6
    assert grouped[0].bar == 2 and grouped[0].instrument == "tom_mid"


def test_grouping_keeps_the_bar_and_the_instrument_apart():
    from rambass.restore import group_missing

    before = _perf([Hit("tom_mid", 4.0, 80), Hit("tom_mid", 8.0, 80),
                    Hit("crash", 4.0, 105)])
    after = _perf([])
    grouped = group_missing(missing_hits(before, after, SECTIONS, end_bar=5,
                                        subdivision=3))
    assert [(g.bar, g.instrument, g.count) for g in grouped] == [
        (2, "crash", 1), (2, "tom_mid", 1), (3, "tom_mid", 1)]


def test_a_group_reports_the_earliest_beat_it_covers():
    from rambass.restore import group_missing

    before = _perf([Hit("tom_mid", 5.0, 80), Hit("tom_mid", 4.333, 80)])
    after = _perf([])
    grouped = group_missing(missing_hits(before, after, SECTIONS, end_bar=5,
                                        subdivision=3))
    assert grouped[0].beat == pytest.approx(1.333, abs=1e-3)
