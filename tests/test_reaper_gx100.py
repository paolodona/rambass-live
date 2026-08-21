"""Reaper build scripts and GX-100 patch changes."""

from __future__ import annotations

import mido
import pytest

from rambass.gx100 import (
    MAX_BANK,
    TOTAL_MEMORIES,
    ProgramMap,
    index_to_memory,
    load_program_map,
    memory_to_index,
    patch_sheet,
    resolve_patch_changes,
    write_patch_midi,
)
from rambass.manifest import PatchChange, save_song
from rambass.project import ProjectError
from rambass.reaper import build_setlist_script, build_song_script, project_length_seconds


# ── build script ─────────────────────────────────────────────────────────
def _records(script, kind):
    return [r for r in script.records if r[0] == kind]


def test_build_script_starts_the_tempo_at_time_zero(song):
    """Otherwise the count-in would sit at Reaper's default tempo."""
    script = build_song_script(song)
    tempos = _records(script, "TEMPO")
    assert tempos[0] == ("TEMPO", "0", "120", "4", "4")


def test_build_script_places_markers_in_audio_time(song):
    script = build_song_script(song)
    markers = {r[2]: float(r[1]) for r in _records(script, "MARKER")}
    assert markers["BAR 1"] == pytest.approx(4.0)      # two bars of count-in
    assert markers["9. verse"] == pytest.approx(4.0 + 16.0)


def test_build_script_regions_span_to_the_next_section(song):
    script = build_song_script(song)
    regions = {r[3]: (float(r[1]), float(r[2])) for r in _records(script, "REGION")}
    assert regions["intro"] == (pytest.approx(4.0), pytest.approx(20.0))
    # the last section runs to the end of the song (32 bars + count-in)
    assert regions["chorus"][1] == pytest.approx(4.0 + 64.0)


def test_build_script_skips_media_that_is_not_there(song):
    script = build_song_script(song)
    assert not _records(script, "ITEM")
    assert not _records(script, "MIDI")


def test_build_script_places_the_two_stems_on_their_own_tracks(song):
    """Sticks at zero; the click starts with the song, so they never overlap."""
    sticks = song.path("render", "sticks.wav")
    click = song.path("render", "click.wav")
    base = song.path("render", "base.wav")
    for path in (sticks, click, base):
        path.write_bytes(b"RIFF")

    script = build_song_script(song, sticks_wav=sticks, click_wav=click, backing_wav=base)
    placed = {r[1]: float(r[3]) for r in _records(script, "ITEM")}
    assert placed["STICKS"] == pytest.approx(0.0)
    assert placed["CLICK"] == pytest.approx(4.0)      # two bars of count-in at 120
    assert placed["BACKING"] == pytest.approx(4.0)


def test_build_script_mutes_the_click_but_not_the_sticks(song):
    """The click must never reach front of house; the sticks may."""
    script = build_song_script(song)
    muted = {r[1] for r in _records(script, "MUTE")}
    assert "CLICK" in muted
    assert "STICKS" not in muted
    assert "BACKING" not in muted


def test_setlist_script_also_mutes_the_click(song):
    script = build_setlist_script([song])
    assert ("MUTE", "CLICK", "1") in script.records
    names = [r[1] for r in _records(script, "TRACK")]
    assert names[:3] == ["STICKS", "BACKING", "CLICK"]


def test_build_script_can_leave_out_the_reference_tracks(song):
    lean = build_song_script(song, include_reference=False)
    names = [r[1] for r in _records(lean, "TRACK")]
    assert "CLICK" in names and "DRUMS MIDI" in names
    assert not any(n.startswith("REF") for n in names)


def test_build_script_rejects_tabs_in_a_field(song):
    song.title = "bad\ttitle"
    with pytest.raises(ValueError, match="tabs"):
        build_song_script(song)


def test_build_script_renders_with_a_header(song):
    text = build_song_script(song).render("hello")
    assert text.startswith("# rambass reaper build script v1")
    assert "# hello" in text
    assert text.endswith("\n")


def test_project_length_includes_the_count_in(song):
    assert project_length_seconds(song) == pytest.approx(4.0 + 64.0)


def test_setlist_script_lays_songs_out_end_to_end(song):
    script = build_setlist_script([song, song], gap_seconds=2.0)
    regions = _records(script, "REGION")
    assert len(regions) == 2
    first_end = float(regions[0][2])
    second_start = float(regions[1][1])
    assert second_start == pytest.approx(first_end + 2.0)


# ── GX-100 memory arithmetic ─────────────────────────────────────────────
@pytest.mark.parametrize(
    ("memory", "index"),
    [("U01-1", 0), ("U01-4", 3), ("U02-1", 4), ("U32-4", 127),
     ("U33-1", 128), ("U50-4", 199), ("P01-1", 200), ("P25-4", 299)],
)
def test_memory_index_round_trip(memory, index):
    assert memory_to_index(memory) == index
    assert index_to_memory(index) == memory


@pytest.mark.parametrize("bad", ["U51-1", "U01-5", "P26-1", "X01-1", "U1", "", "U01"])
def test_rejects_impossible_memory_names(bad):
    with pytest.raises(ProjectError):
        memory_to_index(bad)


def test_index_to_memory_covers_exactly_the_addressable_range():
    assert index_to_memory(TOTAL_MEMORIES - 1) == "P25-4"
    with pytest.raises(ProjectError):
        index_to_memory(TOTAL_MEMORIES)


