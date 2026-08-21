"""Subtitle cue parsing, formatting, editing and checking."""

from __future__ import annotations

import pytest

from rambass.lyrics import (
    LyricLine,
    LyricsError,
    format_ass,
    format_lrc,
    format_srt,
    format_text,
    format_vtt,
    from_bar_cues,
    load,
    parse_lrc,
    parse_srt,
    problems,
    retime,
    save,
    scale,
    shift,
    stats,
    to_bar_cues,
    wrap_lines,
)
from rambass.timeline import Timeline

# CRLF, a missing index, accents, caps and an ellipsis — as the real files are.
REAL_SRT = (
    "1\r\n00:00:02,850 --> 00:00:07,039\r\nFor My Grana\r\n\r\n"
    "2\r\n00:00:26,170 --> 00:00:27,139\r\nlei è sempre\r\n\r\n"
    "3\r\n00:00:27,140 --> 00:00:28,669\r\npiù carina\r\n\r\n"
    "4\r\n00:00:30,140 --> 00:00:31,579\r\nE non ti ASS...\r\n\r\n"
    "5\r\n00:00:31,580 --> 00:00:34,399\r\n..omiglia neanche un po.\r\n"
)


def _cues() -> list[LyricLine]:
    return parse_srt(REAL_SRT)


# ── parsing ──────────────────────────────────────────────────────────────
def test_parses_crlf_accents_and_caps():
    cues = _cues()
    assert len(cues) == 5
    assert cues[0] == LyricLine(2.85, 7.039, "For My Grana")
    assert cues[1].text == "lei è sempre"
    assert cues[3].text == "E non ti ASS..."


def test_parses_butted_cues_without_treating_them_as_overlaps():
    cues = _cues()
    assert cues[1].end == pytest.approx(27.139)
    assert cues[2].start == pytest.approx(27.140)
    assert problems(cues) == []


def test_tolerates_bom_missing_index_and_dotted_decimals():
    text = "﻿00:00:01.500 --> 00:00:03.000\nuna riga\n"
    cues = parse_srt(text)
    assert cues == [LyricLine(1.5, 3.0, "una riga")]


def test_multi_line_cue():
    cues = parse_srt("1\n00:00:01,000 --> 00:00:02,000\nprima\nseconda\n")
    assert cues[0].lines == ["prima", "seconda"]


def test_blank_input_is_no_cues():
    assert parse_srt("") == []
    assert parse_srt("\n\n  \n") == []


def test_bad_block_is_an_error():
    with pytest.raises(LyricsError, match="no timecode"):
        parse_srt("1\nnot a timecode\nsome words\n")


def test_cue_ending_before_it_starts_is_rejected():
    with pytest.raises(LyricsError, match="ends before"):
        LyricLine(5.0, 2.0, "x")


def test_negative_start_is_rejected():
    with pytest.raises(LyricsError, match="before zero"):
        LyricLine(-1.0, 2.0, "x")


# ── formatting ───────────────────────────────────────────────────────────
def test_srt_round_trip_is_exact():
    cues = _cues()
    assert parse_srt(format_srt(cues)) == cues


def test_srt_is_written_with_crlf_and_renumbered():
    text = format_srt([LyricLine(1.0, 2.0, "a"), LyricLine(3.0, 4.0, "b")])
    assert "\r\n" in text
    assert text.startswith("1\r\n00:00:01,000 --> 00:00:02,000\r\na\r\n")
    assert "\r\n2\r\n" in text


def test_srt_time_formatting_rounds_to_milliseconds():
    text = format_srt([LyricLine(3661.2345, 3662.0, "x")])
    assert "01:01:01,234" in text or "01:01:01,235" in text


def test_vtt_has_the_header_and_dotted_times():
    text = format_vtt(_cues())
    assert text.startswith("WEBVTT")
    assert "00:00:02.850 --> 00:00:07.039" in text


def test_vtt_round_trips_through_load(tmp_path):
    path = tmp_path / "lyrics.vtt"
    path.write_text(format_vtt(_cues()), encoding="utf-8")
    assert load(path) == _cues()


def test_lrc_writes_one_stamp_per_line_and_closes_gaps():
    text = format_lrc(_cues(), title="ForMayGrana")
    assert "[ti:ForMayGrana]" in text
    assert "[00:02.85]For My Grana" in text
    # a gap after cue 1 becomes a bare closing timestamp
    assert "[00:07.03]" in text or "[00:07.04]" in text


def test_lrc_round_trip_keeps_the_words_in_order():
    parsed = parse_lrc(format_lrc(_cues()))
    assert [c.text for c in parsed] == [c.text for c in _cues()]


def test_ass_has_a_playable_header_and_escapes_newlines():
    text = format_ass([LyricLine(1.0, 2.0, "prima\nseconda")], title="X")
    assert "[Script Info]" in text and "[V4+ Styles]" in text
    assert r"prima\Nseconda" in text
    assert "Dialogue: 0,0:00:01.00,0:00:02.00" in text


