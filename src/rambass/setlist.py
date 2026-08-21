"""Setlists: the running order for one show.

A setlist is a small YAML file listing song references in order. Keeping it
separate from the songs means the same material can be arranged into a short
set, a full two-album show, or a rehearsal order without touching any manifest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .manifest import Song, load_song
from .project import Project, ProjectError
from .reaper import project_length_seconds


@dataclass
class Setlist:
    name: str
    songs: list[str] = field(default_factory=list)
    date: str = ""
    venue: str = ""
    notes: str = ""
    #: A record of a set that was already played, rather than a proposal. Still
    #: worth reviewing — that is how you learn what to do differently — but its
    #: findings are not failures.
    historical: bool = False
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> Setlist:
        path = Path(path)
        if not path.is_file():
            raise ProjectError(f"no such setlist: {path}")
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        entries = data.get("songs") or []
        songs: list[str] = []
        for entry in entries:
            if isinstance(entry, dict):        # allow `- song: x` with extras
                songs.append(str(entry.get("song") or entry.get("slug")))
            else:
                songs.append(str(entry))
        return cls(
            name=str(data.get("name", path.stem)),
            songs=songs,
            date=str(data.get("date", "")),
            venue=str(data.get("venue", "")),
            notes=str(data.get("notes", "")),
            historical=bool(data.get("historical", False)),
            path=path,
        )

    def resolve(self, project: Project) -> list[Song]:
        out: list[Song] = []
        missing: list[str] = []
        for reference in self.songs:
            try:
                out.append(load_song(project.find_song_dir(reference)))
            except ProjectError:
                missing.append(reference)
        if missing:
            raise ProjectError(
                f"setlist {self.name!r} references songs that do not exist: "
                + ", ".join(missing)
            )
        return out


def find_setlist(project: Project, reference: str) -> Path:
    candidate = Path(reference)
    if candidate.is_file():
        return candidate
    for suffix in ("", ".yaml", ".yml"):
        candidate = project.setlists_dir / f"{reference}{suffix}"
        if candidate.is_file():
            return candidate
    available = ", ".join(sorted(p.stem for p in project.setlists_dir.glob("*.y*ml")))
    raise ProjectError(f"no setlist {reference!r} (have: {available or 'none'})")


def running_order(setlist: Setlist, songs: list[Song], *, gap_seconds: float = 30.0) -> str:
    """A printable running order.

    Lengths are shown only for songs whose bar count is actually known. A song
    that has not been analysed yet shows ``?`` rather than a number derived from
    a fallback guess — a set running time you cannot trust is worse than one that
    admits what it does not know, because the whole point of the table is
    deciding whether the set fits the slot.
    """
    lines = [f"# {setlist.name}"]
    if setlist.date or setlist.venue:
        lines.append(" · ".join(x for x in (setlist.date, setlist.venue) if x))
    lines += [
        "",
        "| # | song | BPM | sig | bars | length | cumulative |",
        "|--:|:-----|----:|:---:|-----:|-------:|-----------:|",
    ]

    known = [s for s in songs if s.length_known]
    cumulative = 0.0
    for index, song in enumerate(songs, start=1):
        if song.length_known:
            length = project_length_seconds(song)
            cumulative += length
            shown, running = _mmss(length), _mmss(cumulative)
            cumulative += gap_seconds
            bars = str(song.total_bars())
        else:
            shown, running, bars = "?", "?", "?"
        lines.append(
            f"| {index} | {song.title} | {song.bpm:g} | "
            f"{song.time_signature[0]}/{song.time_signature[1]} | {bars} | "
            f"{shown} | {running} |"
        )

    lines.append("")
    if not known:
        lines.append(
            f"**{len(songs)} songs · total length unknown** — no song has a bar "
            "count yet, so there is nothing to add up. `rambass analyze <song> "
            "--write` or `rambass reaper import` sets it."
        )
    elif len(known) < len(songs):
        music = cumulative - gap_seconds
        lines.append(
            f"**{len(songs)} songs · at least {_mmss(music)}** from the "
            f"{len(known)} with a known length, plus {_mmss(gap_seconds)} between "
            f"songs. {len(songs) - len(known)} song(s) still unmeasured, so the "
            "real total is higher."
        )
    else:
        music = cumulative - gap_seconds
        lines.append(
            f"**{len(songs)} songs · {_mmss(music)} including {_mmss(gap_seconds)} "
            f"between songs**"
        )
    if setlist.notes:
        lines += ["", "## Notes", "", setlist.notes]
    return "\n".join(lines)


def _mmss(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"
