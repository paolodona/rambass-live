"""MIDI round-trips and the cleanup operations."""

from __future__ import annotations

import pytest

from rambass.drummap import GENERAL_MIDI, load_drum_map
from rambass.midiio import DrumPerformance, Hit, read_drum_midi, write_drum_midi
from rambass.quantize import (
    QuantizeSettings,
    deflam,
    humanize,
    quantize,
    shape_velocities,
    trim_to_bars,
)
from rambass.timeline import TempoChange, Timeline


def _performance(timeline: Timeline | None = None) -> DrumPerformance:
    timeline = timeline or Timeline(bpm=120, count_in_bars=2)
    hits = [
        Hit("kick", 0.0, 110),
        Hit("hihat_closed", 0.5, 80),
        Hit("snare", 1.0, 100),
        Hit("kick", 2.0, 108),
        Hit("crash", 4.0, 120),
    ]
    return DrumPerformance(hits, timeline, "Drums")


# ── midiio ───────────────────────────────────────────────────────────────
def test_midi_round_trip_preserves_times_and_velocities(tmp_path):
    original = _performance()
    path = write_drum_midi(tmp_path / "d.mid", original)
    back = read_drum_midi(path)
    assert back.timeline.bpm == pytest.approx(120)
    assert len(back.hits) == len(original.hits)
    for before, after in zip(original.sorted_hits(), back.sorted_hits(), strict=True):
        assert after.instrument == before.instrument
        assert after.time == pytest.approx(before.time, abs=1e-3)
        assert after.velocity == before.velocity


def test_midi_round_trip_survives_a_tempo_change(tmp_path):
    timeline = Timeline(bpm=120, changes=[TempoChange(bar=3, bpm=90)])
    performance = DrumPerformance(
        [Hit("kick", 0.0), Hit("snare", 4.0), Hit("kick", 8.0)], timeline
    )
    back = read_drum_midi(write_drum_midi(tmp_path / "t.mid", performance))
    assert back.timeline.bpm == pytest.approx(120)
    assert back.timeline.bpm_at(3) == pytest.approx(90)
    assert [round(h.time, 3) for h in back.sorted_hits()] == [0.0, 4.0, 8.0]


def test_writing_uses_the_drum_map_channel_and_notes(tmp_path, project):
    electro = load_drum_map("electro", project)
    path = write_drum_midi(tmp_path / "e.mid", _performance(), electro)
    import mido

    messages = [m for track in mido.MidiFile(path).tracks for m in track
                if m.type == "note_on"]
    assert {m.channel for m in messages} == {9}          # channel 10, zero-based
    assert 60 in {m.note for m in messages}              # electro kick
    # hihat_closed is a null in that map, so it falls back to General MIDI
    assert GENERAL_MIDI.note_for("hihat_closed") in {m.note for m in messages}


def test_reading_can_filter_by_channel(tmp_path):
    import mido

    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.Message("note_on", note=36, velocity=100, channel=9, time=0))
    track.append(mido.Message("note_on", note=38, velocity=100, channel=0, time=480))
    midi.save(str(tmp_path / "mixed.mid"))

    assert len(read_drum_midi(tmp_path / "mixed.mid").hits) == 2
    assert len(read_drum_midi(tmp_path / "mixed.mid", channel=10).hits) == 1


def test_performance_counts_are_sorted_by_frequency():
    counts = _performance().count()
    assert list(counts)[0] == "kick"
    assert counts["kick"] == 2


# ── de-flam ──────────────────────────────────────────────────────────────
def test_deflam_collapses_a_double_trigger():
    timeline = Timeline(bpm=120)
    performance = DrumPerformance(
        [Hit("kick", 1.000, 60), Hit("kick", 1.014, 110), Hit("snare", 1.010, 90)],
        timeline,
    )
    cleaned, removed = deflam(performance, 25.0)
    assert removed == 1
    assert len(cleaned.hits) == 2
    kick = next(h for h in cleaned.hits if h.instrument == "kick")
    # earliest attack, loudest velocity
    assert kick.time == pytest.approx(1.000)
    assert kick.velocity == 110


def test_deflam_keeps_hits_outside_the_window():
    performance = DrumPerformance(
        [Hit("kick", 1.0), Hit("kick", 1.2)], Timeline(bpm=120)
    )
    cleaned, removed = deflam(performance, 25.0)
    assert removed == 0 and len(cleaned.hits) == 2


def test_deflam_first_mode():
    performance = DrumPerformance(
        [Hit("snare", 1.0, 50), Hit("snare", 1.01, 120)], Timeline(bpm=120)
    )
    cleaned, _ = deflam(performance, 25.0, keep="first")
    assert cleaned.hits[0].velocity == 50


# ── quantise ─────────────────────────────────────────────────────────────
def test_quantise_snaps_to_the_sixteenth_grid():
    timeline = Timeline(bpm=120)          # 16th = 0.125 s
    performance = DrumPerformance(
        [Hit("kick", 0.012), Hit("hihat_closed", 0.140), Hit("snare", 0.493)],
        timeline,
    )
    cleaned, report = quantize(performance, QuantizeSettings(subdivision=4))
    assert [round(h.time, 4) for h in cleaned.sorted_hits()] == [0.0, 0.125, 0.5]
    assert report["left_alone"] == 0
    assert report["largest_shift_ms"] == pytest.approx(15.0, abs=1.0)


def test_quantise_strength_interpolates():
    performance = DrumPerformance([Hit("kick", 0.100)], Timeline(bpm=120))
    # nearest 16th to 0.100 is 0.125, so half strength lands halfway there
    half = quantize(performance, QuantizeSettings(strength=0.5))[0]
    assert half.hits[0].time == pytest.approx(0.1125)


