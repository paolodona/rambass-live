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
detected beats, and the audio between two anchors is stretched — at its own
pitch, see below — so that the recording's own duration for those bars becomes
the grid's duration.

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
    # lam=0: this pins how a segment is *constructed*, so it reads the anchors
    # literally. Smoothing is a separate decision about where the edges land,
    # measured further down under "smoothing the warp path".
    plan = warp_plan(amap, timeline, bars=8, lam=0.0)
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


# ── the warp itself ──────────────────────────────────────────────────────────


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
    line: p90 107 ms for a single offset, 58 ms per bar, 38 ms per beat.

    Anchor *density* is what this settles, and smoothing the path does not
    reopen it: per beat still beats per bar by a factor of 25 here.
    """
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
    assert fine < coarse / 20.0
    # 41.4 ms at the default lambda, against 1068 ms per bar, and inside the 50
    # ms limit in docs/practice-tracks.md. It was 10.63 ms while the path ran
    # through every anchor -- see the test below for why that number was the
    # metric flattering itself rather than the warp being better.
    assert fine < 50.0, "an anchor on every beat should land every beat"


def test_the_warp_keeps_the_backbeat_late_instead_of_flattening_it():
    """Why the number above moved from 10.6 ms to 41.4 ms, and why that is the
    improvement rather than the regression it looks like.

    `_drifting_click` puts its backbeat 60 ms late on purpose. A path that runs
    through every anchor drags that backbeat onto the grid line -- it lands 0.7
    ms out -- so the "worst error" metric scores nearly zero by **deleting the
    groove**, which is the one thing a reference track exists to show. Warping
    the band flat and then asking whether the new drums sit where the band
    played is a question with no content left in it.

    Smoothed, the 60 ms interval survives essentially intact (61 ms here). It
    comes out centred on the grid rather than hung off the downbeat, because an
    alternation this regular is indistinguishable from noise to a smoothness
    prior -- nothing tells it which of the two phases is "the" beat. Real swing
    lives in the eighths, below the anchors, so it never reaches this operator
    at all.
    """
    sr, bars = 8000, 8
    beats, samples = _drifting_click(sr, bars)
    timeline = Timeline(bpm=60.0)
    amap = AlignMap(anchors=fit_anchors(beats, timeline, bars=bars,
                                        every_beats=1))

    def _swing(lam):
        out = warp_samples(samples, sr,
                           warp_plan(amap, timeline, bars=bars, lam=lam))
        found = np.flatnonzero(out > 0.2) / sr
        offsets = [(min(found, key=lambda x: abs(x - i)) - i) * 1000.0
                   for i in range(1, bars * 4)]
        late = [o for i, o in enumerate(offsets, start=1) if i % 2]
        early = [o for i, o in enumerate(offsets, start=1) if i % 2 == 0]
        return float(np.mean(late) - np.mean(early))

    assert _swing(0.0) < 10.0, "the old path flattened a 60 ms backbeat to nothing"
    assert _swing(DEFAULT_LAM) > 50.0, "the smoothed path has to keep the groove"


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


# ── the warp preserves pitch ─────────────────────────────────────────────────
#
# The bug this section exists to stop. `warp_samples` used to *resample*: read
# the source at `position * rate` and interpolate. That changes rate and pitch
# together, by 12*log2(rate) semitones. Manlio's fitted map has 308 per-beat
# segments with rates spanning 0.929-1.091, so the pitch moved every beat.
# Measured on a 440 Hz tone pushed through the real plan:
#
#     rate 0.929 -> 408.0 Hz  (-1.31 semitones)
#     rate 1.000 -> 440.0 Hz  ( 0.00)
#     rate 1.091 -> 480.0 Hz  (+1.51 semitones)
#
# 2.78 semitones peak to peak, once per beat. That is the warble, and it made
# the one file this module produces unusable for the one job it has.


def _dominant_hz(x, sr):
    """Spectral peak, parabolically interpolated. numpy only, no librosa."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    mag = np.abs(np.fft.rfft(x * np.hanning(n)))
    k = int(np.argmax(mag))
    if 0 < k < len(mag) - 1:
        a, b, c = np.log(mag[k - 1:k + 2] + 1e-20)
        k = k + 0.5 * (a - c) / (a - 2.0 * b + c)
    return k * sr / n


def _one_rate_plan(rate, seconds):
    from rambass.align import WarpSegment

    return [WarpSegment(source_start=0.0, source_end=rate * seconds,
                        target_start=0.0, target_end=seconds)]


