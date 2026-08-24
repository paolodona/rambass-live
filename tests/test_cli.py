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


@pytest.mark.parametrize(
    "group, expected",
    [("review", "serve"), ("drums", "transcribe"), ("lyrics", "import"),
     ("video", "render"), ("gx100", "midi"), ("reaper", "build")],
)
def test_a_bare_command_group_lists_its_own_subcommands(cwd, capsys, group,
                                                        expected):
    """Typing `rambass review` printed the *top-level* usage, which lists every
    command except the six under `review` -- so the console looked like it did
    not exist. The help you get has to be the help for what you typed."""
    assert run(group) == 1
    out = capsys.readouterr().out
    assert f"usage: rambass {group}" in out
    assert expected in out


def test_every_command_the_code_names_is_a_command_that_exists():
    """`rambass review serve` printed "rambass console at http://..." -- a name
    the shell rejects, for a reader who then hunts for a command that never
    existed. Docs are excluded on purpose: `docs/practice-tracks.md` and
    `docs/review-ui.md` describe commands that are deliberately not built yet.
    Anything printed or documented *by the code* has to resolve."""
    import argparse
    import re

    from rambass.cli import build_parser

    def subcommands(parser):
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                return action.choices
        return {}

    groups = subcommands(build_parser())
    src = Path(__file__).resolve().parent.parent / "src" / "rambass"
    wrong = []
    for path in sorted(src.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"rambass ([a-z][a-z0-9-]*)(?:\s+([a-z][a-z0-9-]*))?", text):
            group, sub = match.group(1), match.group(2)
            if group not in groups:
                wrong.append(f"{path.name}: rambass {group}")
            elif sub and subcommands(groups[group]) and sub not in subcommands(groups[group]):
                wrong.append(f"{path.name}: rambass {group} {sub}")
    assert wrong == []


# ── starting the console ─────────────────────────────────────────────────────
#
# Paolo: *"ok so I do I start it? 'rambass review serve'? maybe a shortcut
# 'rambass-console' or 'rambass-dashboard' would be useful"*. `review serve` is
# where it lives in the command tree and stays; the console is the thing you
# open at the start of a session, so it gets a name you can type without
# remembering which group it is filed under.


def test_console_is_a_top_level_alias_for_review_serve(cwd):
    from rambass.cli import build_parser, cmd_review_serve

    parser = build_parser()
    short = parser.parse_args(["console"])
    full = parser.parse_args(["review", "serve"])
    assert short.func is cmd_review_serve
    assert full.func is cmd_review_serve
    for flag in ("port", "setlist", "no_browser"):
        assert getattr(short, flag) == getattr(full, flag), flag


def test_the_console_alias_takes_the_same_flags(cwd):
    from rambass.cli import build_parser

    args = build_parser().parse_args(
        ["console", "--port", "9111", "--setlist", "rehearsal", "--no-browser"])
    assert (args.port, args.setlist, args.no_browser) == (9111, "rehearsal", True)


def test_the_console_executable_starts_the_server(cwd, monkeypatch):
    """`rambass-console` is its own console_script, so it must reach `serve`
    with the flags it was given and nothing in between."""
    import rambass.console as console_module
    from rambass.cli import console_main

    seen = {}
    monkeypatch.setattr(console_module, "serve",
                        lambda project, **kwargs: seen.update(kwargs))
    assert console_main(["--port", "9111", "--no-browser"]) == 0
    assert seen == {"port": 9111, "setlist": "gig", "open_browser": False}


def test_the_console_executable_is_declared_as_an_entry_point():
    """A shortcut that is not in pyproject.toml is a shortcut that vanishes at
    the next `pip install -e .`."""
    import tomllib

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    scripts = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["rambass-console"] == "rambass.cli:console_main"


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
    """`analyze` needs librosa; without it the user gets an install hint.

    Asserted against `install_hint` rather than a literal `pip install`, because
    the literal passes on a uv-only machine by being a substring of `uv pip
    install` -- so it would go on passing while the printed command was one the
    reader cannot run. See tests/test_install_hint.py.
    """
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
    from rambass.audio import install_hint

    assert f"install it with:  {install_hint('audio')}" in capsys.readouterr().err


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