def test_quantise_leaves_far_hits_alone_unless_forced():
    timeline = Timeline(bpm=120)
    # exactly between two 16th lines: half a step away, beyond the 0.35 tolerance
    performance = DrumPerformance([Hit("kick", 0.0625)], timeline)
    gentle, report = quantize(performance, QuantizeSettings(subdivision=4))
    assert report["left_alone"] == 1
    assert gentle.hits[0].time == pytest.approx(0.0625)

    forced, report = quantize(performance, QuantizeSettings(subdivision=4, force=True))
    assert report["left_alone"] == 0
    assert forced.hits[0].time in (pytest.approx(0.0), pytest.approx(0.125))


def test_far_hits_are_named_for_review_not_just_counted():
    """Codex's review (Aug 2026): "unknown hits should be flagged for review,
    not carried through unchanged." Leaving a distant hit physically alone was
    already right -- forcing it onto the grid is how a deliberate push gets
    ironed flat -- but leaving it *unlisted* was not: nothing above this
    function ever saw which hit, or how far off, so a transcription error and
    an intentional swing sat in the same silent bucket as a plain count.
    """
    timeline = Timeline(bpm=120)  # 16th = 0.125 s, so 0.0625 s is half a step
    performance = DrumPerformance([Hit("kick", 0.0625, 77)], timeline)
    _, report = quantize(performance, QuantizeSettings(subdivision=4))
    assert len(report["unresolved"]) == 1
    item = report["unresolved"][0]
    assert (item.bar, round(item.beat, 3)) == (1, 1.125)
    assert item.instrument == "kick"
    assert item.velocity == 77
    assert item.distance_ms == pytest.approx(62.5, abs=0.5)
    assert item.tolerance_ms == pytest.approx(43.75, abs=0.5)

    # forcing the grid resolves it -- there is nothing left to flag
    _, forced_report = quantize(
        performance, QuantizeSettings(subdivision=4, force=True)
    )
    assert forced_report["unresolved"] == []


def test_cymbals_default_to_a_coarser_grid():
    """A crash is quantised to 8ths, so it is not dragged onto a 16th line."""
    timeline = Timeline(bpm=120)          # 8th = 0.25 s, 16th = 0.125 s
    performance = DrumPerformance(
        [Hit("crash", 0.140), Hit("snare", 0.140)], timeline
    )
    cleaned, report = quantize(performance, QuantizeSettings(subdivision=4))
    by_instrument = {h.instrument: h.time for h in cleaned.hits}
    # the snare snaps onto the nearby 16th line
    assert by_instrument["snare"] == pytest.approx(0.125)
    # the crash's own grid is 8ths, and 0.140 is too far from either 8th line,
    # so it is left where the drummer put it rather than yanked to 0.0 or 0.25
    assert by_instrument["crash"] == pytest.approx(0.140)
    assert report["left_alone"] == 1


def test_swing_pushes_odd_subdivisions_late():
    timeline = Timeline(bpm=120)
    performance = DrumPerformance([Hit("snare", 0.30)], timeline)
    straight, _ = quantize(performance, QuantizeSettings(subdivision=2, per_instrument={}))
    swung, _ = quantize(
        performance, QuantizeSettings(subdivision=2, swing=0.33, per_instrument={})
    )
    assert straight.hits[0].time == pytest.approx(0.25)
    assert swung.hits[0].time > straight.hits[0].time


def test_quantise_rejects_impossible_settings():
    performance = DrumPerformance([Hit("kick", 0.0)], Timeline(bpm=120))
    with pytest.raises(ValueError):
        quantize(performance, QuantizeSettings(strength=2.0))
    with pytest.raises(ValueError):
        quantize(performance, QuantizeSettings(swing=1.0))


# ── velocities, humanise, trim ───────────────────────────────────────────
def test_shape_velocities_sets_and_accents():
    timeline = Timeline(bpm=120)
    performance = DrumPerformance(
        [Hit("kick", 0.0, 40), Hit("snare", 1.0, 40), Hit("kick", 2.0, 40)],
        timeline,
    )
    shaped = shape_velocities(
        performance, accents={"kick": 100, "snare": 90}, downbeat_boost=10
    )
    by_time = {round(h.time, 2): h.velocity for h in shaped.sorted_hits()}
    assert by_time[0.0] == 110        # kick on bar 1 beat 1 gets the boost
    assert by_time[1.0] == 90         # snare on beat 3, no boost
    assert by_time[2.0] == 110        # bar 2 beat 1


def test_shape_velocities_clamps():
    performance = DrumPerformance([Hit("crash", 0.0, 120)], Timeline(bpm=120))
    assert shape_velocities(performance, downbeat_boost=50).hits[0].velocity == 127


def test_humanize_is_seeded_and_spares_the_kick():
    performance = DrumPerformance(
        [Hit("kick", 0.0), Hit("snare", 1.0)], Timeline(bpm=120)
    )
    first = humanize(performance, timing_ms=10, seed=3)
    second = humanize(performance, timing_ms=10, seed=3)
    assert [h.time for h in first.sorted_hits()] == [h.time for h in second.sorted_hits()]
    assert next(h for h in first.hits if h.instrument == "kick").time == 0.0
    assert next(h for h in first.hits if h.instrument == "snare").time != 1.0


def test_trim_to_bars():
    timeline = Timeline(bpm=120)
    performance = DrumPerformance(
        [Hit("kick", -1.0), Hit("kick", 0.0), Hit("kick", 2.0), Hit("kick", 10.0)],
        timeline,
    )
    assert len(trim_to_bars(performance, 1, 2).hits) == 2
    assert len(trim_to_bars(performance, 1).hits) == 3