@pytest.mark.parametrize("rate,resampling_read_it_as", [
    (0.900, 396.0),
    (0.929, 408.0),      # measured on Manlio's real plan: -1.31 semitones
    (1.000, 440.0),
    (1.091, 480.0),      # measured on Manlio's real plan: +1.51 semitones
    (1.100, 484.0),
])
def test_a_sine_keeps_its_pitch_through_the_warp(rate, resampling_read_it_as):
    """440 Hz in, 440 Hz out, within 1% (0.17 semitones) at every rate."""
    sr, seconds = 22050, 2.0
    source = np.sin(2.0 * np.pi * 440.0
                    * np.arange(int(sr * (seconds * 1.3 + 1.0))) / sr
                    ).astype(np.float32)
    out = warp_samples(source, sr, _one_rate_plan(rate, seconds))
    heard = _dominant_hz(out[int(0.25 * sr):int(1.75 * sr)], sr)
    assert heard == pytest.approx(440.0, rel=0.01), f"rate {rate} read {heard:.1f} Hz"
    if rate != 1.0:
        # and it is not what resampling produced, which is the whole point
        assert abs(heard - resampling_read_it_as) > 0.5 * abs(
            resampling_read_it_as - 440.0)


def test_pitch_holds_across_many_segments_without_clicking():
    """One continuous pass, not 308 independently warped segments concatenated.
    Alternating rates every half second is the worst case for seams."""
    from rambass.align import WarpSegment

    sr = 22050
    plan, at, source_at = [], 0.0, 0.0
    for index in range(12):
        rate = 0.929 if index % 2 else 1.091
        plan.append(WarpSegment(source_start=source_at,
                                source_end=source_at + 0.5 * rate,
                                target_start=at, target_end=at + 0.5))
        source_at += 0.5 * rate
        at += 0.5
    source = np.sin(2.0 * np.pi * 220.0 * np.arange(int(sr * 12)) / sr
                    ).astype(np.float32)
    out = warp_samples(source, sr, plan)

    heard = _dominant_hz(out[int(0.5 * sr):int(5.5 * sr)], sr)
    assert heard == pytest.approx(220.0, rel=0.01)
    # a 220 Hz sine at 22050 moves at most 0.063 per sample; a seam artefact is
    # a step, so anything over 0.3 is a click and not the waveform
    assert float(np.max(np.abs(np.diff(out)))) < 0.3


def test_silence_in_silence_out():
    sr = 8000
    timeline = Timeline(bpm=60.0)
    amap = AlignMap(anchors=[Anchor(bar=1, at=0.4), Anchor(bar=3, at=8.5)])
    out = warp_samples(np.zeros(sr * 20, dtype=np.float32), sr,
                       warp_plan(amap, timeline, bars=4))
    assert float(np.max(np.abs(out))) == pytest.approx(0.0)


def test_a_stereo_file_keeps_its_channels_together():
    """WSOLA picks an offset per frame; picking it per channel independently
    would decorrelate the two sides and smear the stereo image. One offset,
    chosen on the mixdown, applied to both."""
    sr = 22050
    tone = np.sin(2.0 * np.pi * 330.0 * np.arange(sr * 4) / sr).astype(np.float32)
    stereo = np.stack([tone, tone], axis=1)
    out = warp_samples(stereo, sr, _one_rate_plan(1.05, 3.0))
    assert out.shape == (int(round(3.0 * sr)), 2)
    assert np.array_equal(out[:, 0], out[:, 1])


def test_a_mono_array_still_comes_back_mono():
    sr = 8000
    out = warp_samples(np.zeros(sr * 5, dtype=np.float32), sr,
                       _one_rate_plan(1.02, 4.0))
    assert out.ndim == 1


def test_a_grid_position_before_the_recording_starts_is_silence():
    """The mirror of reading past the end: an anchor can sit later in the
    recording than the grid position it maps to, and the head of the output then
    has nothing to read. Silence, not a wrap."""
    from rambass.align import WarpSegment

    sr = 8000
    source = np.ones(sr * 4, dtype=np.float32)
    plan = [WarpSegment(source_start=-2.0, source_end=2.0,
                        target_start=0.0, target_end=4.0)]
    out = warp_samples(source, sr, plan)
    assert float(np.max(np.abs(out[:int(1.5 * sr)]))) == pytest.approx(0.0)
    assert float(np.max(np.abs(out[int(2.5 * sr):int(3.5 * sr)]))) > 0.5


