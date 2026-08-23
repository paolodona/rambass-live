"""Piecewise alignment: hearing the new drums against the original mix.

Paolo: *"I would like to rebuild the drums for the song manlio, end to end, until
I have them over the original time warped track (minus the original drums), so I
can hear them in context."*

A single offset is not enough and the numbers say so. `practice/align.yaml` on
Manlio records the honest limit already: measured against the drum stem, the
band's pulse drifts **-88 to +258 ms** across the song, so a reference laid at
one offset lines up at the start and flams by a quarter second by the end — which
reads as "the click does not line up with the mix" exactly when you are trying to
judge timing.

So the map is piecewise: a bar-to-seconds anchor every few bars, fitted from
detected beats, and the audio between two anchors is resampled so that the
recording's own duration for those bars becomes the grid's duration.

**Only the source side is stored in seconds.** CLAUDE.md is explicit: the target
side is computed from :class:`~rambass.timeline.Timeline` at build time, or a BPM
edit silently stops re-warping. The seconds here describe an immutable audio
file, which is the whole reason this is one of the two allowed exceptions to
"positions are in bars, never seconds".

All of this is pure numpy, so it is tested without librosa, ffmpeg or an audio
file — the beat detection that feeds it is `analyze`'s problem, not this one's.
"""

from __future__ import annotations

import numpy as np
import pytest

from rambass.align import (
    AlignMap,
    Anchor,
    fit_anchors,
    residual_ms,
    warp_plan,
    warp_samples,
)
from rambass.timeline import Timeline


# ── fitting anchors from detected beats ──────────────────────────────────────


def test_a_take_played_to_a_click_fits_one_offset_everywhere():
    timeline = Timeline(bpm=60.0)
    beats = [0.5 + i for i in range(32)]          # dead even, 500 ms late
    anchors = fit_anchors(beats, timeline, bars=8, every_beats=8)
    assert [a.bar for a in anchors] == [1, 3, 5, 7]
    assert all(a.at == pytest.approx(0.5 + 4.0 * (a.bar - 1)) for a in anchors)


def test_a_take_that_speeds_up_gets_anchors_that_follow_it():
    """The case that matters. Each bar is 3% shorter than the last, so by bar 8
    the recording is most of a beat ahead of the fixed grid."""
    timeline = Timeline(bpm=60.0)
    beats, at, step = [], 0.5, 1.0
    for _ in range(32):
        beats.append(at)
        at += step
        step *= 0.99
    anchors = fit_anchors(beats, timeline, bars=8, every_beats=4)
    assert [a.bar for a in anchors] == list(range(1, 9))
    gaps = [b.at - a.at for a, b in zip(anchors, anchors[1:])]
    assert all(later < earlier for earlier, later in zip(gaps, gaps[1:]))


def test_a_bar_with_no_beat_near_it_is_skipped_not_guessed():
    """A silent stretch has nothing to fit, and an invented anchor there warps
    audio nobody measured."""
    timeline = Timeline(bpm=60.0)
    beats = [float(i) for i in range(8)] + [float(i) for i in range(24, 32)]
    anchors = fit_anchors(beats, timeline, bars=8, every_beats=4)
    assert [a.bar for a in anchors] == [1, 2, 7, 8]


def test_the_first_anchor_is_always_bar_one():
    timeline = Timeline(bpm=60.0)
    anchors = fit_anchors([0.7 + i for i in range(32)], timeline, bars=8, every_beats=16)
    assert anchors[0].bar == 1


def test_no_beats_at_all_gives_no_anchors_rather_than_a_fake_map():
    assert fit_anchors([], Timeline(bpm=60.0), bars=8, every_beats=8) == []


def test_anchors_come_out_sorted_and_unique():
    timeline = Timeline(bpm=60.0)
    anchors = fit_anchors([0.5 + i for i in range(32)], timeline, bars=8, every_beats=8)
    bars = [a.bar for a in anchors]
    assert bars == sorted(set(bars))


# ── the residual: is the map good enough to be worth using ───────────────────


