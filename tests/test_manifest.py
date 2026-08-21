"""Manifest round-tripping, validation and song lookup."""

from __future__ import annotations

import pytest
import yaml

from rambass.manifest import Song, load_song, save_song
from rambass.project import ProjectError, slugify


def test_slugify_folds_italian_accents():
    assert slugify("Perché No") == "perche-no"
    assert slugify("L'Esercito Del Surf") == "l-esercito-del-surf"
    assert slugify("  Tutti In Fila!  ") == "tutti-in-fila"


def test_round_trip_preserves_everything(song, project):
    reloaded = load_song(song.dir)
    assert reloaded.title == song.title
    assert reloaded.bpm == song.bpm
    assert reloaded.count_in_bars == song.count_in_bars
    assert [s.name for s in reloaded.sections] == ["intro", "verse", "chorus"]
    assert reloaded.album == "tutti-in-fila"


def test_album_is_inferred_from_the_folder(song):
    data = yaml.safe_load((song.dir / "song.yaml").read_text())
    del data["album"]
    (song.dir / "song.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    assert load_song(song.dir).album == "tutti-in-fila"


def test_save_creates_the_song_subfolders(song):
    for sub in ("source", "stems", "midi", "render", "video"):
        assert (song.dir / sub).is_dir()


def test_find_song_dir_accepts_several_forms(project, song):
    for reference in (
        "03-tutti-in-fila",
        "tutti-in-fila/03-tutti-in-fila",
        str(song.dir),
    ):
        assert project.find_song_dir(reference) == song.dir.resolve()


def test_find_song_dir_reports_a_miss(project, song):
    with pytest.raises(ProjectError, match="no song matching"):
        project.find_song_dir("non-esiste")


def test_rejects_bad_drum_origin():
    with pytest.raises(ProjectError, match="drums.origin"):
        Song.from_dict({"title": "x", "drums": {"origin": "magic"}})


def test_rejects_invalid_gx100_memory():
    with pytest.raises(ProjectError, match="not a GX-100 memory"):
        Song.from_dict({
            "title": "x",
            "gx100": {"changes": [{"bar": 1, "memory": "U99-9"}]},
        })


def test_problems_flags_duplicate_sections_and_bad_channel():
    from rambass.manifest import Section

    item = Song.from_dict({"title": "x"})
    # Set the bad state directly: `from_dict` validates, and here we want to
    # exercise `problems()` on its own.
    item.sections = [Section("a", 5), Section("b", 5)]
    item.gx100_channel = 42
    problems = item.problems()
    assert any("both at bar 5" in p for p in problems)
    assert any("gx100.channel" in p for p in problems)


def test_total_bars_falls_back_to_sections():
    item = Song.from_dict({"title": "x", "sections": [{"name": "end", "bar": 40}]})
    assert item.bars == 0
    assert item.total_bars() == 48


def test_progress_ignores_not_applicable_stages(song):
    song.status["stems"] = "n/a"
    song.status["source"] = "done"
    done, total = song.progress()
    assert (done, total) == (1, 9)


def test_section_at_returns_the_current_section(song):
    assert song.section_at(1).name == "intro"
    assert song.section_at(12).name == "verse"
    assert song.section_at(99).name == "chorus"


def test_unknown_keys_survive_a_round_trip(song):
    data = yaml.safe_load((song.dir / "song.yaml").read_text())
    data["mixer_notes"] = "kick a bit loud"
    (song.dir / "song.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    reloaded = load_song(song.dir)
    assert reloaded.extra["mixer_notes"] == "kick a bit loud"
    save_song(reloaded)
    assert "mixer_notes" in yaml.safe_load((song.dir / "song.yaml").read_text())


def test_source_url_round_trips(song):
    song.source_url = "https://drive.google.com/file/d/abc/view"
    save_song(song)
    assert load_song(song.dir).source_url.endswith("/abc/view")