# ── lyrics commands ──────────────────────────────────────────────────────
SRT = (
    "1\r\n00:00:02,850 --> 00:00:07,039\r\nFor My Grana\r\n\r\n"
    "2\r\n00:00:08,220 --> 00:00:09,529\r\nA-uuuuuh\r\n"
)


def test_lyrics_import_stores_and_points_the_manifest_at_it(cwd, capsys, tmp_path):
    run("new", "ForMayGrana", "--album", "dg", "--create-album", "--bpm", "112")
    source = tmp_path / "02 ForMayGrana.srt"
    source.write_text(SRT, encoding="utf-8", newline="")
    capsys.readouterr()

    assert run("lyrics", "import", "01-formaygrana", str(source)) == 0
    out = capsys.readouterr().out
    assert "2 cues" in out

    stored = cwd.songs_dir / "dg" / "01-formaygrana" / "lyrics.srt"
    assert stored.is_file()
    assert b"\r\n" in stored.read_bytes()
    assert load_song(stored.parent).lyrics_file == "lyrics.srt"


def test_lyrics_import_can_shift_on_the_way_in(cwd, capsys, tmp_path):
    run("new", "X", "--album", "dg", "--create-album")
    source = tmp_path / "x.srt"
    source.write_text(SRT, encoding="utf-8", newline="")
    capsys.readouterr()
    run("lyrics", "import", "01-x", str(source), "--shift", "4")

    from rambass.lyrics import load

    cues = load(cwd.songs_dir / "dg" / "01-x" / "lyrics.srt")
    assert cues[0].start == pytest.approx(6.85)


def test_lyrics_export_writes_several_formats(cwd, capsys, tmp_path):
    run("new", "X", "--album", "dg", "--create-album", "--bars", "32")
    directory = cwd.songs_dir / "dg" / "01-x"
    (directory / "lyrics.srt").write_text(SRT, encoding="utf-8", newline="")
    capsys.readouterr()

    assert run("lyrics", "export", "01-x", "--format", "srt", "vtt", "lrc", "ass",
               "txt") == 0
    for suffix in ("srt", "vtt", "lrc", "ass", "txt"):
        assert (directory / "video" / f"x.{suffix}").is_file()
    assert (directory / "video" / "x.vtt").read_text().startswith("WEBVTT")


def test_lyrics_export_count_in_shifts_by_the_count_in(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album", "--bpm", "120",
        "--count-in", "2")
    directory = cwd.songs_dir / "dg" / "01-x"
    (directory / "lyrics.srt").write_text(SRT, encoding="utf-8", newline="")
    capsys.readouterr()

    run("lyrics", "export", "01-x", "--format", "srt", "--count-in",
        "--out", str(directory / "shifted.srt"))
    from rambass.lyrics import load

    assert load(directory / "shifted.srt")[0].start == pytest.approx(6.85)