def test_a_perfect_map_has_no_residual():
    timeline = Timeline(bpm=60.0)
    beats = [0.5 + i for i in range(16)]
    amap = AlignMap(anchors=fit_anchors(beats, timeline, bars=4, every_beats=4))
    worst, mean = residual_ms(amap, beats, timeline)
    assert worst == pytest.approx(0.0, abs=1e-6)
    assert mean == pytest.approx(0.0, abs=1e-6)


def test_the_residual_reports_the_worst_beat_not_the_average():
    """practice-tracks.md sets 50 ms as the limit, and a mean hides the one bar
    that is 200 ms out — which is the bar you would hear."""
    timeline = Timeline(bpm=60.0)
    amap = AlignMap(anchors=[Anchor(bar=1, at=0.0), Anchor(bar=5, at=16.0)])
    beats = [0.0, 1.0, 2.2, 3.0]          # one beat 200 ms late
    worst, mean = residual_ms(amap, beats, timeline)
    assert worst == pytest.approx(200.0, abs=1.0)
    assert mean < worst


def test_a_map_with_one_anchor_is_an_offset_and_says_so():
    amap = AlignMap(anchors=[Anchor(bar=1, at=0.7)])
    assert amap.mode == "offset"
    assert AlignMap(anchors=[Anchor(bar=1, at=0.0),
                             Anchor(bar=3, at=8.1)]).mode == "piecewise"
    assert AlignMap(anchors=[]).mode == "none"


# ── the warp plan: source segment -> target segment ──────────────────────────


def test_each_segment_maps_its_own_recorded_span_onto_the_grid_span():
    timeline = Timeline(bpm=60.0)
    amap = AlignMap(anchors=[Anchor(bar=1, at=1.0), Anchor(bar=3, at=9.2),
                             Anchor(bar=5, at=17.0)])
    plan = warp_plan(amap, timeline, bars=8)
    assert [(round(s.source_start, 3), round(s.source_end, 3)) for s in plan[:2]] == [
        (1.0, 9.2), (9.2, 17.0)]
    assert [(round(s.target_start, 3), round(s.target_end, 3)) for s in plan[:2]] == [
        (0.0, 8.0), (8.0, 16.0)]
    assert plan[0].rate == pytest.approx(8.2 / 8.0)


def test_the_last_segment_runs_to_the_end_of_the_song():
    timeline = Timeline(bpm=60.0)
    amap = AlignMap(anchors=[Anchor(bar=1, at=0.0), Anchor(bar=5, at=16.0)])
    plan = warp_plan(amap, timeline, bars=8)
    assert plan[-1].target_end == pytest.approx(32.0)
    assert plan[-1].source_end == pytest.approx(32.0)


def test_a_map_with_no_anchors_has_no_plan():
    assert warp_plan(AlignMap(anchors=[]), Timeline(bpm=60.0), bars=8) == []


def test_a_single_anchor_is_a_straight_shift_with_rate_one():
    timeline = Timeline(bpm=60.0)
    plan = warp_plan(AlignMap(anchors=[Anchor(bar=1, at=1.2)]), timeline, bars=4)
    assert len(plan) == 1
    assert plan[0].rate == pytest.approx(1.0)
    assert plan[0].source_start == pytest.approx(1.2)


def test_an_absurd_rate_is_refused_rather_than_rendered():
    """A bad anchor produces a segment that has to stretch 3x, and rendering it
    makes an unlistenable file that looks like a tool bug rather than bad data."""
    from rambass.project import ProjectError

    timeline = Timeline(bpm=60.0)
    amap = AlignMap(anchors=[Anchor(bar=1, at=0.0), Anchor(bar=3, at=30.0)])
    with pytest.raises(ProjectError) as caught:
        warp_plan(amap, timeline, bars=4)
    assert "bar 1" in str(caught.value)


# ── the resampler ────────────────────────────────────────────────────────────


