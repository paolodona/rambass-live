"""Checking a running order against set-list practice."""

from __future__ import annotations

from rambass.arrange import review, sparkline, table
from rambass.manifest import Character, Song


def _song(title, energy, standing="known", role="", genre="rock", note=""):
    item = Song.from_dict({"title": title})
    item.character = Character(
        genre=genre, energy=energy, heaviness=3, standing=standing,
        role=role, note=note,
    )
    return item


def _good_set() -> list[Song]:
    """A short order that respects the principles."""
    return [
        _song("Opener", 4, "hit"),
        _song("Second", 5, "known", genre="metal"),
        _song("Funky", 4, "known", genre="funk"),
        _song("Ballad", 1, "known", genre="ballad"),
        _song("NewOne", 2, "new", genre="ballad"),
        _song("Lift", 4, "known", genre="pop"),
        _song("Heaviest", 5, "known", genre="metal"),
        _song("Closer", 4, "hit", role="closer"),
    ]


def _problems(songs):
    return [f for f in review(songs) if f.severity == "problem"]


def test_a_sound_order_passes():
    assert review(_good_set()) == []


def test_flags_an_unknown_opener():
    songs = _good_set()
    songs[0] = _song("Deep cut", 4, "deep")
    assert any("opening with a deep song" in f.what for f in _problems(songs))


def test_flags_a_quiet_opener():
    songs = _good_set()
    songs[0] = _song("Quiet", 1, "hit")
    assert any("statement of intent" in f.what for f in _problems(songs))


def test_skips_a_linking_piece_when_judging_the_opener():
    songs = [_song("Intro", 2, "deep", role="linking"), *_good_set()]
    assert not any("opening" in f.what for f in _problems(songs))


def test_flags_an_unknown_closer():
    songs = _good_set()
    songs[-1] = _song("Deep cut", 4, "deep")
    assert any("closing on a deep song" in f.what for f in _problems(songs))


def test_flags_a_declared_closer_that_is_not_last():
    songs = _good_set()
    songs.append(_song("After", 4, "hit"))
    assert any("marked as the closer but is not last" in f.what for f in _problems(songs))


def test_flags_four_songs_at_the_same_energy():
    songs = [
        _song("A", 4, "hit"), _song("B", 3), _song("C", 3), _song("D", 3),
        _song("E", 3), _song("F", 1), _song("G", 5),
        _song("H", 4, "hit", role="closer"),
    ]
    found = _problems(songs)
    assert any("4 songs at energy 3 in a row" in f.what for f in found)


def test_three_at_the_same_energy_is_allowed_when_the_feels_differ():
    songs = [
        _song("A", 4, "hit"),
        _song("B", 3, genre="ambient"),
        _song("C", 3, genre="jazz"),
        _song("D", 3, genre="pop"),
        _song("E", 1), _song("F", 5),
        _song("G", 4, "hit", role="closer"),
    ]
    assert not any("energy 3" in f.what for f in review(songs))


def test_three_at_the_same_energy_is_flagged_when_they_feel_alike():
    songs = [
        _song("A", 4, "hit"),
        _song("B", 3, genre="rock"),
        _song("C", 3, genre="rock"),
        _song("D", 3, genre="rock"),
        _song("E", 1), _song("F", 5),
        _song("G", 4, "hit", role="closer"),
    ]
    assert any("distinct feel" in f.what for f in review(songs))


def test_flags_two_ballads_back_to_back():
    songs = _good_set()
    songs[4] = _song("Another ballad", 1, "known", genre="ballad")
    assert any("two ballads back to back" in f.what for f in _problems(songs))


def test_flags_no_hit_at_the_front():
    songs = _good_set()
    songs[0] = _song("Known", 4, "known")
    assert any("no hit in the opening stretch" in f.what for f in _problems(songs))


def test_flags_a_plateau():
    songs = [_song(f"S{i}", 3, "hit") for i in range(6)]
    songs[-1].character.role = "closer"
    found = _problems(songs)
    assert any("no arc, just a plateau" in f.what for f in found)


def test_flags_a_set_with_no_breather():
    songs = [
        _song("A", 4, "hit"), _song("B", 5, genre="metal"), _song("C", 3),
        _song("D", 5, genre="punk"), _song("E", 4, "hit", role="closer"),
    ]
    assert any("never gets to breathe" in f.what for f in _problems(songs))


def test_flags_a_set_that_never_returns_to_its_peak():
    songs = [
        _song("A", 5, "hit"), _song("B", 5, genre="metal"), _song("C", 1),
        _song("D", 3), _song("E", 3, genre="pop"),
        _song("F", 4, "hit", role="closer"),
    ]
    assert any("never returns to its highest energy" in f.what for f in review(songs))


def test_flags_a_stranded_linking_piece():
    songs = _good_set()
    songs.append(_song("Intro X", 2, "deep", role="linking",
                       note="runs straight into the next one"))
    assert any("linking piece cannot be last" in f.what for f in _problems(songs))


def test_flags_unfamiliar_material_left_at_the_ends():
    songs = _good_set()
    songs[1] = _song("Deep", 5, "deep", genre="metal")
    assert any("does better in the middle" in f.what for f in review(songs))


def test_empty_set():
    assert review([])[0].what == "the set is empty"


def test_sparkline_and_table_render():
    songs = _good_set()
    line = sparkline(songs)
    assert len(line) == len(songs)
    text = table(songs)
    assert "Opener" in text and "energy" in text


def test_the_repos_own_setlists_pass_the_review():
    """Guards the real running orders in this checkout."""
    from pathlib import Path

    from rambass.project import Project
    from rambass.setlist import Setlist

    project = Project.discover(Path(__file__).resolve().parent)
    for path in sorted(project.setlists_dir.glob("*.y*ml")):
        songs = Setlist.load(path).resolve(project)
        problems = [f for f in review(songs) if f.severity == "problem"]
        assert problems == [], f"{path.name}: {[f.what for f in problems]}"
