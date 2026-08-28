"""Stage 6 of docs/drums-rebuild.md: consolidating a section to one pattern."""

import pytest

from rambass.midiio import DrumPerformance, Hit
from rambass.quantize import ConsolidateSettings, SectionSpan, consolidate
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


# ── the stop rule ────────────────────────────────────────────────────────
# consolidate owns the fill problem in its docstring: a fill is the bar that
# does not repeat, so the vote deletes it. The mirror case is this one — the
# voted pattern being stamped into the last repetition, where the band has
# already lifted off into the break. See ConsolidateSettings.stop_beats.

def test_a_bar_that_stopped_is_not_stamped_over():
    """Four bars of the pattern and a fifth that stops after beat 1."""
    hits = backbeat(1, 4) + [Hit("kick", TIMELINE.bar_beat_to_seconds(5, 1.0), 100)]
    out, report = consolidate(DrumPerformance(hits, TIMELINE), [("verse", 1, 6)])

    after = [h for h in out.hits if h.time > TIMELINE.bar_beat_to_seconds(5, 1.0) + 1e-9]
    assert not after, "bar 5 stopped on beat 1 and must not be stamped past it"
    # And the kick that was actually played there is still there.
    assert [h.instrument for h in out.hits
            if abs(h.time - TIMELINE.bar_beat_to_seconds(5, 1.0)) < 1e-9] == ["kick"]

    entry = report["sections"][0]
    assert entry["stopped"] == [{"bar": 5, "beat": 1.0, "withheld": 3}]


def test_the_bars_before_the_one_that_stopped_are_untouched():
    hits = backbeat(1, 4) + [Hit("kick", TIMELINE.bar_beat_to_seconds(5, 1.0), 100)]
    out, _ = consolidate(DrumPerformance(hits, TIMELINE), [("verse", 1, 6)])
    for b in (1, 2, 3, 4):
        played = sorted((round(h.time - bar(b), 3), h.instrument)
                        for h in out.hits
                        if bar(b) - 1e-9 <= h.time < bar(b + 1) - 1e-9)
        assert played == [(0.0, "kick"), (0.5, "snare"), (1.0, "kick"), (1.5, "snare")]


def dense(first_bar, bars, *, velocity=100):
    """Kick on 1 and 3, snare on 2 and 4, hi-hat on all eight 8ths.

    A *dense* pattern, because that is what ``stop_beats`` was calibrated
    against — Manlio runs continuous triplets, twelve hat slots to the bar. On a
    sparse pattern one missing hit at the end of a bar already leaves two beats
    of silence; see
    ``test_the_constant_assumes_a_dense_grid_and_a_sparse_one_reads_as_a_stop``.
    """
    out = backbeat(first_bar, bars, velocity=velocity)
    for b in range(first_bar, first_bar + bars):
        for eighth in (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5):
            out.append(Hit("hihat_closed",
                           TIMELINE.bar_beat_to_seconds(b, eighth), velocity))
    return out


def test_a_one_slot_gap_at_the_end_of_a_bar_is_still_filled():
    """The ordinary case the vote exists for: 1.25 beats is not a dropout.

    Bar 4 loses only its last 8th, so its playing stops 1 beat before the bar
    ends — under the threshold, so the vote still puts the hat back. This is the
    sweep's "real killed" column: everything below 1.25 deletes this hit.
    """
    dropped_at = TIMELINE.bar_beat_to_seconds(4, 4.5)
    hits = [h for h in dense(1, 4)
            if not (h.instrument == "hihat_closed" and abs(h.time - dropped_at) < 1e-9)]
    out, report = consolidate(DrumPerformance(hits, TIMELINE), [("verse", 1, 5)])
    assert [h.instrument for h in out.hits if abs(h.time - dropped_at) < 1e-9] \
        == ["hihat_closed"]
    assert not report["sections"][0].get("stopped")


def test_the_threshold_is_between_one_beat_and_one_and_a_quarter():
    """The sweep's boundary, as executable evidence.

    A bar whose playing stops exactly 1 beat early is an ordinary gap the vote
    fills, and the default 1.25 is deliberately above that. Drop it to 1.0 and
    the same bar reads as a stop — which on Manlio is 17 real hits lost for the
    same 10 phantoms.
    """
    at = TIMELINE.bar_beat_to_seconds(4, 4.5)
    hits = [h for h in dense(1, 4)
            if not (h.instrument == "hihat_closed" and abs(h.time - at) < 1e-9)]
    performance = DrumPerformance(hits, TIMELINE)
    default, _ = consolidate(performance, [("verse", 1, 5)])
    eager, report = consolidate(performance, [("verse", 1, 5)],
                                settings=ConsolidateSettings(stop_beats=1.0))
    assert [h.instrument for h in default.hits if abs(h.time - at) < 1e-9] \
        == ["hihat_closed"]
    assert not [h for h in eager.hits if abs(h.time - at) < 1e-9]
    assert report["sections"][0]["stopped"] == [{"bar": 4, "beat": 4.0, "withheld": 1}]


