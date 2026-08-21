"""Lyric cues, ASS output, click rendering, setlists and the status board."""

from __future__ import annotations

import pathlib
import wave

import numpy as np
import pytest

from rambass.click import render_click, render_click_file
from rambass.manifest import save_song
from rambass.project import ProjectError
from rambass.setlist import Setlist, find_setlist, running_order
from rambass.status import board, next_actions
from rambass.timeline import Timeline
from rambass.video import build_ass, image_segments, parse_lyrics

LYRICS = """\
# Nome Canzone

[bar 1] image: intro.png

[bar 9]
Prima riga
Seconda riga

[bar 17.3] image: foto.jpg
Ritornello

[bar 25] blank
"""


# ── lyric cues ───────────────────────────────────────────────────────────
def test_parse_lyrics_reads_bars_beats_images_and_blanks():
    cues = parse_lyrics(LYRICS)
    assert [(c.bar, c.beat) for c in cues] == [(1, 1.0), (9, 1.0), (17, 3.0), (25, 1.0)]
    assert cues[0].image == "intro.png" and cues[0].lines == []
    assert cues[1].lines == ["Prima riga", "Seconda riga"]
    assert cues[2].image == "foto.jpg" and cues[2].lines == ["Ritornello"]
    assert cues[3].blank is True


def test_text_before_any_cue_lands_at_bar_one():
    cues = parse_lyrics("Riga senza cue\n")
    assert cues[0].bar == 1 and cues[0].lines == ["Riga senza cue"]


def test_cues_are_sorted_even_if_written_out_of_order():
    cues = parse_lyrics("[bar 17]\nb\n[bar 9]\na\n")
    assert [c.bar for c in cues] == [9, 17]


def test_ass_times_use_the_audio_clock():
    timeline = Timeline(bpm=120, count_in_bars=2)     # bar 9 -> 4 + 16 = 20 s
    text = build_ass(parse_lyrics(LYRICS), timeline, end_seconds=120.0)
    assert "Dialogue: 0,0:00:20.00,0:00:37.00" in text
    assert r"Prima riga\NSeconda riga" in text


def test_ass_has_a_playable_header():
    text = build_ass(parse_lyrics(LYRICS), Timeline(bpm=120), end_seconds=60.0)
    assert "[Script Info]" in text
    assert "ScriptType: v4.00+" in text
    assert "[V4+ Styles]" in text
    assert text.count("Style: Lyrics,") == 1
    assert "[Events]" in text


def test_a_blank_cue_ends_the_previous_line():
    timeline = Timeline(bpm=120, count_in_bars=2)
    text = build_ass(parse_lyrics(LYRICS), timeline, end_seconds=120.0)
    # "Ritornello" starts at bar 17 beat 3 and ends where the blank cue is
    line = next(x for x in text.splitlines() if "Ritornello" in x)
    assert line.split(",")[2] == "0:00:52.00"


def test_image_segments_hold_until_the_next_image():
    timeline = Timeline(bpm=120, count_in_bars=2)
    segments = image_segments(parse_lyrics(LYRICS), timeline, 120.0)
    assert segments == [
        ("intro.png", pytest.approx(4.0), pytest.approx(37.0)),
        ("foto.jpg", pytest.approx(37.0), pytest.approx(120.0)),
    ]


def test_empty_lyrics_produce_a_valid_but_empty_ass():
    text = build_ass([], Timeline(bpm=120), end_seconds=10.0)
    assert "[Events]" in text
    assert "Dialogue:" not in text


# ── sticks: the count-in, its own stem ───────────────────────────────────
def _click_onsets(buffer: np.ndarray, sample_rate: int, threshold: float = 0.02):
    above = np.abs(buffer) > threshold
    starts = list(np.where(above[1:] & ~above[:-1])[0] + 1)
    # A hit that begins on sample 0 has no preceding sample to rise from, so the
    # edge test cannot see it. That is the normal case for the first stick.
    if above.size and above[0]:
        starts.insert(0, 0)
    merged: list[int] = []
    for start in starts:
        if not merged or start - merged[-1] > 0.05 * sample_rate:
            merged.append(int(start))
    return [s / sample_rate for s in merged]