def test_warping_a_click_track_puts_the_clicks_on_the_grid():
    """End to end on synthetic audio: a take that drifts, warped flat."""
    sr = 8000
    beats, at, step = [], 0.25, 1.03
    for _ in range(16):
        beats.append(at)
        at += step
    samples = np.zeros(int(sr * (at + 2.0)), dtype=np.float32)
    for beat in beats:
        samples[int(beat * sr)] = 1.0

    timeline = Timeline(bpm=60.0)
    amap = AlignMap(anchors=fit_anchors(beats, timeline, bars=4, every_beats=4))
    out = warp_samples(samples, sr, warp_plan(amap, timeline, bars=4))

    found = [i / sr for i in np.flatnonzero(out > 0.2)]
    for bar in range(4):
        for beat in range(4):
            expected = bar * 4.0 + beat
            assert any(abs(f - expected) < 0.02 for f in found), expected


def test_the_output_is_exactly_as_long_as_the_grid_says():
    sr = 8000
    samples = np.zeros(sr * 20, dtype=np.float32)
    timeline = Timeline(bpm=60.0)
    amap = AlignMap(anchors=[Anchor(bar=1, at=1.0), Anchor(bar=5, at=17.5)])
    out = warp_samples(samples, sr, warp_plan(amap, timeline, bars=8))
    assert len(out) == pytest.approx(sr * 32, rel=0.001)


def test_warping_with_no_plan_returns_the_audio_untouched():
    samples = np.arange(100, dtype=np.float32)
    assert np.array_equal(warp_samples(samples, 8000, []), samples)


def test_a_segment_that_runs_past_the_end_of_the_file_is_padded_not_wrapped():
    sr = 8000
    samples = np.ones(sr * 4, dtype=np.float32)
    timeline = Timeline(bpm=60.0)
    amap = AlignMap(anchors=[Anchor(bar=1, at=0.0)])
    out = warp_samples(samples, sr, warp_plan(amap, timeline, bars=8))
    assert len(out) == pytest.approx(sr * 32, rel=0.001)
    assert out[-1] == pytest.approx(0.0)


# ── the file ─────────────────────────────────────────────────────────────────


def test_the_map_round_trips_through_align_yaml(tmp_path):
    from rambass.align import load_align, save_align

    amap = AlignMap(
        anchors=[Anchor(bar=1, at=0.692), Anchor(bar=5, at=16.9)],
        source="09 Manlio.wav", detected_bpm=60.0,
        residual_max_ms=41.0, residual_mean_ms=12.0)
    path = tmp_path / "align.yaml"
    save_align(path, amap)
    again = load_align(path)
    assert again.anchors == amap.anchors
    assert again.source == "09 Manlio.wav"
    assert again.residual_max_ms == pytest.approx(41.0)


