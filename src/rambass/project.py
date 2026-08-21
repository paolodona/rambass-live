"""Project layout: where songs, stems, MIDI and renders live on disk."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Directories created inside every song folder.
SONG_SUBDIRS = ("source", "stems", "midi", "render", "video")

#: Marker files that identify the repository root.
ROOT_MARKERS = ("pyproject.toml", "rambass.toml", ".git")


class ProjectError(RuntimeError):
    """Raised for user-facing problems (bad paths, missing songs, ...)."""


def slugify(text: str) -> str:
    """Turn a song or album title into a filesystem-safe slug.

    Italian titles are common here, so accented characters are folded down to
    ASCII rather than dropped: ``"Perché No"`` -> ``"perche-no"``.
    """
    folds = {
        "à": "a", "á": "a", "â": "a", "ä": "a", "è": "e", "é": "e", "ê": "e",
        "ë": "e", "ì": "i", "í": "i", "î": "i", "ï": "i", "ò": "o", "ó": "o",
        "ô": "o", "ö": "o", "ù": "u", "ú": "u", "û": "u", "ü": "u", "ç": "c",
        "ñ": "n", "'": " ", "’": " ",
    }
    out = text.strip().lower()
    for src, dst in folds.items():
        out = out.replace(src, dst)
    out = re.sub(r"[^a-z0-9]+", "-", out)
    return out.strip("-")


def find_root(start: Path | None = None) -> Path:
    """Walk upwards from *start* looking for the repository root."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if any((candidate / marker).exists() for marker in ROOT_MARKERS):
            if (candidate / "songs").is_dir() or (candidate / "pyproject.toml").exists():
                return candidate
    raise ProjectError(
        f"could not find the rambass-live repository root above {here}. "
        "Run the command from inside the checkout."
    )


@dataclass(frozen=True)
class Project:
    """Resolved paths for one checkout of the repository."""

    root: Path

    @classmethod
    def discover(cls, start: Path | None = None) -> Project:
        return cls(find_root(start))

    # ── top-level directories ────────────────────────────────────────────
    @property
    def songs_dir(self) -> Path:
        return self.root / "songs"

    @property
    def setlists_dir(self) -> Path:
        return self.root / "setlists"

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def drum_maps_dir(self) -> Path:
        return self.config_dir / "drum-maps"

    @property
    def reaper_dir(self) -> Path:
        return self.root / "reaper"

    @property
    def reaper_build_dir(self) -> Path:
        return self.reaper_dir / "build"

    @property
    def template_dir(self) -> Path:
        return self.songs_dir / "_template"

    # ── song lookup ──────────────────────────────────────────────────────
    def albums(self) -> list[str]:
        if not self.songs_dir.is_dir():
            return []
        return sorted(
            p.name
            for p in self.songs_dir.iterdir()
            if p.is_dir() and not p.name.startswith("_")
        )

    def song_dirs(self, album: str | None = None) -> list[Path]:
        """Every directory that contains a ``song.yaml``, in setlist order."""
        found: list[Path] = []
        for album_name in [album] if album else self.albums():
            album_dir = self.songs_dir / album_name
            if not album_dir.is_dir():
                raise ProjectError(f"no such album: {album_name}")
            found.extend(
                sorted(p for p in album_dir.iterdir() if (p / "song.yaml").is_file())
            )
        return found

    def find_song_dir(self, ref: str) -> Path:
        """Resolve a song reference to a directory.

        *ref* may be a path, an ``album/slug`` pair, a bare slug, or the
        numbered folder name (``03-nome-canzone``).
        """
        as_path = Path(ref)
        if (as_path / "song.yaml").is_file():
            return as_path.resolve()

        wanted = slugify(Path(ref).name)
        matches: list[Path] = []
        for song_dir in self.song_dirs():
            names = {
                song_dir.name,
                slugify(song_dir.name),
                _strip_track_number(song_dir.name),
                f"{song_dir.parent.name}/{song_dir.name}",
            }
            if ref in names or wanted in {slugify(n) for n in names}:
                matches.append(song_dir)

        if not matches:
            raise ProjectError(
                f"no song matching {ref!r}. Use `rambass list` to see what exists."
            )
        if len(matches) > 1:
            listed = ", ".join(f"{m.parent.name}/{m.name}" for m in matches)
            raise ProjectError(f"{ref!r} is ambiguous — matches {listed}")
        return matches[0]


def _strip_track_number(name: str) -> str:
    """``"03-nome-canzone"`` -> ``"nome-canzone"``."""
    return re.sub(r"^\d+[-_]", "", name)