def test_lyrics_check_reports_problems_and_exit_code(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album", "--bars", "8")
    directory = cwd.songs_dir / "dg" / "01-x"
    (directory / "lyrics.srt").write_text(
        "1\n00:00:00,000 --> 00:00:05,000\na\n\n"
        "2\n00:00:02,000 --> 00:00:06,000\nb\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    assert run("lyrics", "check", "01-x") == 1
    assert "overlap" in capsys.readouterr().out


def test_lyrics_check_is_clean_on_a_good_file(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album", "--bars", "64")
    (cwd.songs_dir / "dg" / "01-x" / "lyrics.srt").write_text(SRT, encoding="utf-8", newline="")
    capsys.readouterr()
    assert run("lyrics", "check", "01-x") == 0
    assert "look sane" in capsys.readouterr().out


def test_lyrics_check_skips_songs_without_lyrics_when_quiet(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album")
    capsys.readouterr()
    assert run("lyrics", "check", "--all", "--quiet") == 0


def test_srt_wins_over_the_bar_cue_markdown(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album", "--bpm", "120", "--bars", "32")
    directory = cwd.songs_dir / "dg" / "01-x"
    (directory / "lyrics.srt").write_text(SRT, encoding="utf-8", newline="")
    (directory / "lyrics.md").write_text("[bar 9]\nda markdown\n", encoding="utf-8")
    capsys.readouterr()

    assert run("video", "ass", "01-x") == 0
    text = (directory / "video" / "x.ass").read_text()
    assert "For My Grana" in text
    assert "da markdown" not in text


def test_video_ass_falls_back_to_the_markdown_when_there_is_no_srt(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album", "--bpm", "120", "--bars", "32")
    directory = cwd.songs_dir / "dg" / "01-x"
    (directory / "lyrics.md").write_text("[bar 9]\nda markdown\n", encoding="utf-8")
    capsys.readouterr()
    assert run("video", "ass", "01-x") == 0
    assert "da markdown" in (directory / "video" / "x.ass").read_text()


def test_video_ass_puts_the_title_on_screen_through_the_count_in(cwd, capsys):
    run("new", "Il Phurgone", "--album", "dg", "--create-album", "--bpm", "120",
        "--bars", "32", "--count-in", "2")
    directory = cwd.songs_dir / "dg" / "01-il-phurgone"
    (directory / "lyrics.md").write_text("[bar 9]\nUna riga\n", encoding="utf-8")
    capsys.readouterr()

    assert run("video", "ass", "01-il-phurgone", "--subtitle", "Diversamente Giovani") == 0
    text = (directory / "video" / "il-phurgone.ass").read_text()
    card = next(x for x in text.splitlines() if ",Card,," in x)
    assert card.startswith("Dialogue: 0,0:00:00.00,0:00:04.00")   # the count-in
    assert "Il Phurgone" in card and "Diversamente Giovani" in card

    assert run("video", "ass", "01-il-phurgone", "--no-card") == 0
    assert ",Card,," not in (directory / "video" / "il-phurgone.ass").read_text()


def test_video_card_writes_a_standalone_card_without_needing_lyrics(cwd, capsys):
    """The a cappella songs have no cues and never will — the card is the screen."""
    run("new", "Se Sei Felice", "--album", "dg", "--create-album", "--bars", "32")
    directory = cwd.songs_dir / "dg" / "01-se-sei-felice"
    (directory / "lyrics.md").unlink()
    capsys.readouterr()

    assert run("video", "card", "01-se-sei-felice", "--ass-only") == 0
    text = (directory / "video" / "se-sei-felice-card.ass").read_text()
    assert text.count("Dialogue:") == 1
    assert "Se Sei Felice" in text


def test_lyrics_commands_say_what_to_do_when_there_are_no_cues(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album")
    (cwd.songs_dir / "dg" / "01-x" / "lyrics.md").unlink()
    capsys.readouterr()
    assert run("lyrics", "export", "01-x") == 0
    assert "no lyrics file" in capsys.readouterr().out


def test_lyrics_bars_converts_to_the_markdown_format(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album", "--bpm", "120",
        "--count-in", "2")
    directory = cwd.songs_dir / "dg" / "01-x"
    (directory / "lyrics.srt").write_text(
        "1\n00:00:20,000 --> 00:00:22,000\nPrima riga\n", encoding="utf-8"
    )
    capsys.readouterr()
    assert run("lyrics", "bars", "01-x") == 0
    assert "[bar 9]" in (directory / "lyrics.bars.md").read_text()


def test_lyrics_shift_refuses_to_shift_a_bar_anchored_file(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album")
    directory = cwd.songs_dir / "dg" / "01-x"
    (directory / "lyrics.md").write_text("[bar 9]\nuna riga\n", encoding="utf-8")
    capsys.readouterr()
    assert run("lyrics", "shift", "01-x", "2.0") == 2
    assert "anchored to bars" in capsys.readouterr().err


# ── reaper import ────────────────────────────────────────────────────────
def test_reaper_import_reports_without_writing_then_writes(cwd, capsys):
    run("new", "Phooffi", "--album", "tif", "--create-album", "--count-in", "2")
    directory = cwd.songs_dir / "tif" / "01-phooffi"
    fixture = Path(__file__).parent / "fixtures" / "live-project.RPP"
    capsys.readouterr()

    assert run("reaper", "import", "01-phooffi", str(fixture)) == 0
    out = capsys.readouterr().out
    assert "90 BPM" in out and "nothing written" in out
    assert load_song(directory).bpm == 120.0        # untouched

    assert run("reaper", "import", "01-phooffi", str(fixture), "--write") == 0
    song = load_song(directory)
    assert song.bpm == 90.0
    assert song.status["analyze"] == "done"
    assert [s.name for s in song.sections][:2] == ["intro", "start"]
    assert song.patch_changes and song.patch_changes[0].memory == "U01-2"


def test_reaper_import_can_keep_existing_patches(cwd, capsys):
    run("new", "X", "--album", "tif", "--create-album")
    run("patch", "01-x", "5", "U04-4", "--name", "mine")
    fixture = Path(__file__).parent / "fixtures" / "live-project.RPP"
    capsys.readouterr()
    run("reaper", "import", "01-x", str(fixture), "--write", "--keep-patches")
    song = load_song(cwd.songs_dir / "tif" / "01-x")
    assert [c.memory for c in song.patch_changes] == ["U04-4"]


# ── scope and accompaniment ──────────────────────────────────────────────
def test_scope_out_cuts_a_song_and_scope_in_restores_it(cwd, capsys):
    run("new", "Solero", "--album", "tif", "--create-album")
    run("mark", "01-solero", "analyze", "done")
    capsys.readouterr()

    assert run("scope", "01-solero", "out", "--reason", "no WAV master") == 0
    song = load_song(cwd.songs_dir / "tif" / "01-solero")
    assert song.excluded and song.exclude_reason == "no WAV master"
    assert song.progress() == (0, 0)

    assert run("scope", "01-solero", "in") == 0
    song = load_song(cwd.songs_dir / "tif" / "01-solero")
    assert song.excluded is False
    assert song.progress()[1] > 0


def test_a_cut_song_is_hidden_from_list_and_check(cwd, capsys):
    run("new", "Cut", "--album", "tif", "--create-album")
    run("scope", "01-cut", "out", "--reason", "gone")
    capsys.readouterr()

    run("list")
    out = capsys.readouterr().out
    assert "Cut" not in out.split("cut from the set")[0]
    assert "1 song(s) cut" in out

    run("list", "--all-songs")
    assert "Cut" in capsys.readouterr().out

    # no source audio, but a cut song needs nothing
    assert run("check") == 0
    assert "consistent" in capsys.readouterr().out


def test_accompaniment_a_cappella_marks_the_pipeline_not_applicable(cwd, capsys):
    run("new", "Tonno", "--album", "tif", "--create-album")
    capsys.readouterr()
    assert run("accompaniment", "01-tonno", "a-cappella",
               "--reason", "sung unaccompanied") == 0
    out = capsys.readouterr().out
    assert "source, rehearsed" in out

    song = load_song(cwd.songs_dir / "tif" / "01-tonno")
    assert song.drums_origin == "a-cappella"
    assert song.status["render"] == "n/a"
    assert song.status["gx100"] == "n/a"
    assert song.status["source"] == "todo"
    assert "sung unaccompanied" in song.notes


def test_accompaniment_keeps_finished_stages_that_still_apply(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album")
    run("mark", "01-x", "source", "done")
    capsys.readouterr()
    run("accompaniment", "01-x", "backing-track")
    song = load_song(cwd.songs_dir / "dg" / "01-x")
    assert song.status["source"] == "done"      # kept
    assert song.status["quantize"] == "n/a"     # newly not applicable


def test_a_cappella_check_does_not_demand_source_audio(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album")
    run("accompaniment", "01-x", "a-cappella")
    capsys.readouterr()
    assert run("check") == 0


def test_section_from_a_reaper_bar_subtracts_the_count_in(cwd, capsys):
    """Reaper's ruler starts at the count-in, so its bar 19 is musical bar 17."""
    run("new", "X", "--album", "dg", "--create-album", "--bars", "64", "--count-in", "2")
    capsys.readouterr()
    assert run("section", "01-x", "19", "verse-1", "--reaper-bar") == 0
    song = load_song(cwd.songs_dir / "dg" / "01-x")
    assert [(s.bar, s.name) for s in song.sections if s.name == "verse-1"] == [(17, "verse-1")]


def test_a_reaper_bar_inside_the_count_in_is_refused(cwd, capsys):
    run("new", "X", "--album", "dg", "--create-album", "--count-in", "2")
    capsys.readouterr()
    assert run("section", "01-x", "2", "intro", "--reaper-bar") == 2
    assert "count-in" in capsys.readouterr().err


# ── the tempo, once settled, is not re-detected over ──────────────────────────
#
# Manlio's `notes:` record the decision: detection said 60.02, the flat 60 was
# chosen deliberately, and the transcription was re-run against 60 rather than
# re-quantised. Then a console click on "Find the tempo" put 60.02 back — 107 ms
# of slip across that song's 320 beats — with nothing said. `analyze --write`
# will not change a tempo the manifest already calls settled.


def _fake_analysis(bpm: float):
    from rambass.analyze import TempoAnalysis

    return TempoAnalysis(bpm=bpm, bpm_rounded=bpm, duration=60.0)


def _stub_analyze(monkeypatch, bpm: float):
    from rambass import analyze

    monkeypatch.setattr(analyze, "analyze_tempo",
                        lambda *a, **k: _fake_analysis(bpm))


def _song_with_a_settled_tempo(cwd, bpm=60.0):
    from rambass.manifest import load_song, save_song

    run("new", "Manlio", "--album", "tif", "--create-album")
    directory = cwd.songs_dir / "tif" / "01-manlio"
    (directory / "source" / "09 Manlio.wav").write_bytes(b"RIFF----WAVE")
    song = load_song(directory)
    song.bpm = bpm
    song.status["analyze"] = "done"
    save_song(song)
    return directory


def test_analyze_write_refuses_to_move_a_settled_tempo(cwd, capsys, monkeypatch):
    from rambass.manifest import load_song

    directory = _song_with_a_settled_tempo(cwd)
    _stub_analyze(monkeypatch, 60.02)
    capsys.readouterr()

    assert run("analyze", "01-manlio", "--write") == 1
    out = capsys.readouterr().out
    assert "60.02" in out and "--force" in out
    assert load_song(directory).bpm == 60.0


def test_analyze_write_may_be_forced(cwd, capsys, monkeypatch):
    from rambass.manifest import load_song

    directory = _song_with_a_settled_tempo(cwd)
    _stub_analyze(monkeypatch, 60.02)
    capsys.readouterr()

    assert run("analyze", "01-manlio", "--write", "--force") == 0
    assert load_song(directory).bpm == 60.02


def test_analyze_write_still_writes_an_unsettled_tempo(cwd, monkeypatch):
    """A song whose analyze stage is not done has nothing to protect."""
    from rambass.manifest import load_song, save_song

    directory = _song_with_a_settled_tempo(cwd)
    song = load_song(directory)
    song.status["analyze"] = "todo"
    save_song(song)
    _stub_analyze(monkeypatch, 60.02)

    assert run("analyze", "01-manlio", "--write") == 0
    assert load_song(directory).bpm == 60.02


def test_analyze_write_agreeing_with_the_manifest_is_not_a_refusal(cwd, monkeypatch):
    directory = _song_with_a_settled_tempo(cwd)
    _stub_analyze(monkeypatch, 60.0)
    assert run("analyze", "01-manlio", "--write") == 0
    from rambass.manifest import load_song

    assert load_song(directory).bpm == 60.0
