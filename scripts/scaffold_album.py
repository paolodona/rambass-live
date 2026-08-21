#!/usr/bin/env python3
"""Create song folders for an album from its ``sources.yaml``.

    python scripts/scaffold_album.py songs/tutti-in-fila/sources.yaml
    python scripts/scaffold_album.py --all

Existing ``song.yaml`` files are never overwritten, so this is safe to re-run
after adding a track to ``sources.yaml``. Pass ``--force`` if you really do want
to reset a manifest.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rambass.manifest import STAGES, Section, Song, save_song  # noqa: E402
from rambass.project import Project, slugify  # noqa: E402

DRIVE_FILE = "https://drive.google.com/file/d/{id}/view"


def scaffold(sources_path: Path, *, force: bool = False) -> int:
    data = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
    album = data["album"]
    album_dir = sources_path.parent
    drums = data.get("drums", "extracted")
    created = 0

    for entry in data.get("tracks") or []:
        title = str(entry["title"])
        track = int(entry["track"])
        slug = slugify(title)
        directory = album_dir / f"{track:02d}-{slug}"
        manifest = directory / "song.yaml"

        if manifest.exists() and not force:
            print(f"  keep    {directory.name}")
            continue

        note = str(entry.get("note", ""))
        song = Song(
            slug=slug,
            title=title,
            album=album,
            track=track,
            directory=directory,
            bpm=120.0,                     # placeholder until `rambass analyze`
            drums_origin=drums,
            source_audio=str(entry.get("file", "")),
            source_url=DRIVE_FILE.format(id=entry["id"]) if entry.get("id") else "",
            sections=[Section("intro", 1)],
            status={stage: "todo" for stage in STAGES},
            notes=note,
        )
        if drums != "extracted":
            song.status["stems"] = "n/a"
        save_song(song, directory)

        lyrics = directory / song.lyrics_file
        if not lyrics.exists():
            lyrics.write_text(f"# {title}\n\n[bar 1]\n", encoding="utf-8")
        print(f"  created {directory.name}")
        created += 1

    print(f"{album}: {created} new, {len(data.get('tracks') or [])} tracks total")
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="*", type=Path)
    parser.add_argument("--all", action="store_true", help="every album with a sources.yaml")
    parser.add_argument("--force", action="store_true", help="overwrite existing song.yaml")
    args = parser.parse_args()

    paths = list(args.sources)
    if args.all or not paths:
        project = Project.discover()
        paths = sorted(project.songs_dir.glob("*/sources.yaml"))
    if not paths:
        print("no sources.yaml found", file=sys.stderr)
        return 1
    for path in paths:
        print(path)
        scaffold(path, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
