"""Shared fixtures: a throwaway project on disk, no heavy dependencies."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from rambass.manifest import STAGES, Section, Song, save_song
from rambass.project import Project


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A minimal but complete repository layout in a tmp dir."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "songs").mkdir()
    (tmp_path / "setlists").mkdir()
    (tmp_path / "config" / "drum-maps").mkdir(parents=True)
    (tmp_path / "reaper" / "build").mkdir(parents=True)
    (tmp_path / "config" / "drum-maps" / "electro.yaml").write_text(
        yaml.safe_dump({
            "name": "electro",
            "channel": 10,
            "notes": {"kick": 60, "snare": 62, "hihat_closed": None},
        }),
        encoding="utf-8",
    )
    return Project(tmp_path)


@pytest.fixture
def song(project: Project) -> Song:
    """One song with sections and patch changes, saved to disk."""
    directory = project.songs_dir / "tutti-in-fila" / "03-tutti-in-fila"
    item = Song(
        slug="tutti-in-fila",
        title="Tutti In Fila",
        album="tutti-in-fila",
        track=3,
        directory=directory,
        bpm=120.0,
        count_in_bars=2,
        bars=32,
        sections=[Section("intro", 1), Section("verse", 9), Section("chorus", 17)],
        status={stage: "todo" for stage in STAGES},
    )
    save_song(item, directory)
    return item


@pytest.fixture
def cwd_song(song, project, monkeypatch):
    """The standard song, with the CLI's working directory inside the project."""
    monkeypatch.chdir(project.root)
    return song
