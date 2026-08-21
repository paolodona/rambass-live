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
        if bpm:
            if song.bpm != float(bpm):
                notes.append(f"bpm {song.bpm:g} -> {float(bpm):g}")
                song.bpm = float(bpm)
            # The tempo is known either way, so the analyze stage is done —
            # including when the known value happens to match the placeholder.
            if song.status.get("analyze") != "done":
                notes.append("analyze = done")
                song.status["analyze"] = "done"

        # A finished backing track makes the whole drum pipeline moot.
        origin = str((data.get("drums") or {}).get("origin", ""))
        if origin and song.drums_origin != origin:
            notes.append(f"drums.origin {song.drums_origin} -> {origin}")
            song.drums_origin = origin
            song.status.update(
                {k: v for k, v in song.default_status().items() if v == "n/a"}
            )

        base = entry.get("backing_track")
        if base and song.backing_track != base:
            notes.append("backing track recorded")
            song.backing_track = base
            song.drum_kit = ""          # nothing is being voiced; the base is mixed
            song.drum_map = "general-midi"
        if entry.get("base_id"):
            song.extra["backing_track_source"] = (
                f"https://drive.google.com/file/d/{entry['base_id']}/view"
            )

        kit = entry.get("bfd3")
        if kit and not base:
            # only meaningful for a song whose drums still have to be voiced
            song.extra["bfd3_preset"] = str(kit)

        for stage, state in (entry.get("status") or {}).items():
            if song.status.get(stage) != state:
                notes.append(f"{stage} = {state}")
                song.status[stage] = str(state)

        review = str(entry.get("review", ""))
        if review and review not in song.notes:
            notes.append("review note added")
            song.notes = (song.notes + "\n" if song.notes else "") + f"base review: {review}"

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