def test_sequential_program_map_slots():
    program_map = ProgramMap()
    assert program_map.slot_for("U01-1") == (0, 0)
    assert program_map.slot_for("U32-4") == (0, 127)
    assert program_map.slot_for("U33-1") == (1, 0)
    assert program_map.slot_for("P25-4") == (MAX_BANK, 43)


def test_explicit_assignments_win_over_the_sequential_default():
    program_map = ProgramMap(assignments={(2, 7): "U01-1"})
    assert program_map.slot_for("U01-1") == (2, 7)
    assert program_map.memory_for(2, 7) == "U01-1"


def test_strict_map_refuses_unlisted_memories():
    program_map = ProgramMap(assignments={(0, 0): "U01-1"}, sequential_default=False)
    with pytest.raises(ProjectError, match="not in the GX-100 program map"):
        program_map.slot_for("U09-2")


def test_load_program_map_reads_config(project):
    (project.config_dir / "gx100.yaml").write_text(
        "channel: 5\nsequential_default: false\n"
        "assignments:\n  - {bank: 1, pc: 9, memory: u04-2}\n",
        encoding="utf-8",
    )
    program_map = load_program_map(project)
    assert program_map.channel == 5
    assert program_map.sequential_default is False
    assert program_map.slot_for("U04-2") == (1, 9)


def test_load_program_map_rejects_an_out_of_range_bank(project):
    (project.config_dir / "gx100.yaml").write_text(
        "assignments:\n  - {bank: 9, pc: 0, memory: U01-1}\n", encoding="utf-8"
    )
    with pytest.raises(ProjectError, match="bank must be"):
        load_program_map(project)


# ── GX-100 MIDI output ───────────────────────────────────────────────────
def test_patch_midi_emits_bank_select_then_program_change(song, tmp_path):
    song.patch_changes = [
        PatchChange(bar=1, memory="U01-1", name="clean"),
        PatchChange(bar=9, memory="U33-1", name="lead"),
    ]
    save_song(song)
    path, events = write_patch_midi(tmp_path / "gx.mid", song, ProgramMap())

    assert [e.bank for e in events] == [0, 1]
    assert [e.program for e in events] == [0, 0]

    messages = [m for m in mido.MidiFile(path).tracks[0] if not m.is_meta]
    kinds = [(m.type, getattr(m, "control", None), getattr(m, "value", None),
              getattr(m, "program", None)) for m in messages]
    assert kinds[0] == ("control_change", 0, 0, None)     # bank MSB
    assert kinds[1] == ("control_change", 32, 0, None)    # bank LSB, always 0
    assert kinds[2][0] == "program_change"
    assert kinds[3] == ("control_change", 0, 1, None)     # second change, bank 1


def test_patch_midi_uses_the_songs_channel(song, tmp_path):
    song.gx100_channel = 4
    song.patch_changes = [PatchChange(bar=1, memory="U01-1")]
    path, _ = write_patch_midi(tmp_path / "gx.mid", song, ProgramMap())
    channels = {m.channel for m in mido.MidiFile(path).tracks[0] if not m.is_meta}
    assert channels == {3}


def test_patch_changes_are_sent_early_but_never_before_zero(song, tmp_path):
    song.patch_changes = [
        PatchChange(bar=1, memory="U01-1"),
        PatchChange(bar=5, memory="U01-2"),
    ]
    _, events = write_patch_midi(
        tmp_path / "gx.mid", song, ProgramMap(), lead_ms=200.0
    )
    timeline = song.timeline()
    # events carry the *musical* position; the lead is applied when writing ticks
    assert events[1].seconds == pytest.approx(timeline.bar_beat_to_seconds(5))

    path, _ = write_patch_midi(tmp_path / "g2.mid", song, ProgramMap(), lead_ms=200.0)
    midi = mido.MidiFile(path)
    ticks = [m.time for m in midi.tracks[0] if m.type == "program_change"]
    assert ticks[0] == 0        # bar 1 cannot be pulled earlier than the start


def test_resolve_patch_changes_sorts_by_bar(song):
    song.patch_changes = [
        PatchChange(bar=17, memory="U02-1"),
        PatchChange(bar=1, memory="U01-1"),
    ]
    bars = [e.bar for e in resolve_patch_changes(song, ProgramMap())]
    assert bars == [1, 17]


def test_patch_sheet_lists_every_change(song):
    song.patch_changes = [PatchChange(bar=9, memory="U02-3", name="crunch")]
    text = patch_sheet([song], ProgramMap())
    assert "Tutti In Fila" in text
    assert "`U02-3`" in text
    assert "crunch" in text


def test_patch_sheet_says_so_when_nothing_is_programmed(song):
    assert "no patch changes programmed yet" in patch_sheet([song], ProgramMap())


def test_describe_surfaces_the_notes_and_the_backing_track(song):
    from rambass.reaper import describe

    song.notes = "base review: signed off. Missing the count-in."
    song.backing_track = "Il Phurgone_Mix_2 BASE.wav"
    text = describe(song.timeline(), song)
    assert "notes" in text
    assert "Missing the count-in" in text
    assert "Il Phurgone_Mix_2 BASE.wav" in text
    assert "not on disk yet" in text