def test_text_export_is_just_the_words():
    assert format_text(_cues()).splitlines()[0] == "For My Grana"


def test_save_rejects_an_unknown_format(tmp_path):
    with pytest.raises(LyricsError, match="cannot write"):
        save(tmp_path / "lyrics.xyz", _cues())


def test_load_rejects_an_unknown_format(tmp_path):
    path = tmp_path / "lyrics.doc"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(LyricsError, match="do not know how to read"):
        load(path)


def test_saved_srt_bytes_use_crlf(tmp_path):
    path = save(tmp_path / "lyrics.srt", _cues())
    assert b"\r\n" in path.read_bytes()


# ── editing ──────────────────────────────────────────────────────────────
def test_shift_moves_every_cue_and_clamps_at_zero():
    cues = shift(_cues(), 4.0)
    assert cues[0].start == pytest.approx(6.85)
    assert shift(_cues(), -100.0)[0].start == 0.0


def test_scale_and_retime():
    cues = scale(_cues(), 2.0)
    assert cues[0].start == pytest.approx(5.7)
    # cues timed at 124 BPM, replayed at 128, must happen sooner
    faster = retime(_cues(), 124, 128)
    assert faster[0].start < _cues()[0].start
    with pytest.raises(LyricsError):
        scale(_cues(), 0)
    with pytest.raises(LyricsError):
        retime(_cues(), 0, 120)


def test_wrap_lines_breaks_at_word_boundaries():
    long_line = "Ascolta i Ramba che sono dei simpaticoni e non pensare ai giorni"
    wrapped = wrap_lines([LyricLine(0.0, 3.0, long_line)], max_chars=20)
    assert all(len(line) <= 20 for line in wrapped[0].lines)
    assert " ".join(wrapped[0].lines) == long_line


# ── checking ─────────────────────────────────────────────────────────────
def test_problems_finds_overlaps():
    cues = [LyricLine(0.0, 2.0, "a"), LyricLine(1.0, 3.0, "b")]
    assert any("overlap" in p for p in problems(cues))


def test_problems_finds_flashed_and_empty_cues():
    cues = [LyricLine(0.0, 0.05, "a"), LyricLine(1.0, 2.0, "  ")]
    found = problems(cues)
    assert any("only" in p and "ms" in p for p in found)
    assert any("no text" in p for p in found)


def test_a_rapid_fire_syllable_cue_is_not_flagged():
    """The band's real files run down to ~0.29 s; that is style, not error."""
    assert problems([LyricLine(126.99, 127.279, "donna")]) == []


def test_problems_finds_long_lines_and_too_many_lines():
    found = problems([LyricLine(0.0, 3.0, "x" * 60)])
    assert any("character" in p for p in found)
    found = problems([LyricLine(0.0, 3.0, "a\nb\nc\nd")])
    assert any("lines" in p for p in found)


def test_problems_flags_cues_past_the_end_of_the_song():
    found = problems([LyricLine(0.0, 90.0, "a")], duration=30.0)
    assert any("after the" in p for p in found)


def test_problems_on_nothing():
    assert problems([]) == ["no cues at all"]


def test_stats_summarises():
    summary = stats(_cues())
    assert summary["cues"] == 5
    assert summary["first"] == 2.85
    assert summary["last"] == 34.4
    assert summary["words"] > 10
    assert summary["longest_line"] == len("..omiglia neanche un po.")


# ── the bar-cue bridge ───────────────────────────────────────────────────
def test_from_bar_cues_uses_the_audio_clock():
    timeline = Timeline(bpm=120, count_in_bars=2)      # bar 9 -> 4 + 16 = 20 s
    cues = from_bar_cues(
        "[bar 9]\nPrima riga\n\n[bar 17]\nRitornello\n",
        timeline,
        end_seconds=120.0,
    )
    assert cues[0].start == pytest.approx(20.0)
    assert cues[0].end == pytest.approx(36.0)
    assert cues[1].end == pytest.approx(120.0)


def test_to_bar_cues_snaps_to_beats():
    timeline = Timeline(bpm=120, count_in_bars=2)
    text = to_bar_cues([LyricLine(20.0, 22.0, "Prima riga")], timeline)
    assert "[bar 9]" in text
    assert "Prima riga" in text


def test_bar_cue_round_trip_lands_on_the_same_bar():
    timeline = Timeline(bpm=120, count_in_bars=2)
    cues = from_bar_cues("[bar 5]\nuna riga\n", timeline, end_seconds=60.0)
    assert "[bar 5]" in to_bar_cues(cues, timeline)


def test_stats_on_an_empty_cue_list_still_has_every_key():
    """An untouched lyrics.md template parses to zero cues; that is not a crash."""
    from rambass.lyrics import STAT_KEYS

    summary = stats([])
    assert set(summary) == set(STAT_KEYS)
    assert summary["cues"] == 0 and summary["words"] == 0
