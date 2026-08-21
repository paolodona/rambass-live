"""``rambass`` — the command line for building the show.

Run ``rambass`` with no arguments for the command list, or ``rambass <cmd> -h``
for one command. ``rambass doctor`` first, if something is not working.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .drummap import load_drum_map
from .manifest import STAGES, STATUS_VALUES, PatchChange, Section, Song, load_song, save_song
from .project import Project, ProjectError, slugify

EPILOGUE = """\
typical order of work for one song:
  rambass new "Titolo" --album tutti-in-fila --track 4
  rambass analyze <song> --write        # find the tempo, write it to song.yaml
  rambass stems <song>                  # extracted songs only
  rambass drums transcribe <song>       # or: rambass drums import <song> take.mid
  rambass drums clean <song>            # de-flam, quantise, shape velocities
  rambass click <song>
  rambass gx100 midi <song>
  rambass reaper build <song>           # then run the ReaScript in Reaper
  rambass video ass <song>
"""


# ── small helpers ────────────────────────────────────────────────────────
def _project() -> Project:
    return Project.discover()


def _songs(project: Project, refs: list[str], album: str | None, every: bool) -> list[Song]:
    if every or (not refs and album):
        return [load_song(d) for d in project.song_dirs(album)]
    if not refs:
        raise ProjectError("name a song, or pass --all")
    return [load_song(project.find_song_dir(ref)) for ref in refs]


def _mark(song: Song, stage: str, state: str = "done") -> None:
    if stage in STAGES:
        song.status[stage] = state
        save_song(song)


def _say(message: str = "") -> None:
    print(message)


# ── commands: inventory ──────────────────────────────────────────────────
def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import report

    text, ok = report()
    _say(text)
    return 0 if ok else 1


def cmd_list(args: argparse.Namespace) -> int:
    project = _project()
    songs = [load_song(d) for d in project.song_dirs(args.album)]
    if not songs:
        _say("no songs yet. `rambass new \"Titolo\" --album <album>`")
        return 0
    width = max(len(s.title) for s in songs)
    for song in songs:
        done, total = song.progress()
        _say(
            f"{song.album:<22} {song.title.ljust(width)}  "
            f"{song.bpm:>6.1f} BPM  {song.time_signature[0]}/{song.time_signature[1]}  "
            f"{song.drums_origin:<10} {done}/{total}"
        )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    from .reaper import describe

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        _say(describe(song.timeline(), song))
        _say()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from .status import board, next_actions

    project = _project()
    songs = [load_song(d) for d in project.song_dirs(args.album)]
    _say(board(songs, markdown=args.markdown))
    if not args.markdown:
        upcoming = next_actions(songs)
        if upcoming:
            _say()
            _say(upcoming)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    project = _project()
    problems = 0
    for song_dir in project.song_dirs(args.album):
        try:
            song = load_song(song_dir)
        except ProjectError as exc:
            _say(f"BROKEN {song_dir}: {exc}")
            problems += 1
            continue
        found = song.problems()
        if (not args.no_audio_check and song.drums_origin != "programmed"
                and song.source_path() is None):
            hint = f" — download it from {song.source_url}" if song.source_url else ""
            found.append(f"no audio in source/{hint}")
        if found:
            _say(f"{song.album}/{song.slug}")
            for problem in found:
                _say(f"  - {problem}")
            problems += len(found)
    if problems:
        _say(f"\n{problems} problem(s) found")
        return 1
    _say("all manifests look consistent")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    project = _project()
    slug = args.slug or slugify(args.title)
    album = args.album
    album_dir = project.songs_dir / album
    if not album_dir.is_dir():
        if not args.create_album:
            raise ProjectError(
                f"no album {album!r}. Existing: {', '.join(project.albums()) or 'none'}. "
                "Pass --create-album to make it."
            )
        album_dir.mkdir(parents=True)

    track = args.track
    if not track:
        existing = [d.name for d in album_dir.iterdir() if d.is_dir()]
        numbers = [int(n[:2]) for n in existing if n[:2].isdigit()]
        track = max(numbers, default=0) + 1

    directory = album_dir / f"{track:02d}-{slug}"
    if directory.exists() and not args.force:
        raise ProjectError(f"{directory} already exists (pass --force to overwrite song.yaml)")

    song = Song(
        slug=slug,
        title=args.title,
        album=album,
        track=track,
        directory=directory,
        bpm=args.bpm,
        time_signature=tuple(int(x) for x in args.time_signature.split("/")),  # type: ignore[arg-type]
        count_in_bars=args.count_in,
        drums_origin=args.drums,
        bars=args.bars,
        sections=[Section("intro", 1)],
        status={stage: "todo" for stage in STAGES},
    )
    if song.drums_origin != "extracted":
        song.status["stems"] = "n/a"
    path = save_song(song, directory)

    lyrics = directory / song.lyrics_file
    if not lyrics.exists():
        lyrics.write_text(
            f"# {song.title}\n\n"
            "[bar 1] image: ../../../video/assets/logo.png\n\n"
            "[bar 9]\nPrima riga del testo\nSeconda riga\n",
            encoding="utf-8",
        )
    _say(f"created {path}")
    _say(f"  put the original mix in {directory / 'source'}/")
    return 0


# ── commands: audio analysis ─────────────────────────────────────────────
def cmd_analyze(args: argparse.Namespace) -> int:
    from .analyze import analyze_tempo

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        source = Path(args.file) if args.file else song.source_path()
        if not source:
            _say(f"{song.slug}: nothing in source/ to analyse")
            continue
        _say(f"── {song.title}  ({source.name})")
        analysis = analyze_tempo(source, round_to=args.round_to)
        _say(analysis.summary())
        if args.write:
            song.bpm = analysis.bpm_rounded
            if not song.bars and analysis.estimated_bars:
                song.bars = analysis.estimated_bars
            song.status["analyze"] = "done" if analysis.steady else "wip"
            save_song(song)
            _say(f"→ wrote bpm {song.bpm:g} to song.yaml"
                 + ("" if analysis.steady else " (marked wip: tempo drifts)"))
        _say()
    return 0


def cmd_stems(args: argparse.Namespace) -> int:
    from .stems import separate

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        source = song.source_path()
        if not source:
            _say(f"{song.slug}: nothing in source/ to separate")
            continue
        _say(f"── {song.title}: separating with {args.model} "
             f"({'drums only' if args.drums_only else 'four stems'})")
        result = separate(
            source,
            song.path("stems"),
            model=args.model,
            two_stems="drums" if args.drums_only else None,
            device=args.device,
            shifts=args.shifts,
            jobs=args.jobs,
        )
        for name, path in sorted(result.stems.items()):
            _say(f"   {name:<10} {path}")
        _mark(song, "stems")
    return 0


# ── commands: drums ──────────────────────────────────────────────────────
def cmd_drums_transcribe(args: argparse.Namespace) -> int:
    from .analyze import analyze_tempo
    from .midiio import write_drum_midi
    from .transcribe import transcribe_drums

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        stem = Path(args.file) if args.file else song.stem_path("drums")
        if not stem:
            _say(f"{song.slug}: no stems/drums.* — run `rambass stems` first")
            continue

        offset = args.offset
        if offset is None:
            analysis = analyze_tempo(stem)
            offset = analysis.downbeat_time
            _say(f"── {song.title}: first downbeat at {offset:.3f}s "
                 f"(detected {analysis.bpm:.1f} BPM, manifest says {song.bpm:g})")
        else:
            _say(f"── {song.title}: offset {offset:.3f}s")

        performance, report = transcribe_drums(
            stem, timeline=song.timeline(), offset=offset,
            detect_cymbals=not args.no_cymbals,
        )
        _say(report.summary())
        target = song.drum_midi_path("raw")
        write_drum_midi(target, performance, load_drum_map(song.drum_map, project))
        _say(f"→ {target}  ({len(performance.hits)} hits)")
        _mark(song, "drums_midi", "wip")
    return 0


def cmd_drums_import(args: argparse.Namespace) -> int:
    from .midiio import read_drum_midi, write_drum_midi

    project = _project()
    song = load_song(project.find_song_dir(args.song))
    drum_map = load_drum_map(args.map or song.drum_map, project)
    performance = read_drum_midi(args.file, drum_map, channel=args.channel)
    _say(f"read {len(performance.hits)} hits from {args.file}")
    _say(f"  source tempo   {performance.timeline.bpm:.2f} BPM")
    _say(f"  instruments    {', '.join(performance.instruments())}")
    for instrument, count in performance.count().items():
        _say(f"    {instrument:<16} {count}")

    if args.retempo:
        performance.timeline = song.timeline()
        _say(f"  retimed onto the manifest grid at {song.bpm:g} BPM")
    target = song.drum_midi_path("raw")
    write_drum_midi(target, performance, drum_map)
    _say(f"→ {target}")
    _mark(song, "drums_midi")
    return 0


def cmd_drums_clean(args: argparse.Namespace) -> int:
    from .midiio import read_drum_midi, write_drum_midi
    from .quantize import QuantizeSettings, deflam, humanize, quantize, shape_velocities

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        source = Path(args.file) if args.file else song.drum_midi_path(args.input)
        if not source.exists():
            _say(f"{song.slug}: no {source.name} — transcribe or import first")
            continue
        drum_map = load_drum_map(song.drum_map, project)
        performance = read_drum_midi(source, drum_map)
        performance.timeline = song.timeline()
        _say(f"── {song.title}: {len(performance.hits)} hits from {source.name}")

        if args.deflam_ms > 0:
            performance, removed = deflam(performance, args.deflam_ms)
            _say(f"   de-flam       -{removed} duplicate hits "
                 f"(within {args.deflam_ms:g} ms)")

        performance, report = quantize(performance, QuantizeSettings(
            subdivision=args.subdivision,
            strength=args.strength,
            swing=args.swing,
            tolerance_steps=args.tolerance,
            force=args.force_grid,
        ))
        _say(f"   quantise      {report['hits']} hits, "
             f"{report['left_alone']} left alone, "
             f"mean move {report['mean_shift_ms']} ms, "
             f"max {report['largest_shift_ms']} ms")

        if args.accent_kick or args.accent_snare:
            accents = {}
            if args.accent_kick:
                accents["kick"] = args.accent_kick
            if args.accent_snare:
                accents["snare"] = args.accent_snare
            performance = shape_velocities(
                performance, accents=accents, downbeat_boost=args.downbeat_boost
            )
            _say(f"   velocities    {accents}"
                 + (f" +{args.downbeat_boost} on downbeats" if args.downbeat_boost else ""))

        if args.humanize > 0:
            performance = humanize(
                performance, timing_ms=args.humanize, seed=args.seed,
            )
            _say(f"   humanise      ±{args.humanize:g} ms (seed {args.seed}, kick untouched)")

        target = song.drum_midi_path(args.output)
        write_drum_midi(target, performance, drum_map)
        _say(f"→  {target}")
        _mark(song, "quantize")
    return 0


def cmd_drums_remap(args: argparse.Namespace) -> int:
    from .midiio import read_drum_midi, write_drum_midi

    project = _project()
    song = load_song(project.find_song_dir(args.song))
    source_map = load_drum_map(args.from_map or song.drum_map, project)
    target_map = load_drum_map(args.to_map, project)
    source = Path(args.file) if args.file else song.drum_midi_path(args.input)
    performance = read_drum_midi(source, source_map)
    performance.timeline = song.timeline()
    target = song.drum_midi_path(f"{args.input}-{target_map.name}")
    write_drum_midi(target, performance, target_map)
    _say(f"{source_map.name} → {target_map.name}: {target}")
    if args.set_default:
        song.drum_map = target_map.name
        save_song(song)
        _say(f"song.yaml now defaults to drum map {target_map.name}")
    return 0


# ── commands: click, reaper, gx100, video ────────────────────────────────
def cmd_click(args: argparse.Namespace) -> int:
    from .click import render_click_file

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        bars = args.bars or song.total_bars()
        target = song.path("render", "click.wav")
        render_click_file(
            target, song.timeline(), bars,
            sample_rate=args.sample_rate,
            accent_downbeat=bool(song.click.get("accent_downbeat", True)),
            level_db=args.level,
        )
        timeline = song.timeline()
        _say(f"{song.title}: {target}  "
             f"({song.count_in_bars} bar count-in = {timeline.count_in_seconds:.2f}s, "
             f"then {bars} bars at {song.bpm:g} BPM)")
    return 0


def cmd_reaper_build(args: argparse.Namespace) -> int:
    from .reaper import build_song_script

    project = _project()
    project.reaper_build_dir.mkdir(parents=True, exist_ok=True)
    for song in _songs(project, args.song, args.album, args.all):
        script = build_song_script(
            song,
            click_wav=song.path("render", "click.wav"),
            drum_midi=song.drum_midi_path(args.midi),
            gx100_midi=song.path("midi", "gx100.mid"),
            include_reference=not args.no_reference,
        )
        target = project.reaper_build_dir / f"{song.album}-{song.slug}.rbs"
        script.write(target, header_comment=f"{song.album} / {song.title}")
        _say(f"{song.title}: {target}")
    _say()
    _say("in Reaper: File > New Project, then Actions > ReaScript > "
         "reaper/scripts/rambass_build_song.lua")
    return 0


def cmd_reaper_setlist(args: argparse.Namespace) -> int:
    from .reaper import build_setlist_script
    from .setlist import Setlist, find_setlist

    project = _project()
    setlist = Setlist.load(find_setlist(project, args.setlist))
    songs = setlist.resolve(project)
    renders = {}
    for song in songs:
        candidate = song.path("render", f"{song.slug}.wav")
        if candidate.exists():
            renders[song.slug] = candidate
    script = build_setlist_script(songs, gap_seconds=args.gap, renders=renders)
    target = project.reaper_build_dir / f"setlist-{setlist.path.stem}.rbs"
    script.write(target, header_comment=f"setlist: {setlist.name}")
    _say(f"{setlist.name}: {target}  ({len(songs)} songs, "
         f"{len(renders)} rendered backing tracks found)")
    return 0


def cmd_gx100_midi(args: argparse.Namespace) -> int:
    from .gx100 import load_program_map, write_patch_midi

    project = _project()
    program_map = load_program_map(project)
    for song in _songs(project, args.song, args.album, args.all):
        if not song.patch_changes:
            _say(f"{song.slug}: no gx100.changes in song.yaml — nothing to send")
            continue
        target, events = write_patch_midi(
            song.path("midi", "gx100.mid"), song, program_map, lead_ms=args.lead_ms
        )
        _say(f"{song.title}: {target}")
        for event in events:
            _say(f"   bar {event.bar:>4}  {event.memory:<7} "
                 f"bank {event.bank} PC {event.program:<4} {event.name}")
        _mark(song, "gx100")
    return 0


def cmd_gx100_map(args: argparse.Namespace) -> int:
    from .gx100 import load_program_map

    program_map = load_program_map(_project())
    _say(f"GX-100 program map (RX channel {program_map.channel}, "
         f"sequential default: {program_map.sequential_default})")
    _say("bank  PC   memory")
    for bank, program, memory in program_map.table(limit=args.limit):
        _say(f"{bank:>4} {program:>4}   {memory}")
    if args.limit:
        _say(f"... limited to {args.limit} rows; pass --limit 0 for all")
    return 0


def cmd_gx100_sheet(args: argparse.Namespace) -> int:
    from .gx100 import load_program_map, patch_sheet
    from .setlist import Setlist, find_setlist

    project = _project()
    if args.setlist:
        setlist = Setlist.load(find_setlist(project, args.setlist))
        songs = setlist.resolve(project)
    else:
        songs = [load_song(d) for d in project.song_dirs(args.album)]
    text = patch_sheet(songs, load_program_map(project))
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        _say(f"wrote {args.out}")
    else:
        _say(text)
    return 0


def cmd_video_ass(args: argparse.Namespace) -> int:
    from .reaper import project_length_seconds
    from .video import build_ass, parse_lyrics

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        lyrics = song.path(song.lyrics_file)
        if not lyrics.is_file():
            _say(f"{song.slug}: no {song.lyrics_file}")
            continue
        cues = parse_lyrics(lyrics.read_text(encoding="utf-8"))
        text = build_ass(
            cues, song.timeline(),
            end_seconds=project_length_seconds(song),
            title=song.title,
        )
        target = song.path("video", f"{song.slug}.ass")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        images = sum(1 for c in cues if c.image)
        _say(f"{song.title}: {target}  "
             f"({len(cues)} cues, {images} image cues)")
    return 0


def cmd_video_render(args: argparse.Namespace) -> int:
    from .reaper import project_length_seconds
    from .video import build_ass, image_segments, parse_lyrics, render_video

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        lyrics = song.path(song.lyrics_file)
        if not lyrics.is_file():
            _say(f"{song.slug}: no {song.lyrics_file}")
            continue
        cues = parse_lyrics(lyrics.read_text(encoding="utf-8"))
        timeline = song.timeline()
        duration = project_length_seconds(song)

        ass_path = song.path("video", f"{song.slug}.ass")
        ass_path.parent.mkdir(parents=True, exist_ok=True)
        ass_path.write_text(
            build_ass(cues, timeline, end_seconds=duration, title=song.title),
            encoding="utf-8",
        )

        audio = None
        if args.with_audio:
            for candidate in (song.path("render", f"{song.slug}.wav"), song.source_path()):
                if candidate and Path(candidate).exists():
                    audio = candidate
                    break

        target = song.path("video", f"{song.slug}.mp4")
        _say(f"── {song.title}: rendering {duration:.1f}s"
             + (f" with audio from {Path(audio).name}" if audio else " (silent)"))
        render_video(
            target, ass_path,
            duration=duration,
            images=image_segments(cues, timeline, duration),
            image_dir=song.dir,
            audio=audio,
            crf=args.crf,
        )
        _say(f"→ {target}")
        _mark(song, "video")
    return 0


def cmd_setlist_show(args: argparse.Namespace) -> int:
    from .setlist import Setlist, find_setlist, running_order

    project = _project()
    setlist = Setlist.load(find_setlist(project, args.setlist))
    songs = setlist.resolve(project)
    text = running_order(setlist, songs, gap_seconds=args.gap)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        _say(f"wrote {args.out}")
    else:
        _say(text)
    return 0


def cmd_set_status(args: argparse.Namespace) -> int:
    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        song.status[args.stage] = args.state
        save_song(song)
        _say(f"{song.album}/{song.slug}: {args.stage} = {args.state}")
    return 0


def cmd_patch_add(args: argparse.Namespace) -> int:
    project = _project()
    song = load_song(project.find_song_dir(args.song))
    song.patch_changes = [c for c in song.patch_changes if c.bar != args.bar]
    song.patch_changes.append(PatchChange(bar=args.bar, memory=args.memory, name=args.name))
    song.patch_changes.sort(key=lambda c: c.bar)
    song.validate()
    save_song(song)
    _say(f"{song.title}: bar {args.bar} -> {args.memory.upper()}")
    return 0


def cmd_section_add(args: argparse.Namespace) -> int:
    project = _project()
    song = load_song(project.find_song_dir(args.song))
    song.sections = [s for s in song.sections if s.bar != args.bar]
    song.sections.append(Section(name=args.name, bar=args.bar))
    song.sections.sort(key=lambda s: s.bar)
    song.validate()
    save_song(song)
    _say(f"{song.title}: bar {args.bar} -> {args.name}")
    return 0


# ── parser ───────────────────────────────────────────────────────────────
def _add_song_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("song", nargs="*", help="song reference (slug, album/slug or path)")
    parser.add_argument("--album", help="operate on every song in this album")
    parser.add_argument("--all", action="store_true", help="operate on every song")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rambass",
        description="Backing-track tooling for Ramba S.S. live shows.",
        epilog=EPILOGUE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"rambass {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("doctor", help="check ffmpeg / librosa / demucs are usable")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("list", help="list songs")
    p.add_argument("--album")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="show one song's tempo, sections and patch changes")
    _add_song_args(p)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("status", help="progress board across both albums")
    p.add_argument("--album")
    p.add_argument("--markdown", action="store_true", help="emit a markdown table")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("check", help="validate every song.yaml")
    p.add_argument("--album")
    p.add_argument("--no-audio-check", action="store_true",
                   help="skip the 'is the source audio present' check")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("new", help="scaffold a new song folder")
    p.add_argument("title")
    p.add_argument("--album", required=True)
    p.add_argument("--slug")
    p.add_argument("--track", type=int, default=0, help="track number (default: next free)")
    p.add_argument("--bpm", type=float, default=120.0)
    p.add_argument("--time-signature", default="4/4")
    p.add_argument("--count-in", type=int, default=2, help="count-in length in bars")
    p.add_argument("--bars", type=int, default=0, help="song length in bars, if known")
    p.add_argument("--drums", default="extracted",
                   choices=("recorded", "extracted", "programmed"))
    p.add_argument("--create-album", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("analyze", help="detect tempo, drift and the first downbeat")
    _add_song_args(p)
    p.add_argument("--file", help="analyse this audio file instead of source/")
    p.add_argument("--write", action="store_true", help="write the tempo into song.yaml")
    p.add_argument("--round-to", type=float, default=0.5, help="BPM rounding (default 0.5)")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("stems", help="separate a mix into stems with demucs")
    _add_song_args(p)
    p.add_argument("--model", default="htdemucs_ft")
    p.add_argument("--drums-only", action="store_true",
                   help="two-stem mode: drums + everything else (much faster)")
    p.add_argument("--device", help="cpu, cuda or mps")
    p.add_argument("--shifts", type=int, default=1, help="more shifts = cleaner, slower")
    p.add_argument("--jobs", type=int, default=1)
    p.set_defaults(func=cmd_stems)

    drums = sub.add_parser("drums", help="drum MIDI: transcribe, import, clean, remap")
    drums_sub = drums.add_subparsers(dest="drums_command", metavar="<subcommand>")

    p = drums_sub.add_parser("transcribe", help="drum stem -> MIDI (first pass)")
    _add_song_args(p)
    p.add_argument("--file", help="transcribe this file instead of stems/drums.*")
    p.add_argument("--offset", type=float, default=None,
                   help="seconds to bar 1 beat 1 (default: detect)")
    p.add_argument("--no-cymbals", action="store_true",
                   help="treat every high-band hit as a closed hat")
    p.set_defaults(func=cmd_drums_transcribe)

    p = drums_sub.add_parser("import", help="import an existing drum MIDI performance")
    p.add_argument("song")
    p.add_argument("file")
    p.add_argument("--channel", type=int, help="only read this MIDI channel (1-16)")
    p.add_argument("--map", help="drum map the file was written with")
    p.add_argument("--retempo", action="store_true",
                   help="re-time onto the manifest's grid instead of the file's")
    p.set_defaults(func=cmd_drums_import)

    p = drums_sub.add_parser("clean", help="de-flam, quantise and shape a drum MIDI")
    _add_song_args(p)
    p.add_argument("--file")
    p.add_argument("--input", default="raw", help="input variant (default: raw)")
    p.add_argument("--output", default="quantized", help="output variant")
    p.add_argument("--subdivision", type=int, default=4, help="4 = 16ths in 4/4")
    p.add_argument("--strength", type=float, default=1.0, help="0..1")
    p.add_argument("--swing", type=float, default=0.0, help="0 straight, 0.33 shuffle")
    p.add_argument("--deflam-ms", type=float, default=25.0, help="0 disables de-flam")
    p.add_argument("--tolerance", type=float, default=0.35,
                   help="snap only within this fraction of a grid step "
                        "(0.5 = always snap, default 0.35)")
    p.add_argument("--force-grid", action="store_true",
                   help="snap even hits far from the grid")
    p.add_argument("--humanize", type=float, default=0.0, help="± timing jitter in ms")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--accent-kick", type=int, default=0, help="fixed kick velocity")
    p.add_argument("--accent-snare", type=int, default=0, help="fixed snare velocity")
    p.add_argument("--downbeat-boost", type=int, default=0)
    p.set_defaults(func=cmd_drums_clean)

    p = drums_sub.add_parser("remap", help="move a drum MIDI onto another kit's mapping")
    p.add_argument("song")
    p.add_argument("to_map", metavar="to-map")
    p.add_argument("--from-map", dest="from_map")
    p.add_argument("--input", default="quantized")
    p.add_argument("--file")
    p.add_argument("--set-default", action="store_true")
    p.set_defaults(func=cmd_drums_remap)

    p = sub.add_parser("click", help="render the click track")
    _add_song_args(p)
    p.add_argument("--bars", type=int, default=0, help="override the song length")
    p.add_argument("--sample-rate", type=int, default=48000)
    p.add_argument("--level", type=float, default=-9.0, help="click level in dBFS")
    p.set_defaults(func=cmd_click)

    reaper = sub.add_parser("reaper", help="generate Reaper build scripts")
    reaper_sub = reaper.add_subparsers(dest="reaper_command", metavar="<subcommand>")

    p = reaper_sub.add_parser("build", help="one project per song")
    _add_song_args(p)
    p.add_argument("--midi", default="quantized", help="which drum MIDI variant to place")
    p.add_argument("--no-reference", action="store_true",
                   help="leave out the reference stems and mix")
    p.set_defaults(func=cmd_reaper_build)

    p = reaper_sub.add_parser("setlist", help="one project for the whole show")
    p.add_argument("setlist")
    p.add_argument("--gap", type=float, default=4.0, help="seconds between songs")
    p.set_defaults(func=cmd_reaper_setlist)

    gx = sub.add_parser("gx100", help="BOSS GX-100 pedalboard MIDI")
    gx_sub = gx.add_subparsers(dest="gx100_command", metavar="<subcommand>")

    p = gx_sub.add_parser("midi", help="write the patch-change MIDI for a song")
    _add_song_args(p)
    p.add_argument("--lead-ms", type=float, default=120.0,
                   help="send each change this early (default 120)")
    p.set_defaults(func=cmd_gx100_midi)

    p = gx_sub.add_parser("map", help="print the bank/PC -> memory table")
    p.add_argument("--limit", type=int, default=32, help="0 for the full table")
    p.set_defaults(func=cmd_gx100_map)

    p = gx_sub.add_parser("sheet", help="printable patch sheet for the pedalboard")
    p.add_argument("--setlist")
    p.add_argument("--album")
    p.add_argument("--out")
    p.set_defaults(func=cmd_gx100_sheet)

    video = sub.add_parser("video", help="lyric and visual video")
    video_sub = video.add_subparsers(dest="video_command", metavar="<subcommand>")

    p = video_sub.add_parser("ass", help="write the subtitle file only")
    _add_song_args(p)
    p.set_defaults(func=cmd_video_ass)

    p = video_sub.add_parser("render", help="render the MP4")
    _add_song_args(p)
    p.add_argument("--with-audio", action="store_true", help="mux the backing track in")
    p.add_argument("--crf", type=int, default=20)
    p.set_defaults(func=cmd_video_render)

    p = sub.add_parser("setlist", help="show a setlist running order")
    p.add_argument("setlist")
    p.add_argument("--gap", type=float, default=30.0, help="seconds of talking between songs")
    p.add_argument("--out")
    p.set_defaults(func=cmd_setlist_show)

    p = sub.add_parser("mark", help="set a pipeline stage's status")
    _add_song_args(p)
    p.add_argument("stage", choices=STAGES)
    p.add_argument("state", choices=STATUS_VALUES)
    p.set_defaults(func=cmd_set_status)

    p = sub.add_parser("patch", help="add or replace a GX-100 patch change")
    p.add_argument("song")
    p.add_argument("bar", type=int)
    p.add_argument("memory")
    p.add_argument("--name", default="")
    p.set_defaults(func=cmd_patch_add)

    p = sub.add_parser("section", help="add or replace a section marker")
    p.add_argument("song")
    p.add_argument("bar", type=int)
    p.add_argument("name")
    p.set_defaults(func=cmd_section_add)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except ProjectError as exc:
        print(f"rambass: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # Something downstream closed the pipe — `rambass list | head` is the
        # usual case. Point stdout at /dev/null so the interpreter's own flush
        # on exit does not raise a second time, and exit quietly.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except Exception as exc:  # noqa: BLE001 - the CLI must not dump a traceback
        from .audio import AudioError

        if isinstance(exc, (AudioError, FileNotFoundError, ValueError)):
            print(f"rambass: {exc}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