def test_only_the_source_side_is_ever_written(tmp_path):
    """CLAUDE.md: store the source side only, or a BPM edit silently stops
    re-warping. So no target seconds, and no bar-to-bar mapping, in the file."""
    from rambass.align import save_align

    path = tmp_path / "align.yaml"
    save_align(path, AlignMap(anchors=[Anchor(bar=1, at=0.5), Anchor(bar=3, at=8.4)]))
    data = [line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")]
    assert any("at: 0.5" in line.replace("0.500", "0.5") for line in data)
    assert not any("target" in line for line in data)
    assert not any("bpm_at" in line or "beat" in line for line in data)


def test_a_missing_file_is_no_map_rather_than_an_error(tmp_path):
    from rambass.align import load_align

    assert load_align(tmp_path / "nope.yaml").mode == "none"


def test_the_old_offset_only_file_still_loads(tmp_path):
    """Manlio's existing align.yaml is `mode: offset` with one anchor, and it
    has a hand-checked value in it. Reading it must keep working."""
    from rambass.align import load_align

    path = tmp_path / "align.yaml"
    path.write_text(
        "source: \"09 Manlio.wav\"\ndetected_bpm: 60.0\nmode: offset\n"
        "anchors:\n  - {bar: 1, at: 0.692}\n",
        encoding="utf-8")
    amap = load_align(path)
    assert amap.mode == "offset"
    assert amap.anchors == [Anchor(bar=1, at=0.692)]
    assert amap.offset == pytest.approx(0.692)


# ── per beat beats per bar, measured ─────────────────────────────────────────


def _drifting_click(sr, bars, per_bar=4, jitter=0.06):
    """A take that drifts *and* swings: the backbeat sits late inside each bar."""
    beats, at, step = [], 0.3, 1.0
    for index in range(bars * per_bar):
        offset = jitter if index % 2 else 0.0     # beats 2 and 4 land late
        beats.append(at + offset)
        at += step
        step *= 0.995
    samples = np.zeros(int(sr * (at + 2.0)), dtype=np.float32)
    for beat in beats:
        samples[int(beat * sr)] = 1.0
    return beats, samples


def _worst_error_ms(out, sr, bars, per_bar=4):
    found = np.flatnonzero(out > 0.2) / sr
    worst = 0.0
    for index in range(bars * per_bar):
        expected = float(index)
        worst = max(worst, min(abs(found - expected)) * 1000.0)
    return worst


def test_an_anchor_on_every_beat_beats_an_anchor_on_every_bar():
    """The measurement that justified putting `beat` on Anchor. On Manlio, the
    drum stem warped and then asked how far each backbeat lands from its grid
    line: p90 107 ms for a single offset, 58 ms per bar, 38 ms per beat."""
    sr, bars = 8000, 8
    beats, samples = _drifting_click(sr, bars)
    timeline = Timeline(bpm=60.0)

    per_bar_map = AlignMap(anchors=fit_anchors(beats, timeline, bars=bars,
                                               every_beats=4))
    per_beat_map = AlignMap(anchors=fit_anchors(beats, timeline, bars=bars,
                                                every_beats=1))
    assert len(per_beat_map.anchors) > len(per_bar_map.anchors)

    coarse = _worst_error_ms(
        warp_samples(samples, sr, warp_plan(per_bar_map, timeline, bars=bars)),
        sr, bars)
    fine = _worst_error_ms(
        warp_samples(samples, sr, warp_plan(per_beat_map, timeline, bars=bars)),
        sr, bars)
    assert fine < coarse
    assert fine < 10.0, "an anchor on every beat should land every beat"


def test_an_anchor_can_sit_mid_bar_and_survive_the_file():
    from rambass.align import load_align, save_align
    import tempfile
    from pathlib import Path as _Path

    with tempfile.TemporaryDirectory() as tmp:
        path = _Path(tmp) / "align.yaml"
        save_align(path, AlignMap(anchors=[Anchor(bar=1, at=0.5),
                                           Anchor(bar=1, beat=3.0, at=2.6),
                                           Anchor(bar=2, at=4.55)]))
        again = load_align(path)
    assert [(a.bar, a.beat) for a in again.anchors] == [
        (1, 1.0), (1, 3.0), (2, 1.0)]


def test_the_plan_orders_mid_bar_anchors_correctly():
    timeline = Timeline(bpm=60.0)
    amap = AlignMap(anchors=[Anchor(bar=2, at=4.5), Anchor(bar=1, at=0.4),
                             Anchor(bar=1, beat=3.0, at=2.45)])
    plan = warp_plan(amap, timeline, bars=4)
    assert [round(s.target_start, 3) for s in plan] == [0.0, 2.0, 4.0]


# ── the residual has to be measured on something the fit did not see ─────────
#
# At one anchor per beat, `residual_ms` against the detected beats is circular:
# the map passes exactly through every one of them, so it reports 0 ms however
# bad the map is. Manlio's first per-beat fit said "residual 0 ms worst / 0 ms
# mean" for 307 anchors, which is true and tells you nothing.
#
# Leave-one-out does tell you something: drop each anchor in turn, ask the map
# where that beat should be, and compare. That measures the only thing a map can
# get wrong between anchors — how well it interpolates where it has none.


def test_leaving_an_anchor_out_measures_the_interpolation():
    from rambass.align import residual_holdout_ms

    timeline = Timeline(bpm=60.0)
    even = AlignMap(anchors=[Anchor(bar=b, beat=t, at=(b - 1) * 4.0 + t - 1 + 0.5)
                             for b in range(1, 5) for t in (1.0, 2.0, 3.0, 4.0)])
    worst, mean = residual_holdout_ms(even, timeline)
    assert worst == pytest.approx(0.0, abs=1e-6)
    assert mean == pytest.approx(0.0, abs=1e-6)


def test_a_beat_the_take_pushed_shows_up_in_the_holdout():
    from rambass.align import residual_holdout_ms

    timeline = Timeline(bpm=60.0)
    anchors = [Anchor(bar=1, beat=1.0, at=0.0), Anchor(bar=1, beat=2.0, at=1.2),
               Anchor(bar=1, beat=3.0, at=2.0), Anchor(bar=1, beat=4.0, at=3.0)]
    worst, mean = residual_holdout_ms(AlignMap(anchors=anchors), timeline)
    assert worst == pytest.approx(200.0, abs=1.0)     # beat 2 sat 200 ms late
    assert 0.0 < mean < worst


def test_the_holdout_needs_three_anchors_to_say_anything():
    from rambass.align import residual_holdout_ms

    timeline = Timeline(bpm=60.0)
    assert residual_holdout_ms(AlignMap(anchors=[Anchor(bar=1, at=0.5)]),
                               timeline) == (None, None)
    assert residual_holdout_ms(AlignMap(anchors=[]), timeline) == (None, None)


# ── and into the Reaper project ──────────────────────────────────────────────


def test_the_warped_reference_goes_on_the_grid_not_at_the_anchor(project, song):
    """The whole point of warping is that the file already *is* on the grid, so
    it starts at the count-in like the drums do. Shifting it by the anchor as
    well would undo the warp — which is a bug you would hear as a double offset
    and diagnose as "the warp does not work"."""
    from rambass.reaper import build_song_script

    (song.directory / "practice").mkdir(parents=True, exist_ok=True)
    aligned = song.directory / "practice" / "no_drums-aligned.wav"
    aligned.write_bytes(b"RIFF")
    text = build_song_script(song).render()
    line = [row for row in text.splitlines()
            if "no_drums-aligned" in row]
    assert line, "the warped reference should be placed when it exists"
    assert line[0].split("\t")[-1] == f"{song.timeline().count_in_seconds:g}"


def test_no_warped_file_means_an_empty_track_not_a_missing_one(project, song):
    """The track is always there so the project layout does not change shape
    depending on whether somebody has run `align --warp` yet."""
    from rambass.reaper import build_song_script

    rows = build_song_script(song).render().splitlines()
    assert any(row.startswith("TRACK\tREF aligned") for row in rows)
    assert not [row for row in rows
                if row.startswith("ITEM\tREF aligned")]


# ── a malformed align.yaml must not crash the CLI ────────────────────────────
#
# Found by a test that wrote `x` into the file as a placeholder: `yaml.safe_load`
# returns the *string* "x", and both readers went straight to `.get` on it —
# AttributeError, no message, from a command that had nothing to do with
# alignment. This file is documented as hand-correctable, so a half-finished edit
# is a completely ordinary state for it to be in.


@pytest.mark.parametrize("text", ["x", "[1, 2, 3]", "", "null", "- bar: 1"])
def test_a_file_that_is_not_a_mapping_reads_as_no_map(tmp_path, text):
    from rambass.align import load_align

    path = tmp_path / "align.yaml"
    path.write_text(text, encoding="utf-8")
    assert load_align(path).mode == "none"


@pytest.mark.parametrize("text", ["x", "anchors: nonsense", "anchors: [{}]",
                                  "anchors: [{bar: one, at: 2}]"])
def test_a_broken_file_gives_no_anchor_rather_than_a_traceback(project, song, text):
    align = song.path("practice", "align.yaml")
    align.parent.mkdir(parents=True, exist_ok=True)
    align.write_text(text, encoding="utf-8")
    assert song.align_anchor() is None


def test_a_good_file_still_reads(project, song):
    align = song.path("practice", "align.yaml")
    align.parent.mkdir(parents=True, exist_ok=True)
    align.write_text("anchors:\n  - {bar: 1, at: 0.692}\n", encoding="utf-8")
    assert song.align_anchor() == pytest.approx(0.692)


# ── a fit must not leave behind a map the warp will refuse ───────────────────
#
# Hit for real: `align --fit --every-beats 4` wrote a map whose first segment
# needed a 1.77x stretch, and the next `--warp` refused it — correctly, but by
# then the good map had already been overwritten. The fit is the place that knows
# it produced something unusable, so it is the place to say so.


def test_a_fit_that_cannot_be_warped_is_reported(tmp_path):
    from rambass.align import plan_problem

    timeline = Timeline(bpm=60.0)
    bad = AlignMap(anchors=[Anchor(bar=1, at=0.0), Anchor(bar=2, at=7.1)])
    problem = plan_problem(bad, timeline, bars=8)
    assert problem and "1.77" in problem or "bar 1" in problem


def test_a_usable_fit_reports_no_problem():
    from rambass.align import plan_problem

    timeline = Timeline(bpm=60.0)
    good = AlignMap(anchors=[Anchor(bar=1, at=0.5), Anchor(bar=2, at=4.6),
                             Anchor(bar=3, at=8.55)])
    assert plan_problem(good, timeline, bars=8) is None


def test_an_empty_map_is_not_a_problem_it_is_just_empty():
    from rambass.align import plan_problem

    assert plan_problem(AlignMap(anchors=[]), Timeline(bpm=60.0), bars=8) is None


# ── the first detected beat is not bar 1 ─────────────────────────────────────
#
# The bug this exists to stop, found on the first re-separation. `beat_track` on
# Manlio's new drum stem reports its first beat at **3.831 s** — the song opens on
# a single kick and the new separation made it less prominent, so the tracker
# simply starts three beats late. Anchoring bar 1 to `times[0]` then built the
# whole chain 3.1 s off, and `--keep-bar-one` patched bar 1 back to the real
# 0.692 *afterwards* — leaving a map whose first segment claimed 4.1 s of
# recording for one beat of grid. The warp guard caught it, but only after a
# working map had been overwritten.
#
# Where bar 1 is, is a *measurement* (analyze.find_grid_anchor, or an ear). The
# fitter is told, it does not guess.


def _sparse_intro_beats(first=3.831, n=32):
    """A take whose tracker misses the intro: beats start most of a bar late."""
    return [first + i for i in range(n)]


def test_the_chain_starts_from_the_known_anchor_not_the_first_beat():
    timeline = Timeline(bpm=60.0)
    beats = _sparse_intro_beats()
    anchors = fit_anchors(beats, timeline, bars=8, every_beats=4, start_at=0.831)
    # 0.831 + 3 s puts bar 2 at 3.831, which is a beat the tracker did find.
    assert anchors[0].bar == 1 and anchors[0].at == pytest.approx(0.831)
    plan = warp_plan(AlignMap(anchors=anchors), timeline, bars=8)
    assert all(0.8 < segment.rate < 1.25 for segment in plan)


def test_without_a_known_anchor_it_still_falls_back_to_the_first_beat():
    """A song nobody has measured yet has to produce *something* usable."""
    timeline = Timeline(bpm=60.0)
    anchors = fit_anchors(_sparse_intro_beats(), timeline, bars=8, every_beats=4)
    assert anchors[0].at == pytest.approx(3.831)


def test_a_known_anchor_never_leaves_an_inconsistent_first_segment():
    """The exact failure: bar 1 at 0.692 with the chain built from 3.831."""
    timeline = Timeline(bpm=60.0)
    anchors = fit_anchors(_sparse_intro_beats(), timeline, bars=8,
                          every_beats=4, start_at=0.692)
    amap = AlignMap(anchors=anchors)
    from rambass.align import plan_problem

    assert plan_problem(amap, timeline, bars=8) is None
    assert anchors[0].at == pytest.approx(0.692)


def test_the_known_anchor_is_used_exactly_not_snapped_away():
    """It came from a measurement or an ear. Snapping it to whatever the tracker
    found nearby is how the 3.1 s error got in in the first place."""
    timeline = Timeline(bpm=60.0)
    anchors = fit_anchors([0.9 + i for i in range(16)], timeline, bars=4,
                          every_beats=4, start_at=0.692)
    assert anchors[0].at == pytest.approx(0.692)