def test_a_stick_hit_sounds_like_wood_not_a_beep():
    """Broadband, bright, no low end, and gone in a few tens of milliseconds."""
    from rambass.click import stick_hit

    sample_rate = 48000
    hit = stick_hit(sample_rate, seed=0)
    spectrum = np.abs(np.fft.rfft(hit * np.hanning(len(hit))))
    freqs = np.fft.rfftfreq(len(hit), 1 / sample_rate)
    power = spectrum ** 2
    total = float(power.sum())

    def share(low, high):
        return float(power[(freqs >= low) & (freqs < high)].sum()) / total

    assert share(0, 300) < 0.02          # no boom — it is not a drum
    assert share(1500, 8000) > 0.6       # the crack lives here
    assert share(10000, 24000) < 0.10    # not hiss

    rms = lambda a: float(np.sqrt((a ** 2).mean()))  # noqa: E731
    assert rms(hit[: int(0.002 * sample_rate)]) > 10 * rms(hit[int(0.03 * sample_rate):])


def test_stick_hits_are_seeded_and_vary():
    """Identical hits in a row read as a machine; the seed keeps it repeatable."""
    from rambass.click import stick_hit

    assert np.allclose(stick_hit(48000, seed=4), stick_hit(48000, seed=4))
    assert not np.allclose(stick_hit(48000, seed=4), stick_hit(48000, seed=5))


def test_accent_on_beat_one_is_brighter():
    from rambass.click import stick_hit

    def centroid(hit):
        spectrum = np.abs(np.fft.rfft(hit))
        freqs = np.fft.rfftfreq(len(hit), 1 / 48000)
        return float((spectrum * freqs).sum() / spectrum.sum())

    assert centroid(stick_hit(48000, accent=True, seed=0)) > centroid(
        stick_hit(48000, accent=False, seed=0)
    )


def test_sticks_cover_the_count_in_only():
    from rambass.click import render_sticks

    timeline = Timeline(bpm=120, time_signature="4/4", count_in_bars=2)
    buffer = render_sticks(timeline, sample_rate=48000, tail_seconds=0.0)
    # two bars at 120 = 4.0 s, and nothing beyond it
    assert len(buffer) / 48000 == pytest.approx(4.0, abs=0.01)
    assert len(_click_onsets(buffer, 48000)) == 8


def test_sticks_land_on_the_beat():
    from rambass.click import render_sticks

    timeline = Timeline(bpm=120, count_in_bars=1)
    onsets = _click_onsets(render_sticks(timeline, sample_rate=48000), 48000)
    assert onsets == [pytest.approx(t, abs=0.002) for t in (0.0, 0.5, 1.0, 1.5)]


def test_sticks_respect_the_metre():
    from rambass.click import render_sticks

    timeline = Timeline(bpm=90, time_signature="7/8", count_in_bars=1)
    assert len(_click_onsets(render_sticks(timeline, sample_rate=48000), 48000)) == 7


def test_sticks_use_the_bars_argument_over_the_manifest():
    from rambass.click import render_sticks

    timeline = Timeline(bpm=120, count_in_bars=2)
    buffer = render_sticks(timeline, 1, sample_rate=48000, tail_seconds=0.0)
    assert len(buffer) / 48000 == pytest.approx(2.0, abs=0.01)


def test_sticks_reject_a_zero_bar_count_in():
    from rambass.click import render_sticks

    with pytest.raises(ValueError):
        render_sticks(Timeline(bpm=120, count_in_bars=0))


def test_sticks_can_use_a_real_recorded_sample():
    from rambass.click import render_sticks

    impulse = np.zeros(480, dtype=np.float32)
    impulse[0] = 1.0
    buffer = render_sticks(
        Timeline(bpm=120, count_in_bars=1), sample_rate=48000, sample=impulse
    )
    assert len(_click_onsets(buffer, 48000)) == 4


def test_sticks_write_a_24_bit_wav(tmp_path):
    from rambass.click import render_sticks_file

    path = render_sticks_file(tmp_path / "sticks.wav", Timeline(bpm=120, count_in_bars=2))
    with wave.open(str(path)) as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 3
        assert handle.getframerate() == 48000


# ── click: the song only, its own stem ───────────────────────────────────
def test_click_starts_at_bar_one_not_at_the_count_in():
    """The two stems must not overlap, or muting one leaves a doubled beat."""
    timeline = Timeline(bpm=120, time_signature="4/4", count_in_bars=2)
    buffer = render_click(timeline, 8, sample_rate=48000, tail_seconds=0.0)
    assert len(buffer) / 48000 == pytest.approx(16.0, abs=0.01)
    assert len(_click_onsets(buffer, 48000)) == 8 * 4
    assert _click_onsets(buffer, 48000)[0] == pytest.approx(0.0, abs=0.002)


