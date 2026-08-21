"""Lyric cues, ASS output, click rendering, setlists and the status board."""

from __future__ import annotations

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


# ── click ────────────────────────────────────────────────────────────────
def _click_onsets(buffer: np.ndarray, sample_rate: int) -> list[float]:
    above = np.abs(buffer) > 0.02
    starts = np.where(above[1:] & ~above[:-1])[0] + 1
    merged: list[int] = []
    for start in starts:
        if not merged or start - merged[-1] > 0.05 * sample_rate:
            merged.append(int(start))
    return [s / sample_rate for s in merged]


def test_click_has_one_tick_per_beat_including_the_count_in():
    timeline = Timeline(bpm=120, time_signature="4/4", count_in_bars=2)
    buffer = render_click(timeline, 8, sample_rate=48000)
    assert len(_click_onsets(buffer, 48000)) == (2 + 8) * 4


def test_click_respects_the_metre():
    timeline = Timeline(bpm=90, time_signature="7/8", count_in_bars=1)
    buffer = render_click(timeline, 4, sample_rate=48000)
    assert len(_click_onsets(buffer, 48000)) == (1 + 4) * 7


def test_click_ticks_land_on_the_beat():
    timeline = Timeline(bpm=120, count_in_bars=1)
    onsets = _click_onsets(render_click(timeline, 2, sample_rate=48000), 48000)
    assert onsets[:5] == [pytest.approx(t, abs=0.002) for t in (0.0, 0.5, 1.0, 1.5, 2.0)]


def test_click_length_covers_the_song_plus_count_in_and_tail():
    timeline = Timeline(bpm=120, count_in_bars=2)
    buffer = render_click(timeline, 8, sample_rate=48000, tail_seconds=1.0)
    assert len(buffer) / 48000 == pytest.approx(4.0 + 16.0 + 1.0, abs=0.01)


def test_click_never_clips():
    timeline = Timeline(bpm=200, count_in_bars=2)
    buffer = render_click(timeline, 16, sample_rate=48000, level_db=0.0, accent_db=0.0)
    assert float(np.max(np.abs(buffer))) <= 1.0


def test_click_writes_a_24_bit_wav(tmp_path):
    path = render_click_file(
        tmp_path / "click.wav", Timeline(bpm=120, count_in_bars=1), 4
    )
    with wave.open(str(path)) as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 3          # 24 bit
        assert handle.getframerate() == 48000


def test_click_rejects_a_zero_length_song():
    with pytest.raises(ValueError):
        render_click(Timeline(bpm=120), 0)


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
    text = board([song])
    assert "1/9" in text
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