def test_the_constant_assumes_a_dense_grid_and_a_sparse_one_reads_as_a_stop():
    """A known limit of 1.25 beats, written down rather than discovered later.

    The constant is calibrated on Manlio, which plays twelve hat slots to the
    bar, so a beat and a quarter of silence really is the band stopping. On a
    *sparse* pattern — hits only on the four beats — a bar that merely loses its
    beat-4 hit has two beats of silence after beat 3, and the rule reads that as
    a stop and withholds the very hit the vote would have restored.

    That is not a bug to fix with a smaller number (the sweep says below a beat
    is worse on real material) and not a reason to make the rule
    density-relative unmeasured. It is the boundary of where the constant
    applies: ``stop_beats=0`` is the escape hatch for a song that plays like
    this.
    """
    at = TIMELINE.bar_beat_to_seconds(4, 4.0)
    hits = [h for h in backbeat(1, 4)
            if not (h.instrument == "snare" and abs(h.time - at) < 1e-9)]
    performance = DrumPerformance(hits, TIMELINE)
    out, report = consolidate(performance, [("verse", 1, 5)])
    assert not [h for h in out.hits if abs(h.time - at) < 1e-9]
    assert report["sections"][0]["stopped"] == [{"bar": 4, "beat": 3.0, "withheld": 1}]
    off, _ = consolidate(performance, [("verse", 1, 5)],
                         settings=ConsolidateSettings(stop_beats=0.0))
    assert [h.instrument for h in off.hits if abs(h.time - at) < 1e-9] == ["snare"]


def test_a_bar_with_no_detections_at_all_is_still_stamped():
    """An empty bar is the vote's own job, not a stop.

    "A drummer who stopped is information" needs a stop to point at. A bar the
    transcriber found nothing in is the case Stage 6 exists for — the other
    repetitions outvote it — and withholding the pattern there would delete a
    whole bar of the part on the strength of a detection failure.
    """
    hits = backbeat(1, 3) + backbeat(5, 2)
    out, report = consolidate(DrumPerformance(hits, TIMELINE), [("verse", 1, 8)])
    assert len([h for h in out.hits
                if bar(4) - 1e-9 <= h.time < bar(5) - 1e-9]) == 4
    assert not report["sections"][0].get("stopped")


def test_stop_beats_zero_restores_the_old_behaviour():
    """The escape hatch, and it must be exact."""
    hits = backbeat(1, 4) + [Hit("kick", TIMELINE.bar_beat_to_seconds(5, 1.0), 100)]
    performance = DrumPerformance(hits, TIMELINE)
    off, report = consolidate(performance, [("verse", 1, 6)],
                              settings=ConsolidateSettings(stop_beats=0.0))
    assert len([h for h in off.hits if bar(5) - 1e-9 <= h.time < bar(6) - 1e-9]) == 4
    assert not report["sections"][0].get("stopped")


def test_a_section_too_short_to_vote_on_withholds_nothing():
    """Nothing was stamped, so there is nothing to withhold."""
    hits = backbeat(1, 2)
    hits = [h for h in hits if h.time < TIMELINE.bar_beat_to_seconds(2, 2.0)]
    out, report = consolidate(DrumPerformance(hits, TIMELINE), [("break", 1, 3)])
    assert "skipped" in report["sections"][0]
    assert not report["sections"][0].get("stopped")
    assert len(out.hits) == len(hits)


def test_the_stop_is_measured_inside_a_section_that_starts_mid_bar():
    """A bar shared by two sections stops where *this* section's slots end.

    Manlio's chorus-1 starts at bar 32 beat 3, so verse-2-lift's last bar is
    two beats long. Measuring the stop against the bar line instead of the
    section's own end would ask whether the band stopped before beat 5 of a
    window that finishes at beat 3.
    """
    hits = []
    for b in (1, 2, 3, 4):
        for beat, name in ((1.0, "kick"), (2.0, "snare")):
            hits.append(Hit(name, TIMELINE.bar_beat_to_seconds(b, beat), 100))
    # Bar 5's window is beats 1-3, and the band plays only beat 1 of it.
    hits.append(Hit("kick", TIMELINE.bar_beat_to_seconds(5, 1.0), 100))
    out, report = consolidate(
        DrumPerformance(hits, TIMELINE),
        [SectionSpan("verse", 1, 5, start_beat=1.0, end_beat=3.0)])
    assert report["sections"][0]["stopped"] == [{"bar": 5, "beat": 1.0, "withheld": 1}]
    assert not [h for h in out.hits
                if abs(h.time - TIMELINE.bar_beat_to_seconds(5, 2.0)) < 1e-9]