def test_click_respects_the_metre():
    timeline = Timeline(bpm=90, time_signature="7/8")
    buffer = render_click(timeline, 4, sample_rate=48000)
    assert len(_click_onsets(buffer, 48000)) == 4 * 7


def test_click_never_clips():
    timeline = Timeline(bpm=200)
    buffer = render_click(timeline, 16, sample_rate=48000, level_db=0.0, accent_db=0.0)
    assert float(np.max(np.abs(buffer))) <= 1.0


def test_click_writes_a_24_bit_wav(tmp_path):
    path = render_click_file(tmp_path / "click.wav", Timeline(bpm=120), 4)
    with wave.open(str(path)) as handle:
        assert handle.getsampwidth() == 3


def test_click_rejects_a_zero_length_song():
    with pytest.raises(ValueError):
        render_click(Timeline(bpm=120), 0)


def test_nothing_in_the_click_module_touches_a_backing_track():
    """A click mixed into the base is unremovable, so the path must not exist."""
    import rambass.click as click_module

    assert not hasattr(click_module, "prepend_count_in")
    source = pathlib.Path(click_module.__file__).read_text()
    assert "backing" not in source.lower().replace("backing track", "")


# ── setlists ─────────────────────────────────────────────────────────────
def test_setlist_resolves_songs_in_order(project, song):
    other = project.songs_dir / "tutti-in-fila" / "04-altra"
    song.slug, song.title, song.track = "altra", "Altra", 4
    save_song(song, other)

    (project.setlists_dir / "gig.yaml").write_text(
        "name: Test\nsongs:\n  - 03-tutti-in-fila\n  - 04-altra\n", encoding="utf-8"
    )
    setlist = Setlist.load(find_setlist(project, "gig"))
    assert [s.title for s in setlist.resolve(project)] == ["Tutti In Fila", "Altra"]


def test_setlist_reports_a_missing_song(project, song):
    (project.setlists_dir / "gig.yaml").write_text(
        "name: Test\nsongs: [03-tutti-in-fila, non-esiste]\n", encoding="utf-8"
    )
    setlist = Setlist.load(find_setlist(project, "gig"))
    with pytest.raises(ProjectError, match="non-esiste"):
        setlist.resolve(project)


def test_find_setlist_reports_what_is_available(project):
    with pytest.raises(ProjectError, match="no setlist"):
        find_setlist(project, "mancante")


def test_running_order_accumulates_time(song):
    setlist = Setlist(name="Test", songs=["a", "b"])
    text = running_order(setlist, [song, song], gap_seconds=30.0)
    assert "| 1 | Tutti In Fila |" in text
    assert "2 songs" in text
    # 68 s each plus one 30 s gap = 2:46
    assert "1:08" in text and "2:46" in text


# ── status board ─────────────────────────────────────────────────────────
def test_board_counts_progress_and_skips_not_applicable(song):
    song.status["source"] = "done"
    song.status["stems"] = "n/a"
    from rambass.manifest import STAGES

    text = board([song])
    assert f"1/{len(STAGES) - 1}" in text
    assert "legend:" in text


def test_board_markdown_is_a_table(song):
    text = board([song], markdown=True)
    assert text.splitlines()[0].startswith("| song |")
    assert "legend:" not in text


def test_board_handles_an_empty_project():
    assert "no songs yet" in board([])


def test_next_actions_points_at_the_first_unfinished_stage(song):
    song.status["source"] = "done"
    assert "analyze" in next_actions([song])
    assert next_actions([]) == ""


# ── the board and scope ──────────────────────────────────────────────────
def test_board_lists_cut_songs_separately_and_excludes_them_from_the_count(song):
    from rambass.manifest import Song

    cut = Song.from_dict({
        "title": "Solero", "album": "tutti-in-fila",
        "excluded": {"from_set": True, "reason": "no WAV master"},
    })
    text = board([song, cut])
    assert "1 songs in the set" in text or "1 song" in text
    assert "cut from the set (1)" in text
    assert "no WAV master" in text
    # the cut song must not appear as a grid row
    grid = text.split("cut from the set")[0]
    assert "Solero" not in grid


def test_board_names_the_a_cappella_songs(song):
    song.drums_origin = "a-cappella"
    text = board([song])
    assert "a cappella (no backing track)" in text
    assert song.title in text


def test_board_when_everything_is_cut(song):
    song.excluded = True
    assert "every song is excluded" in board([song])


def test_next_actions_skips_cut_songs(song):
    song.excluded = True
    assert next_actions([song]) == "everything is done — go and play the gig"
