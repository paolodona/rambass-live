"""Drum maps: canonical names in, note numbers out."""

from __future__ import annotations

import pytest

from rambass.drummap import CANONICAL, GENERAL_MIDI, GM_NOTES, load_drum_map
from rambass.project import ProjectError


def test_general_midi_matches_the_spec():
    assert GENERAL_MIDI.note_for("kick") == 36
    assert GENERAL_MIDI.note_for("snare") == 38
    assert GENERAL_MIDI.note_for("hihat_closed") == 42
    assert GENERAL_MIDI.note_for("hihat_open") == 46
    assert GENERAL_MIDI.note_for("crash") == 49
    assert GENERAL_MIDI.note_for("ride") == 51
    assert GENERAL_MIDI.midi_channel_index == 9


def test_every_canonical_name_has_a_general_midi_note():
    assert set(CANONICAL) == set(GM_NOTES)
    for instrument in CANONICAL:
        assert 0 <= GENERAL_MIDI.note_for(instrument) <= 127


def test_note_numbers_are_unique_in_general_midi():
    assert len(set(GM_NOTES.values())) == len(GM_NOTES)


def test_unknown_instrument_is_an_error():
    with pytest.raises(ProjectError, match="unknown drum instrument"):
        GENERAL_MIDI.note_for("kazoo")


def test_reverse_lookup():
    assert GENERAL_MIDI.instrument_for(38) == "snare"
    assert GENERAL_MIDI.instrument_for(3) == "note3"


def test_custom_map_falls_back_to_general_midi_for_blanks(project):
    electro = load_drum_map("electro", project)
    assert electro.note_for("kick") == 60          # overridden
    assert electro.note_for("hihat_closed") == 42  # left null -> GM
    assert electro.note_for("ride") == 51          # absent -> GM


def test_gm_aliases_resolve_to_the_builtin(project):
    for alias in ("", "gm", "general-midi"):
        assert load_drum_map(alias, project) is GENERAL_MIDI


def test_missing_map_lists_what_exists(project):
    with pytest.raises(ProjectError, match="electro"):
        load_drum_map("nonesuch", project)


def test_map_with_an_out_of_range_note_is_rejected(project):
    (project.drum_maps_dir / "broken.yaml").write_text(
        "name: broken\nnotes:\n  kick: 200\n", encoding="utf-8"
    )
    with pytest.raises(ProjectError, match="outside 0-127"):
        load_drum_map("broken", project)


def test_map_with_a_bad_channel_is_rejected(project):
    (project.drum_maps_dir / "badch.yaml").write_text(
        "name: badch\nchannel: 0\nnotes: {kick: 36}\n", encoding="utf-8"
    )
    with pytest.raises(ProjectError, match="channel must be"):
        load_drum_map("badch", project)


def test_the_shipped_maps_all_load():
    """The real config/ in this repo, not the fixture."""
    from rambass.project import Project

    real = Project.discover()
    for path in sorted(real.drum_maps_dir.glob("*.yaml")):
        loaded = load_drum_map(path.stem, real) if not path.stem.startswith("_") else None
        if loaded is not None:
            assert loaded.notes or loaded.name
