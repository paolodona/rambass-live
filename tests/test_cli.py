"""End-to-end CLI runs against a throwaway project."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rambass.cli import main
from rambass.manifest import load_song


@pytest.fixture
def cwd(project, monkeypatch):
    monkeypatch.chdir(project.root)
    return project


def run(*args) -> int:
    return main(list(args))


def test_help_with_no_command_is_not_a_crash(cwd, capsys):
    assert run() == 1
    assert "usage: rambass" in capsys.readouterr().out


def test_doctor_reports(cwd, capsys):
    run("doctor")
    out = capsys.readouterr().out
    assert "rambass doctor" in out
    assert "mido" in out


def test_new_scaffolds_a_song(cwd, capsys):
    assert run("new", "Ampiamente Contestabile", "--album", "tutti-in-fila",
               "--create-album", "--bpm", "132", "--bars", "48") == 0
    directory = cwd.songs_dir / "tutti-in-fila" / "01-ampiamente-contestabile"
    assert (directory / "song.yaml").is_file()
    assert (directory / "lyrics.md").is_file()
    for sub in ("source", "stems", "midi", "render", "video"):
        assert (directory / sub).is_dir()
    song = load_song(directory)
    assert song.bpm == 132 and song.bars == 48


def test_new_picks_the_next_track_number(cwd):
    run("new", "Uno", "--album", "a", "--create-album")
    run("new", "Due", "--album", "a")
    names = sorted(p.name for p in (cwd.songs_dir / "a").iterdir() if p.is_dir())
    assert names == ["01-uno", "02-due"]


def test_new_refuses_an_unknown_album(cwd, capsys):
    assert run("new", "X", "--album", "mancante") == 2
    assert "no album" in capsys.readouterr().err


def test_new_marks_stems_not_applicable_for_recorded_drums(cwd):
    run("new", "X", "--album", "dg", "--create-album", "--drums", "recorded")
    song = load_song(cwd.songs_dir / "dg" / "01-x")
    assert song.status["stems"] == "n/a"


def test_list_show_status_and_check(cwd, capsys):
    run("new", "Manlio", "--album", "tutti-in-fila", "--create-album", "--bpm", "128")
    capsys.readouterr()

    assert run("list") == 0
    assert "Manlio" in capsys.readouterr().out

    assert run("show", "01-manlio") == 0
    out = capsys.readouterr().out
    assert "128 BPM" in out and "count-in" in out

    assert run("status") == 0
    assert "next up" in capsys.readouterr().out

    # no source audio yet, so check fails and says where to get it
    assert run("check") == 1
    assert "no audio in source/" in capsys.readouterr().out

    assert run("check", "--no-audio-check") == 0
    assert "consistent" in capsys.readouterr().out


def test_section_and_patch_edit_the_manifest(cwd, capsys):
    run("new", "Superman", "--album", "dg", "--create-album")
    capsys.readouterr()
    assert run("section", "01-superman", "9", "verse") == 0
    assert run("patch", "01-superman", "9", "u02-3", "--name", "crunch") == 0
    song = load_song(cwd.songs_dir / "dg" / "01-superman")
    assert [(s.bar, s.name) for s in song.sections] == [(1, "intro"), (9, "verse")]
    assert song.patch_changes[0].memory == "U02-3"      # upper-cased on the way in


def test_patch_replaces_a_change_at_the_same_bar(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album")
    run("patch", "01-x", "5", "U01-1")
    run("patch", "01-x", "5", "U02-2")
    capsys.readouterr()
    song = load_song(cwd.songs_dir / "dg" / "01-x")
    assert len(song.patch_changes) == 1
    assert song.patch_changes[0].memory == "U02-2"


def test_patch_rejects_an_impossible_memory(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album")
    capsys.readouterr()
    assert run("patch", "01-x", "1", "U99-9") == 2
    assert "GX-100 memory" in capsys.readouterr().err


def test_mark_sets_a_stage(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album")
    capsys.readouterr()
    assert run("mark", "01-x", "render", "done") == 0
    assert load_song(cwd.songs_dir / "dg" / "01-x").status["render"] == "done"


def test_click_gx100_and_reaper_build(cwd, capsys):
    run("new", "Bambolina", "--album", "dg", "--create-album", "--bpm", "120",
        "--bars", "16")
    run("patch", "01-bambolina", "1", "U01-1")
    capsys.readouterr()

    assert run("click", "01-bambolina") == 0
    click = cwd.songs_dir / "dg" / "01-bambolina" / "render" / "click.wav"
    assert click.is_file() and click.stat().st_size > 1000

    assert run("gx100", "midi", "01-bambolina") == 0
    assert (cwd.songs_dir / "dg" / "01-bambolina" / "midi" / "gx100.mid").is_file()

    assert run("reaper", "build", "01-bambolina") == 0
    script = cwd.reaper_build_dir / "dg-bambolina.rbs"
    assert script.is_file()
    text = script.read_text()
    assert "TEMPO\t0\t120\t4\t4" in text
    assert "ITEM\tCLICK" in text          # the click we just rendered got placed
    assert "MIDI\tGX-100 MIDI" in text


def test_gx100_midi_says_so_when_there_is_nothing_to_send(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album")
    capsys.readouterr()
    assert run("gx100", "midi", "01-x") == 0
    assert "nothing to send" in capsys.readouterr().out


def test_gx100_map_prints_the_table(cwd, capsys):
    assert run("gx100", "map", "--limit", "5") == 0
    out = capsys.readouterr().out
    assert "U01-1" in out and "U02-1" in out


def test_video_ass_is_written_from_the_lyrics_file(cwd, capsys):
    run("new", "Orologiaio", "--album", "dg", "--create-album", "--bpm", "120",
        "--bars", "32")
    directory = cwd.songs_dir / "dg" / "01-orologiaio"
    (directory / "lyrics.md").write_text("[bar 9]\nUna riga\n", encoding="utf-8")
    capsys.readouterr()

    assert run("video", "ass", "01-orologiaio") == 0
    text = (directory / "video" / "orologiaio.ass").read_text()
    assert "Una riga" in text
    assert "0:00:20.00" in text          # bar 9 with a two-bar count-in at 120


def test_setlist_running_order(cwd, capsys):
    run("new", "A", "--album", "dg", "--create-album", "--bars", "16")
    run("new", "B", "--album", "dg", "--bars", "16")
    (cwd.setlists_dir / "gig.yaml").write_text(
        "name: Prova\nsongs: [01-a, 02-b]\n", encoding="utf-8"
    )
    capsys.readouterr()
    assert run("setlist", "gig") == 0
    out = capsys.readouterr().out
    assert "# Prova" in out and "2 songs" in out


def test_setlist_out_writes_a_file(cwd, tmp_path, capsys):
    run("new", "A", "--album", "dg", "--create-album", "--bars", "16")
    (cwd.setlists_dir / "gig.yaml").write_text(
        "name: Prova\nsongs: [01-a]\n", encoding="utf-8"
    )
    target = tmp_path / "order.md"
    assert run("setlist", "gig", "--out", str(target)) == 0
    assert "# Prova" in target.read_text()


def test_all_flag_operates_on_every_song(cwd, capsys):
    run("new", "A", "--album", "dg", "--create-album", "--bars", "8")
    run("new", "B", "--album", "dg", "--bars", "8")
    capsys.readouterr()
    assert run("click", "--all") == 0
    for slug in ("01-a", "02-b"):
        assert (cwd.songs_dir / "dg" / slug / "render" / "click.wav").is_file()


def test_naming_no_song_without_all_is_an_error(cwd, capsys):
    assert run("click") == 2
    assert "name a song" in capsys.readouterr().err


def test_ambiguous_reference_is_reported(cwd, capsys):
    run("new", "Doppio", "--album", "a", "--create-album")
    run("new", "Doppio", "--album", "b", "--create-album")
    capsys.readouterr()
    assert run("show", "01-doppio") == 2
    assert "ambiguous" in capsys.readouterr().err


def test_audio_commands_explain_the_missing_extra(cwd, capsys, monkeypatch):
    """`analyze` needs librosa; without it the user gets an install hint."""
    run("new", "X", "--album", "dg", "--create-album")
    directory = cwd.songs_dir / "dg" / "01-x"
    (directory / "source" / "mix.wav").write_bytes(b"RIFF----WAVE")
    capsys.readouterr()

    import builtins

    real_import = builtins.__import__

    def no_librosa(name, *args, **kwargs):
        if name == "librosa":
            raise ImportError("no librosa")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_librosa)
    assert run("analyze", "01-x") == 2
    assert "pip install -e '.[audio]'" in capsys.readouterr().err


def test_the_repos_own_songs_and_setlists_are_valid():
    """Guards the 26 real song.yaml files in this checkout."""
    from rambass.project import Project
    from rambass.setlist import Setlist

    real = Project.discover(Path(__file__).resolve().parent)
    songs = [load_song(d) for d in real.song_dirs()]
    assert len(songs) >= 26
    for song in songs:
        assert song.problems() == [], f"{song.album}/{song.slug}: {song.problems()}"
    for path in sorted(real.setlists_dir.glob("*.y*ml")):
        Setlist.load(path).resolve(real)


def test_env_does_not_leak_between_tests(cwd):
    assert Path(os.getcwd()) == cwd.root