# ── the phantom-snare gate ───────────────────────────────────────────────
# min_repeats: 4 means a 2-bar break is passed through untouched, so every
# detection artefact in it survives. See ConsolidateSettings.phantom_snare_velocity.

def test_a_floor_snare_in_a_section_too_short_to_vote_on_is_dropped():
    """And a floor hi-hat and a floor side-stick in the same bars are not."""
    hits = [
        Hit("snare", TIMELINE.bar_beat_to_seconds(1, 2.0), 45),
        Hit("hihat_closed", TIMELINE.bar_beat_to_seconds(1, 3.0), 45),
        Hit("sidestick", TIMELINE.bar_beat_to_seconds(2, 2.0), 45),
        Hit("kick", TIMELINE.bar_beat_to_seconds(2, 1.0), 45),
    ]
    out, report = consolidate(DrumPerformance(hits, TIMELINE), [("break", 1, 3)])
    assert "skipped" in report["sections"][0]
    assert sorted(h.instrument for h in out.hits) == [
        "hihat_closed", "kick", "sidestick"]
    assert report["sections"][0]["phantom_snares"] == 1


def test_a_loud_snare_in_a_section_too_short_to_vote_on_survives():
    hits = [Hit("snare", TIMELINE.bar_beat_to_seconds(1, 2.0), 108)]
    out, report = consolidate(DrumPerformance(hits, TIMELINE), [("break", 1, 3)])
    assert [h.instrument for h in out.hits] == ["snare"]
    assert not report["sections"][0].get("phantom_snares")


def test_a_floor_snare_inside_a_voted_section_survives():
    """A voted section has a better instrument than level, namely agreement.

    The gate applies only where consolidate reported ``skipped``. Second-guessing
    the vote on loudness is exactly the mistake ``suppress_cross_stem_bleed``'s
    exclude list exists to prevent, pointed the other way.
    """
    hits = backbeat(1, 4)
    quiet = TIMELINE.bar_beat_to_seconds(2, 2.0)
    hits = [h.with_velocity(45) if abs(h.time - quiet) < 1e-9 and h.instrument == "snare"
            else h for h in hits]
    out, report = consolidate(DrumPerformance(hits, TIMELINE), [("verse", 1, 5)])
    assert len([h for h in out.hits if h.instrument == "snare"]) == 8
    assert not report["sections"][0].get("phantom_snares")


def test_the_gate_is_the_snare_alone_and_that_is_the_finding():
    """Generalising it to every instrument is the obvious "simplification".

    On Manlio it costs 15 real hits; here it costs the hi-hat, the kick and the
    side-stick that genuinely play at the floor. The asymmetry *is* the finding:
    a snare is the one instrument whose own quietness is evidence against it,
    because this drummer's soft strokes on that drum are side-sticks and ghost
    notes that land in other lanes.
    """
    from rambass import quantize

    hits = [
        Hit("snare", TIMELINE.bar_beat_to_seconds(1, 2.0), 45),
        Hit("hihat_closed", TIMELINE.bar_beat_to_seconds(1, 3.0), 45),
        Hit("sidestick", TIMELINE.bar_beat_to_seconds(2, 2.0), 45),
    ]
    assert quantize.PHANTOM_FLOOR_INSTRUMENTS == ("snare",)
    out, _ = consolidate(DrumPerformance(hits, TIMELINE), [("break", 1, 3)])
    assert sorted(h.instrument for h in out.hits) == ["hihat_closed", "sidestick"]


def test_zero_disables_the_gate_and_is_not_velocity_zero():
    """Same convention as ``drums.backbeat_velocity``."""
    hits = [Hit("snare", TIMELINE.bar_beat_to_seconds(1, 2.0), 45)]
    out, report = consolidate(
        DrumPerformance(hits, TIMELINE), [("break", 1, 3)],
        settings=ConsolidateSettings(phantom_snare_velocity=0))
    assert [h.instrument for h in out.hits] == ["snare"]
    assert not report["sections"][0].get("phantom_snares")


def test_a_dropped_floor_snare_is_not_counted_as_untouched():
    """`untouched` means "passed through", so a dropped hit is not one."""
    hits = [
        Hit("snare", TIMELINE.bar_beat_to_seconds(1, 2.0), 45),
        Hit("kick", TIMELINE.bar_beat_to_seconds(1, 1.0), 100),
    ]
    out, report = consolidate(DrumPerformance(hits, TIMELINE), [("break", 1, 3)])
    assert report["hits_after"] == 1 == len(out.hits)
    assert report["untouched"] == 1
