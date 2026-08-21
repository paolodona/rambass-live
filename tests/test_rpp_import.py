"""Reading an existing Reaper project.

The fixture mirrors the structure of the band's real 2023 live projects
(Reaper 6.82): a long title-card lead-in before the music, a BASE audio track,
a "Program change" track of short MIDI items each holding one bank-select +
program-change triplet, and video tracks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rambass.gx100 import ProgramMap
from rambass.manifest import Song
from rambass.reaper import import_into_song, parse_rpp

FIXTURE = Path(__file__).parent / "fixtures" / "live-project.RPP"


@pytest.fixture
def parsed():
    return parse_rpp(FIXTURE.read_text(encoding="utf-8"))


def test_reads_version_tempo_and_metre(parsed):
    assert parsed.version == "6.82/win64"
    assert parsed.bpm == pytest.approx(90.0)
    assert parsed.time_signature == (4, 4)


def test_project_tempo_is_not_confused_with_a_nested_one(parsed):
    """The METRONOME block also contains numbers; only top-level TEMPO counts."""
    assert parsed.bpm == pytest.approx(90.0)


def test_reads_track_names_including_quoted_ones(parsed):
    assert [t.name for t in parsed.tracks] == [
        "BASE", "Program change", "Cover", "Video - NO AUDIO",
    ]


def test_finds_the_hardware_midi_output(parsed):
    assert parsed.track("Program change").midi_out == 384
    assert parsed.track("BASE").midi_out == -1


def test_reads_markers_with_quoted_names(parsed):
    names = {name for _, name, _ in parsed.markers}
    assert {"intro", "start", "vinceremo", "grande rutto", "SOLO"} <= names


def test_distinguishes_a_region_from_a_marker(parsed):
    by_name = {name: is_region for _, name, is_region in parsed.markers}
    assert by_name["intro"] is False
    assert by_name["il finale"] is True


def test_audio_items_carry_their_source_file(parsed):
    items = parsed.audio_items()
    assert len(items) == 2
    assert all(i.source_file == "I Phooffi_base_2.wav" for i in items)
    assert items[0].position == pytest.approx(135.830860469490)


def test_video_items_are_not_counted_as_audio(parsed):
    sources = {i.source_type for t in parsed.tracks for i in t.items}
    assert sources == {"WAVE", "MIDI", "VIDEO"}
    assert all(i.source_type == "WAVE" for i in parsed.audio_items())


def test_first_audio_position_is_the_musical_zero(parsed):
    """The title card runs for over two minutes before the music starts."""
    assert parsed.first_audio_position() == pytest.approx(135.830860469490)


def test_collapses_bank_select_pairs_onto_the_program_change(parsed):
    changes = parsed.program_changes()
    assert [(round(pos, 2), msb, lsb, pc) for pos, msb, lsb, pc in changes] == [
        (0.0, 0, 0, 1),
        (349.33, 0, 0, 0),
        (508.33, 0, 0, 2),
    ]


def test_all_notes_off_is_not_read_as_a_program_change(parsed):
    """Each MIDI item ends with CC#123; that must not become a patch change."""
    assert len(parsed.program_changes()) == 3


# ── importing into a manifest ────────────────────────────────────────────
def _song() -> Song:
    return Song.from_dict({"title": "Phooffi", "count_in": {"bars": 2}})


def test_import_converts_markers_to_bars(parsed):
    data, _ = import_into_song(parsed, _song())
    by_name = {s.name: s.bar for s in data["sections"]}
    # 90 BPM 4/4 = 2.667 s per bar; "start" is 90.84 s after the musical zero
    assert by_name["intro"] == 2
    assert by_name["start"] == 35
    assert by_name["SOLO"] == 140


def test_import_skips_the_closing_line_of_a_region(parsed):
    data, _ = import_into_song(parsed, _song())
    assert all(s.name.strip() for s in data["sections"])


def test_import_resolves_program_changes_to_memories(parsed):
    data, _ = import_into_song(parsed, _song(), program_map=ProgramMap())
    memories = [c.memory for c in data["patch_changes"]]
    assert memories == ["U01-2", "U01-1", "U01-3"]      # PC 1, 0, 2


def test_import_clamps_a_patch_change_before_the_music_to_bar_one(parsed):
    data, notes = import_into_song(parsed, _song(), program_map=ProgramMap())
    assert data["patch_changes"][0].bar == 1
    assert any("program change" in n for n in notes)


def test_import_respects_an_explicit_offset(parsed):
    data, _ = import_into_song(parsed, _song(), offset=0.0)
    assert data["sections"][0].bar > 40      # everything shifts if zero is wrong


def test_import_reports_the_media_and_the_pedal_feed(parsed):
    data, notes = import_into_song(parsed, _song())
    assert data["media"] == ["I Phooffi_base_2.wav"]
    assert any("pedalboard feed" in n for n in notes)


def test_import_estimates_the_length_in_bars(parsed):
    data, _ = import_into_song(parsed, _song())
    assert data["bars"] > 100                # ~437 s of music at 90 BPM


def test_import_writes_nothing_by_itself(parsed, tmp_path):
    song = _song()
    before = song.bpm
    import_into_song(parsed, song)
    assert song.bpm == before


def test_parsing_an_empty_project_does_not_crash():
    parsed = parse_rpp("REAPER_PROJECT 0.1 \"7.0\" 1\n>\n")
    assert parsed.tracks == []
    assert parsed.markers == []
    assert parsed.program_changes() == []
