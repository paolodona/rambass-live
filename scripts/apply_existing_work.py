#!/usr/bin/env python3
"""Apply an album's ``existing-work.yaml`` to its song manifests.

The tempos in that file came off the band's own production folders rather than
from tempo detection, so they are better than anything `rambass analyze` would
produce — this writes them in and marks the `analyze` stage done.

    python scripts/apply_existing_work.py songs/diversamente-giovani/existing-work.yaml

Re-runnable: it only ever sets fields it has real values for.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rambass.manifest import load_song, save_song  # noqa: E402
from rambass.project import Project  # noqa: E402


def apply(path: Path, *, dry_run: bool = False) -> int:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    album_dir = path.parent
    changed = 0

    for entry in data.get("tracks") or []:
        slug = str(entry["slug"])
        matches = [d for d in album_dir.iterdir()
                   if d.is_dir() and d.name.endswith(f"-{slug}")]
        if not matches:
            print(f"  ?  no folder for {slug}")
            continue
        song = load_song(matches[0])
        notes: list[str] = []

        bpm = entry.get("bpm")
        if bpm and song.bpm != float(bpm):
            notes.append(f"bpm {song.bpm:g} -> {float(bpm):g}")
            song.bpm = float(bpm)
            song.status["analyze"] = "done"

        kit = entry.get("bfd3")
        if kit and not song.drum_kit:
            notes.append(f"kit = BFD3 {kit}")
            song.drum_kit = f"BFD3 {kit}"
            song.drum_map = "bfd3"

        extra = str(entry.get("note", ""))
        if extra and extra not in song.notes:
            notes.append("note added")
            song.notes = (song.notes + "\n" if song.notes else "") + extra

        if entry.get("srt"):
            song.extra["srt_source"] = (
                f"https://drive.google.com/file/d/{entry['srt']}/view"
            )
            notes.append("srt source recorded")
        if entry.get("lyrics_doc"):
            song.extra["lyrics_doc"] = (
                f"https://docs.google.com/document/d/{entry['lyrics_doc']}/edit"
            )
            notes.append("lyrics doc recorded")
        if entry.get("drum_stem"):
            song.extra["drum_stem_source"] = str(entry["drum_stem"])
            notes.append("drum stem source recorded")

        if not notes:
            print(f"  =  {matches[0].name}")
            continue
        print(f"  ->  {matches[0].name}: {', '.join(notes)}")
        if not dry_run:
            song.validate()
            save_song(song)
        changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="*", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    paths = args.sources or sorted(
        Project.discover().songs_dir.glob("*/existing-work.yaml")
    )
    total = 0
    for path in paths:
        print(path)
        total += apply(path, dry_run=args.dry_run)
    print(f"{total} song(s) updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