# ── smoothing the warp path ──────────────────────────────────────────────────
#
# The warp path is piecewise linear through every anchor, so its *rate* — the
# slope — steps at every knot. On Manlio's own fitted map that is 310 knots and
# a rate that jumps by a mean of 2.7% and a maximum of 14.0% from one beat to
# the next; Tutti in Fila's is 3.9% mean and 22.4% max. Paolo heard it: "the
# warped track is jarring as it speeds up and down in an unnatural way."
#
# The knots are not carrying tempo. The beat-to-beat rate series on Manlio has a
# lag-1 autocorrelation of **+0.03** — white noise. Real tempo drift is
# autocorrelated: a band that slows down stays slow for a few bars. What the
# per-beat rate is actually tracking is per-anchor noise, sd 18 ms, from the beat
# detector's placement and from the drummer's own micro-timing. So the current
# map modulates playback speed by up to 14% in response to a signal that carries
# no tempo information at all, and then bakes that noise into the audio.
#
# The fix is a smoother path, not a finer one: subdividing to 16ths adds more
# knots each carrying the same noise over a shorter span, so the rate swings get
# *worse*. `smooth_anchors` fits a penalised least-squares (smoothing) spline —
# minimise sum (s_i - at_i)^2 + lam * integral (s'')^2 — so the path no longer
# has to pass through every anchor. Every anchor still votes; none of them
# dictates.

from rambass.align import DEFAULT_LAM, smooth_anchors  # noqa: E402


def _rates(anchors, timeline):
    grid = np.array([a.grid_seconds(timeline) for a in anchors])
    at = np.array([a.at for a in anchors])
    return np.diff(at) / np.diff(grid)


def _even_anchors(count, *, lag=0.0, noise=None):
    """`count` beats of a dead-even 60 BPM take, one anchor per beat."""
    return [Anchor(bar=i // 4 + 1, beat=i % 4 + 1.0,
                   at=float(i) + lag + (0.0 if noise is None else noise[i]))
            for i in range(count)]


def test_a_dead_even_take_is_left_exactly_alone():
    """Smoothing must not distort data that has nothing wrong with it."""
    timeline = Timeline(bpm=60.0)
    smoothed = smooth_anchors(_even_anchors(64), timeline, lam=DEFAULT_LAM)
    assert _rates(smoothed, timeline) == pytest.approx(1.0, abs=1e-9)


def test_a_constant_lag_costs_no_rate_change_at_all():
    """Paolo's scenario, first half: the drummer sits 100 ms behind the beat and
    *stays* there. He is playing in time — he is only translated off the grid —
    so the honest warp is a shift with no stretch anywhere.

    A constant lag is carried by the path's intercept, not its slope, which is
    why this is free: the map simply reads the recording 100 ms ahead of the
    grid and the section plays at its own speed.
    """
    timeline = Timeline(bpm=60.0)
    smoothed = smooth_anchors(_even_anchors(64, lag=0.100), timeline,
                              lam=DEFAULT_LAM)
    assert _rates(smoothed, timeline) == pytest.approx(1.0, abs=1e-9)
    assert smoothed[0].at == pytest.approx(0.100, abs=1e-6)


