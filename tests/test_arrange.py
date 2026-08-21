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


def test_the_repos_own_proposed_setlists_pass_the_review():
    """Guards the real running orders in this checkout.

    Historical setlists are exempt: they record what was played, and the 2023 one
    opened with the signature song, which is exactly what the checker now flags.
    """
    from pathlib import Path

    from rambass.project import Project
    from rambass.setlist import Setlist

    project = Project.discover(Path(__file__).resolve().parent)
    checked = 0
    for path in sorted(project.setlists_dir.glob("*.y*ml")):
        setlist = Setlist.load(path)
        songs = setlist.resolve(project)
        problems = [f for f in review(songs) if f.severity == "problem"]
        if setlist.historical:
            continue
        assert problems == [], f"{path.name}: {[f.what for f in problems]}"
        checked += 1
    assert checked >= 2, "expected at least the gig and tier-a setlists"


def test_the_2023_set_shows_the_mistake_it_made():
    """The checker has to be able to fault a real set, or it proves nothing."""
    from pathlib import Path

    from rambass.project import Project
    from rambass.setlist import Setlist

    project = Project.discover(Path(__file__).resolve().parent)
    setlist = Setlist.load(project.setlists_dir / "live-2023.yaml")
    assert setlist.historical is True
    problems = [f for f in review(setlist.resolve(project)) if f.severity == "problem"]
    assert any("song people came for" in f.what for f in problems)


# ── the signature song ───────────────────────────────────────────────────
def _long_set(signature_at: int) -> list[Song]:
    """A twelve-song order with the signature song placed where asked."""
    songs = [
        _song("Open", 4, "hit"),
        _song("Two", 5, "known", genre="metal"),
        _song("Three", 4, "known", genre="funk"),
        _song("Four", 1, "known", genre="ballad"),
        _song("Five", 4, "known", genre="pop"),
        _song("Six", 3, "known", genre="ambient"),
        _song("Seven", 2, "new", genre="ballad"),
        _song("Eight", 3, "known", genre="jazz"),
        _song("Nine", 4, "known", genre="rap"),
        _song("Ten", 5, "known", genre="metal"),
        _song("Eleven", 4, "known", genre="rock"),
        _song("Closer", 4, "hit", role="closer"),
    ]
    songs[signature_at].character.standing = "signature"
    return songs


def test_the_signature_song_must_not_open():
    """Metallica do not open with Enter Sandman."""
    songs = _long_set(0)
    found = _problems(songs)
    assert any("song people came for" in f.what for f in found)
    assert any("closing run" in f.what for f in found)


def test_the_signature_song_in_the_middle_is_only_a_watch():
    findings = review(_long_set(5))
    assert not [f for f in findings if f.severity == "problem"]
    assert any("wants the last quarter" in f.what for f in findings)


def test_the_signature_song_in_the_closing_run_passes():
    assert review(_long_set(10)) == []


def test_a_signature_song_counts_as_a_crowd_pleaser_at_the_ends():
    songs = _long_set(10)
    songs[0].character.standing = "known"        # no hit at the front any more
    songs[1].character.standing = "signature"    # but a signature is
    found = _problems(songs)
    assert not any("no hit in the opening stretch" in f.what for f in found)


def test_more_than_one_signature_song_is_questioned():
    songs = _long_set(10)
    songs[9].character.standing = "signature"
    assert any("nothing is" in f.what for f in review(songs))


def test_opening_on_the_heaviest_thing_is_questioned():
    songs = _long_set(10)
    songs[0] = _song("Brutal", 5, "hit", genre="death metal")
    songs[0].character.heaviness = 5
    assert any("not the fastest or heaviest" in f.what for f in review(songs))


def test_a_fast_but_light_opener_is_fine():
    """Energy 5 alone is not the problem — energy 5 plus maximum weight is."""
    songs = _long_set(10)
    songs[0] = _song("Happy", 5, "hit", genre="power pop")
    songs[0].character.heaviness = 2
    assert not any("heaviest" in f.what for f in review(songs))
