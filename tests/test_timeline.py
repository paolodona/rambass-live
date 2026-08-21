"""The bar/seconds arithmetic everything else depends on."""

from __future__ import annotations

import pytest

from rambass.timeline import TempoChange, Timeline, TimelineError, parse_time_signature


def test_parse_time_signature():
    assert parse_time_signature("7/8") == (7, 8)
    assert parse_time_signature((3, 4)) == (3, 4)
    with pytest.raises(TimelineError):
        parse_time_signature("4/5")
    with pytest.raises(TimelineError):
        parse_time_signature("nonsense")


def test_constant_tempo_bar_positions():
    timeline = Timeline(bpm=120, time_signature="4/4")
    assert timeline.bar_beat_to_seconds(1) == 0.0
    assert timeline.bar_beat_to_seconds(2) == pytest.approx(2.0)
    assert timeline.bar_beat_to_seconds(1, 3) == pytest.approx(1.0)
    assert timeline.bar_length_seconds(1) == pytest.approx(2.0)


def test_bpm_is_always_quarter_note_based():
    """A 6/8 bar at 120 BPM is six eighths of 0.25 s, not six of 0.5 s."""
    timeline = Timeline(bpm=120, time_signature="6/8")
    assert timeline.bar_length_seconds(1) == pytest.approx(1.5)
    assert timeline.bar_beat_to_seconds(1, 2) == pytest.approx(0.25)


def test_count_in_is_negative_musical_time():
    timeline = Timeline(bpm=120, time_signature="4/4", count_in_bars=2)
    assert timeline.count_in_seconds == pytest.approx(4.0)
    assert timeline.bar_beat_to_seconds(-1) == pytest.approx(-4.0)
    # audio_time shifts the musical clock onto the rendered file's clock
    assert timeline.audio_time(1) == pytest.approx(4.0)
    assert timeline.audio_time(2) == pytest.approx(6.0)


def test_tempo_change_shifts_later_bars():
    timeline = Timeline(bpm=120, changes=[TempoChange(bar=5, bpm=60)])
    assert timeline.bar_beat_to_seconds(5) == pytest.approx(8.0)
    assert timeline.bar_beat_to_seconds(6) == pytest.approx(12.0)
    assert timeline.bpm_at(4) == 120
    assert timeline.bpm_at(5) == 60


def test_tempo_change_at_bar_one_redefines_the_opening():
    timeline = Timeline(bpm=120, changes=[TempoChange(bar=1, bpm=140)])
    assert timeline.bpm_at(1) == 140
    assert timeline.bar_beat_to_seconds(2) == pytest.approx(4 * 60 / 140)


def test_time_signature_change():
    timeline = Timeline(bpm=120, changes=[TempoChange(bar=3, time_signature=(3, 4))])
    assert timeline.time_signature_at(3) == (3, 4)
    assert timeline.bar_length_seconds(3) == pytest.approx(1.5)
    assert timeline.bpm_at(3) == 120        # tempo carried over


def test_seconds_to_bar_beat_round_trips():
    timeline = Timeline(bpm=137, time_signature="4/4")
    for bar, beat in ((1, 1.0), (7, 3.0), (64, 2.5)):
        seconds = timeline.bar_beat_to_seconds(bar, beat)
        assert timeline.seconds_to_bar_beat(seconds) == pytest.approx((bar, beat))


def test_quarter_conversions_round_trip_across_a_tempo_change():
    timeline = Timeline(bpm=120, changes=[TempoChange(bar=5, bpm=60)])
    for seconds in (0.0, 3.3, 8.0, 13.7, 40.0):
        quarters = timeline.seconds_to_quarters(seconds)
        assert timeline.quarters_to_seconds(quarters) == pytest.approx(seconds)


def test_total_quarters_counts_both_tempo_sections():
    timeline = Timeline(bpm=120, changes=[TempoChange(bar=5, bpm=60)])
    assert timeline.total_quarters(8) == pytest.approx(32.0)


def test_beats_between_respects_metre():
    timeline = Timeline(bpm=90, time_signature="7/8", count_in_bars=1)
    beats = timeline.beats_between(0, 5)      # count-in bar plus bars 1-4
    assert len(beats) == 5 * 7


def test_grid_seconds_counts_subdivisions():
    timeline = Timeline(bpm=120, time_signature="4/4")
    assert len(timeline.grid_seconds(4, 1, 3)) == 32        # 16ths, two bars
    assert timeline.grid_seconds(4, 1, 2)[1] == pytest.approx(0.125)


def test_rejects_nonsense():
    with pytest.raises(TimelineError):
        Timeline(bpm=0)
    with pytest.raises(TimelineError):
        Timeline(bpm=120, changes=[TempoChange(bar=0, bpm=100)])
    with pytest.raises(TimelineError):
        Timeline(bpm=120).bar_beat_to_seconds(1, 0.5)