def test_a_steady_accelerando_is_followed_not_flattened():
    """Smoothing must remove noise, not drift. This take really does speed up —
    3% a bar, the same shape `_drifting_click` uses — and the smoothed path has
    to track it, or the warp has stopped doing its one job.
    """
    timeline = Timeline(bpm=60.0)
    anchors, at, step = [], 0.0, 1.0
    for index in range(64):
        anchors.append(Anchor(bar=index // 4 + 1, beat=index % 4 + 1.0, at=at))
        at += step
        if index % 4 == 3:
            step *= 0.97
    smoothed = smooth_anchors(anchors, timeline, lam=DEFAULT_LAM)
    off = np.abs(np.array([s.at for s in smoothed])
                 - np.array([a.at for a in anchors])) * 1000.0
    assert off.max() < 10.0, "a real accelerando must survive smoothing"


# The scenario Paolo posed, in full: 16 bars sitting 100 ms behind the beat, then
# a catch-up at the section change, then dead on. Built here with the per-anchor
# noise measured on Manlio (sd 18 ms) so the tests measure the case that exists
# rather than an ideal one.

_STEP_BEATS = 128
_STEP_SWITCH = 64
_STEP_NOISE_MS = 18.0


def _step_scenario(seed=7):
    """`(anchors, truth)` — the noisy detections, and where the band really was."""
    rng = np.random.default_rng(seed)
    grid = np.arange(_STEP_BEATS, dtype=float)          # 60 BPM: 1 beat = 1 s
    truth = grid + np.where(grid < _STEP_SWITCH, 0.100, 0.0)
    noisy = truth + rng.normal(0.0, _STEP_NOISE_MS / 1000.0, _STEP_BEATS)
    anchors = [Anchor(bar=i // 4 + 1, beat=i % 4 + 1.0, at=float(noisy[i]))
               for i in range(_STEP_BEATS)]
    return anchors, truth


def test_inside_a_constant_lag_the_noise_stops_driving_the_rate():
    """The headline. Inside either section the correct rate is exactly 1.000.

    Interpolating every anchor gives 1.000 +/- 0.023 there — a +/-2.3% speed
    wobble once per beat, tracking nothing but detection noise. Smoothing at the
    default lambda gives +/- 0.005, four times quieter.
    """
    timeline = Timeline(bpm=60.0)
    anchors, _ = _step_scenario()
    raw = _rates(anchors, timeline)[:_STEP_SWITCH - 3]
    smoothed = _rates(smooth_anchors(anchors, timeline, lam=DEFAULT_LAM),
                      timeline)[:_STEP_SWITCH - 3]
    assert raw.std() > 0.020
    assert smoothed.std() < 0.008
    assert smoothed.std() < raw.std() / 3.0


def test_the_real_catch_up_stops_being_buried_in_the_noise():
    """The second half of Paolo's scenario. The band genuinely catches up 100 ms
    at the section change, which is a 10% rate event over one beat — but under
    the raw map it is indistinguishable from the wobble around it, because three
    beats earlier the noise alone produced a 1.042.

    After smoothing, the boundary is the largest excursion in the whole song by
    a clear margin, which is what makes the warp legible instead of jarring.
    """
    timeline = Timeline(bpm=60.0)
    anchors, _ = _step_scenario()
    timeline_rates = _rates(smooth_anchors(anchors, timeline, lam=DEFAULT_LAM),
                            timeline)
    near = slice(_STEP_SWITCH - 3, _STEP_SWITCH + 2)
    excursion = np.abs(timeline_rates - 1.0)
    assert excursion[near].max() > 0.025, "the real event must survive"
    away = np.delete(excursion, np.r_[near])
    assert excursion[near].max() > 2.0 * away.max(), (
        "the section change should be the biggest thing in the rate curve")


def test_smoothing_is_more_accurate_than_interpolation_not_less():
    """The counterintuitive one, and the reason lambda is on by default.

    The raw map scores a perfect 0 ms against the detected beats because it
    interpolates them exactly — including their noise, which it then bakes into
    the audio. Measured against where the drummer *actually* played, it is p90
    27.7 ms out. The smoothed path is p90 14.5 ms: twice as accurate. So the
    residual smoothing leaves against the anchors is not error, it is rejected
    noise, and `residual_holdout_ms` is not the number to tune lambda by.
    """
    timeline = Timeline(bpm=60.0)
    anchors, truth = _step_scenario()
    raw = np.abs(np.array([a.at for a in anchors]) - truth) * 1000.0
    smoothed = np.abs(
        np.array([s.at for s in smooth_anchors(anchors, timeline,
                                               lam=DEFAULT_LAM)]) - truth
    ) * 1000.0
    assert np.percentile(raw, 90) > 25.0
    assert np.percentile(smoothed, 90) < 18.0
    assert np.percentile(smoothed, 90) < np.percentile(raw, 90) / 1.7


def test_the_one_beat_lurch_becomes_a_glide():
    """What Paolo actually hears. The raw map puts the whole 100 ms catch-up
    inside a single beat and snaps back — a 10% step against its neighbours. The
    smoothed path spreads the same correction over about five beats, so the rate
    changes by roughly 2% per beat at the worst instead of 14%.
    """
    timeline = Timeline(bpm=60.0)
    anchors, _ = _step_scenario()
    raw = np.abs(np.diff(_rates(anchors, timeline))).max()
    smoothed = np.abs(np.diff(
        _rates(smooth_anchors(anchors, timeline, lam=DEFAULT_LAM), timeline))).max()
    assert raw > 0.09
    assert smoothed < 0.04
    assert smoothed < raw / 3.0


def test_the_smoothed_path_never_runs_backwards():
    """A non-monotone path would read the recording backwards. Noise is exactly
    what could produce one, so it is checked on the noisy scenario.
    """
    timeline = Timeline(bpm=60.0)
    anchors, _ = _step_scenario()
    for lam in (0.3, DEFAULT_LAM, 3.0, 10.0, 100.0):
        at = [s.at for s in smooth_anchors(anchors, timeline, lam=lam)]
        assert all(b > a for a, b in zip(at, at[1:])), f"lam={lam} reverses"


def test_lambda_zero_is_exactly_the_old_behaviour():
    """The escape hatch has to be an identity, not an approximation."""
    timeline = Timeline(bpm=60.0)
    anchors, _ = _step_scenario()
    assert [s.at for s in smooth_anchors(anchors, timeline, lam=0.0)] == [
        a.at for a in anchors]


def test_too_few_anchors_to_smooth_is_a_no_op():
    """A second difference needs three points. Fewer is returned untouched
    rather than raising: a one-anchor map is an offset and still has to warp.
    """
    timeline = Timeline(bpm=60.0)
    for count in (0, 1, 2):
        anchors = _even_anchors(count, lag=0.4)
        assert [s.at for s in smooth_anchors(anchors, timeline,
                                             lam=DEFAULT_LAM)] == [
            a.at for a in anchors]


def test_the_plan_smooths_by_default_and_can_be_told_not_to():
    timeline = Timeline(bpm=60.0)
    anchors, _ = _step_scenario()
    amap = AlignMap(anchors=anchors)
    default = np.array([s.rate for s in warp_plan(amap, timeline, bars=32)])
    raw = np.array([s.rate for s in warp_plan(amap, timeline, bars=32, lam=0.0)])
    assert default.std() < raw.std() / 3.0
    assert len(default) == len(raw), "smoothing must not change the segmenting"


def test_manlios_own_map_stops_swinging_seven_percent():
    """The measurement on real data, not a simulation.

    Manlio's committed `practice/align.yaml`: 310 per-beat anchors over 78 bars
    at 60 BPM, correcting a drift of -104 to +187 ms. Interpolated, the rate
    swings 0.905-1.142. At the default lambda it swings 0.948-1.073 and the
    worst beat-to-beat step falls from 14.0% to under 4%.
    """
    from pathlib import Path as _Path

    from rambass.align import load_align

    path = (_Path(__file__).resolve().parent.parent / "songs" /
            "tutti-in-fila" / "09-manlio" / "practice" / "align.yaml")
    if not path.is_file():                       # pragma: no cover
        pytest.skip("Manlio's align.yaml is not in this checkout")
    timeline = Timeline(bpm=60.0)
    anchors = sorted(load_align(path).anchors, key=lambda a: a.position)
    assert len(anchors) > 300

    raw = _rates(anchors, timeline)
    assert raw.min() < 0.91 and raw.max() > 1.14
    smoothed = _rates(smooth_anchors(anchors, timeline, lam=DEFAULT_LAM), timeline)
    assert smoothed.min() > 0.94, f"still squashing to {smoothed.min():.3f}"
    assert smoothed.max() < 1.10, f"still stretching to {smoothed.max():.3f}"
    assert np.abs(np.diff(smoothed)).max() < 0.05

    # The whole remaining excursion is in the first segment, and it is a known
    # bad anchor rather than anything the band played: Manlio's bar 1 is
    # hand-set at 0.692 s and the tracker's next beat is 1.142 s later, a 14%
    # single-beat jump in a song whose real drift is a few percent. Pinning bar
    # 1 means smoothing damps that to 1.095 but cannot remove it. Away from the
    # head the map settles to 0.948-1.041, and dropping the outlier itself is
    # robust fitting -- a separate change, not this one.
    assert smoothed[2:].max() < 1.05, f"body still swings to {smoothed[2:].max():.3f}"
    assert smoothed[2:].min() > 0.94


def test_the_cli_default_lambda_matches_the_module():
    """`rambass align --help` prints a number that must not drift from the one
    the warp actually uses. cli.py cannot import align at module level — numpy
    is not in the core tier — so the constant is duplicated and pinned here.
    """
    from rambass.cli import _DEFAULT_LAM

    assert _DEFAULT_LAM == DEFAULT_LAM
