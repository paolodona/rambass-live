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
from .manifest import (
    DRUM_ORIGINS,
    STAGES,
    STATUS_VALUES,
    PatchChange,
    Section,
    Song,
    load_song,
    save_song,
)
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


def use_utf8() -> None:
    """Make stdout and stderr able to carry the characters the CLI prints.

    Measured on Paolo's machine before this existed::

        $ rambass sections manlio
        rambass: 'charmap' codec can't encode characters in position 0-1:
                 character maps to <undefined>

    Every report here opens with ``──``, the tables use ``→`` and the prose uses
    en dashes. On Windows a console that has not been switched to UTF-8 hands
    Python a ``cp1252`` stdout, so the *first* line of output raises
    ``UnicodeEncodeError`` and the command produces nothing at all — with an
    error message that points at a codec rather than at a codepage.

    Setting ``PYTHONIOENCODING=utf-8`` in a shell profile fixes it for one person
    on one machine. This fixes it everywhere, which matters because
    docs/setup.md promises these commands run on a venue laptop and a venue is
    the last place anybody will debug a codepage.

    Everything here is best-effort on purpose. ``sys.stdout`` can be ``None``
    (pythonw, a frozen build), or something without ``reconfigure`` (a pipe
    wrapper, a test double), and none of that may take the CLI down before it has
    printed a word.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None or getattr(stream, "encoding", "").lower() in (
                "utf-8", "utf8"):
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            pass


def _say(message: str = "") -> None:
    print(message)


def _mmss(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"


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
        if song.excluded and not args.all_songs:
            continue
        done, total = song.progress()
        tail = "CUT" if song.excluded else f"{done}/{total}"
        _say(
            f"{song.album:<22} {song.title.ljust(width)}  "
            f"{song.bpm:>6.1f} BPM  {song.time_signature[0]}/{song.time_signature[1]}  "
            f"{song.drums_origin:<14} {tail}"
        )
    cut = [s for s in songs if s.excluded]
    if cut and not args.all_songs:
        _say(f"\n{len(cut)} song(s) cut from the set — `rambass list --all-songs` "
             "to see them")
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
        if song.excluded:
            continue
        found = song.problems()
        if (not args.no_audio_check
                and song.drums_origin not in ("programmed", "a-cappella")
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
    )
    song.status = song.default_status()
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
            # The tempo question is not a decision any more — the drums are
            # always re-programmed to a fixed grid — so analysis is done as soon
            # as the tempo is known. Wander only predicts how much hand-checking
            # the transcription will want; it does not hold the stage open.
            song.status["analyze"] = "done"
            save_song(song)
            _say(f"→ wrote bpm {song.bpm:g} to song.yaml")
            if not analysis.steady:
                _say(f"   the take wanders {analysis.wander_pp:.0f} ms against that grid "
                     f"(budget {analysis.subdivision_budget_ms:.0f} ms) — check the "
                     f"`drums clean` report for hits snapped to the wrong subdivision")
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
        drums = result.stems.get("drums")
        if drums:
            _stamp(song, drums, "stems", [source])
        _mark(song, "stems")
    return 0


def _stamp(song, artifact, step: str, inputs) -> None:
    """Record what produced *artifact*, so `rambass stale` can tell later.

    Deliberately best-effort: a failure to write provenance must never fail the
    command that just produced a good file.
    """
    from .provenance import stamp

    try:
        stamp(song, artifact, step=step, inputs=[p for p in inputs if p])
    except OSError:
        pass


def _warn_if_stale(song, artifact) -> None:
    """Say so before a command builds on something outdated.

    The alert belongs *here* rather than only in `rambass stale`, because nobody
    runs a status command before every step -- and the failure this exists to stop
    is somebody spending an evening listening to a file that two commits of fixes
    never reached.
    """
    from .provenance import stale_report

    relative = str(artifact).replace("\\", "/")
    for entry in stale_report(song):
        if not relative.endswith(entry.artifact):
            continue
        if entry.state in ("stale", "unknown"):
            _say(f"   ! {entry.artifact} is {entry.state}: "
                 f"{'; '.join(entry.reasons)}")
            _say(f"     re-run `{entry.command}` first, or accept it knowingly")
        return


def cmd_stale(args: argparse.Namespace) -> int:
    """Which derived files are out of date, and which of the three reasons.

    Paolo: "how do I avoid working on stale files?" An artifact goes stale three
    ways and only one of them is the one make would catch: an input changed, the
    manifest changed, or **the code that produced it changed**. The third is the
    one that bit us -- drums-quantized.mid was two commits old with the wrong
    articulation in it, and every timestamp on disk said it was current.

    It reports and prints the command. It does not rebuild: a re-transcription is
    minutes of CPU and it can change the part under you, so that decision belongs
    to somebody who is about to listen to the result.
    """
    from .provenance import stale_report

    MARK = {"ok": "ok", "missing": "--", "unknown": "??", "stale": "STALE",
            "edited": "EDITED"}
    project = _project()
    todo: list[str] = []
    for song in _songs(project, args.song, args.album, args.all):
        entries = stale_report(song)
        if not entries:
            continue
        interesting = [e for e in entries if e.state != "ok"]
        if args.quiet and not [e for e in interesting if e.state != "missing"]:
            continue
        _say(f"── {song.title}")
        for entry in entries:
            if args.quiet and entry.state == "ok":
                continue
            _say(f"   {MARK[entry.state]:<7}{entry.artifact:<34}"
                 f"{'; '.join(entry.reasons)}")
            if entry.state in ("stale", "missing"):
                todo.append(entry.command)
        _say()
    if todo:
        _say("In order:")
        seen = set()
        for command in todo:
            if command not in seen:
                seen.add(command)
                _say(f"   {command}")
        _say()
        _say("Nothing was rebuilt. A re-transcription can change the part, so")
        _say("run these when you are ready to listen to the result.")
    else:
        _say("Everything derived is current.")
    return 0


# ── commands: drums ──────────────────────────────────────────────────────
def _part_stems(song) -> dict[str, Path]:
    """The five isolated part stems, if the Stage 2 split has been run.

    docs/drums-rebuild.md Stage 2: the split itself is an external tool, so what
    the repo can do is notice its output and use it.
    """
    found = {}
    for part in ("kick", "snare", "toms", "hihat", "cymbals"):
        candidate = song.path("stems", "parts", f"{part}.wav")
        if candidate.exists():
            found[part] = candidate
    return found


def _write_align(song, anchor: float, report: dict) -> None:
    """Record a measured anchor so it is never silently re-derived.

    Only the source side is in seconds — the target is the musical grid, so a
    BPM edit keeps working (CLAUDE.md, docs/practice-tracks.md).
    """
    path = song.path("practice", "align.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    source = song.source_path()
    lines = [
        "# Where this song's musical grid sits inside the original recording.",
        "# Measured by analyze.find_grid_anchor. Correct it by ear if it is wrong:",
        "# `rambass drums transcribe` reads this file before measuring anything.",
        f'source: "{source.name if source else ""}"',
        f"detected_bpm: {song.bpm:g}",
        "mode: offset",
        "anchors:",
        f"  - {{bar: 1, at: {anchor:.3f}}}",
        f"evidence: {{on_beat: {report.get('on_beat', 0)}, "
        f"runner_up: {report.get('runner_up_on_beat', 0)}, "
        f"on_subdivision: {report.get('on_subdivision', 0)}}}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_drums_transcribe(args: argparse.Namespace) -> int:
    from .analyze import analyze_tempo
    from .midiio import write_drum_midi
    from .transcribe import (
        bands_with_hat_delta, detect_anchor, transcribe_drums, transcribe_parts,
    )

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        parts = {} if args.no_parts else _part_stems(song)
        if parts and not args.file:
            kit = song.stem_path("drums")
            offset, anchor_report = args.offset, {}
            if offset is None:
                # Measured, not guessed — see analyze.find_grid_anchor for what
                # guessing cost. An anchor read off a stored align.yaml wins,
                # because a human may have corrected it by ear.
                offset = song.align_anchor()
                if offset is None and kit:
                    offset, anchor_report = detect_anchor(kit, song.bpm)
                offset = offset or 0.0
            _say(f"── {song.title}: {len(parts)} part stems "
                 f"({', '.join(sorted(parts))}), offset {offset:.3f}s")
            performance, reports = transcribe_parts(
                parts, timeline=song.timeline(), offset=offset, kit_mix=kit,
                subdivision=song.drum_subdivision,
                bands=bands_with_hat_delta(args.hat_delta),
            )
            reports["kit"].anchor_seconds = offset
            reports["kit"].anchor_report = anchor_report
            _say(reports["kit"].summary())
            if anchor_report and reports["kit"].confidence.get("ratio", 0) >= 1.8:
                _write_align(song, offset, anchor_report)
                _say(f"   recorded the anchor in {song.path('practice', 'align.yaml')}")
            if reports["kit"].confidence.get("ratio", 0) < 1.8:
                _say("   ^ this part does not sit on beats. Do not build on it — "
                     "re-measure the anchor (docs/drums.md) before going further.")
            target = song.drum_midi_path("raw")
            write_drum_midi(target, performance, load_drum_map(song.drum_map, project))
            # Not align.yaml: only the bar-1 anchor out of it matters here, and
            # provenance records that as a scalar. See provenance.SCALARS.
            _stamp(song, target, "drums transcribe", [*parts.values(), kit])
            _say(f"→ {target}  ({len(performance.hits)} hits)")
            _mark(song, "drums_midi", "wip")
            continue
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
            follow=not args.no_follow,
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
    from .quantize import (
        CYMBALS, DEFAULT_SUBDIVISIONS, QuantizeSettings, deflam, humanize,
        quantize, shape_velocities, trim_to_bars,
    )

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
        _warn_if_stale(song, source)

        # The song's own statement of where it ends bounds the part. A stem runs
        # to the end of the album track, so a transcription happily reports
        # whatever is out there after the band stopped: on Manlio that was a
        # sung note two seconds after the final snare, which arrived as a lone
        # hi-hat at the velocity floor in bar 79 and read exactly like a
        # phantom. It was real audio and correctly detected -- just not the drum
        # part, and not played at the gig. bars: 0 means unmeasured, which is not
        # the same as zero, so it trims nothing.
        if song.bars:
            before = len(performance.hits)
            performance = trim_to_bars(performance, 1, song.bars)
            if before != len(performance.hits):
                _say(f"   trim          -{before - len(performance.hits)} hits "
                     f"outside bars 1-{song.bars}")

        if args.deflam_ms > 0:
            performance, removed = deflam(performance, args.deflam_ms)
            _say(f"   de-flam       -{removed} duplicate hits "
                 f"(within {args.deflam_ms:g} ms)")

        # Before quantising, so the declared stroke is the one that gets snapped,
        # and well before `drums consolidate`, so the section's vote sees a
        # consistent backbeat and stamps it across the bars that missed one.
        if not args.no_voicing and any(s.backbeat for s in song.sections):
            from .restore import voice_backbeats

            # The level belongs to the song, so the manifest wins over the
            # measured median and an explicit flag wins over both. 0 in the
            # manifest means "not decided", not velocity 0.
            backbeat_velocity = (args.backbeat_velocity
                                 if args.backbeat_velocity is not None
                                 else (song.drum_backbeat_velocity or None))
            performance, voicing = voice_backbeats(
                performance, song.sections,
                end_bar=(song.bars or song.total_bars()) + 1,
                velocity=backbeat_velocity)
            for entry in voicing["sections"]:
                _say(f"   voicing       {entry['name']:<18} "
                     f"{entry['renamed']} -> {entry['articulation']} "
                     f"at v{entry['velocity'] if entry['velocity'] else '(unchanged)'}, "
                     f"{entry['dropped']} coincident hand hits dropped"
                     + (f", {entry['empty_slots']} empty slots left for the vote"
                        if entry["empty_slots"] else ""))

        # The grid belongs to the song, so the manifest wins over the built-in
        # default and an explicit flag wins over both.
        subdivision = (args.subdivision if args.subdivision is not None
                       else song.drum_subdivision)
        cymbal_subdivision = (args.cymbal_subdivision
                              if args.cymbal_subdivision is not None
                              else song.drum_cymbal_subdivision)

        # DEFAULT_SUBDIVISIONS is an *absolute* table, so it silently wins over
        # `subdivision` for every instrument it lists -- which is all of them
        # except the toms. An explicit subdivision could therefore never change
        # the grid the kick, snare or hats were snapped to. Build the table here
        # so the setting means what it says. The defaults reproduce
        # DEFAULT_SUBDIVISIONS exactly: everything on `subdivision`, the crash
        # family a step coarser.
        per_instrument = {
            name: (cymbal_subdivision if name in CYMBALS else subdivision)
            for name in DEFAULT_SUBDIVISIONS
        }
        performance, report = quantize(performance, QuantizeSettings(
            subdivision=subdivision,
            strength=args.strength,
            swing=args.swing,
            tolerance_steps=args.tolerance,
            per_instrument=per_instrument,
            force=args.force_grid,
        ))
        advice = song.grid_advice()
        if advice and args.subdivision is None and args.cymbal_subdivision is None:
            _say(f"   ! {advice}")
        grid = ("triplet 8ths" if subdivision == 3 else
                "16ths" if subdivision == 4 else f"{subdivision}/beat")
        _say(f"   quantise      {report['hits']} hits on {grid} "
             f"(cymbals {cymbal_subdivision}/beat), "
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
        _stamp(song, target, "drums clean", [source])
        _say(f"→  {target}")
        _mark(song, "quantize")
    return 0


def cmd_align(args: argparse.Namespace) -> int:
    """Fit ``practice/align.yaml`` from detected beats, and warp a reference.

    Paolo: "I would like to rebuild the drums... until I have them over the
    original time warped track (minus the original drums), so I can hear them in
    context." A single offset cannot do that — Manlio's own align.yaml records
    that the band drifts -88 to +258 ms across the song, so a reference laid at
    one offset flams by a quarter second by the end.

    Practice scope (CLAUDE.md, docs/practice-tracks.md): this writes only under
    the song's `practice/` folder, touches no musical field in `song.yaml`, and is
    not a pipeline stage. It must never gate the gig.
    """
    from .align import (
        AlignMap, Anchor, fit_anchors, load_align, plan_problem,
        residual_holdout_ms, residual_ms, save_align, warp_plan, warp_samples,
    )
    from .analyze import analyze_tempo
    from .audio import load_audio, write_wav

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        target = song.path("practice", "align.yaml")
        timeline = song.timeline()
        bars = song.bars or song.total_bars()
        amap = load_align(target)

        if args.fit:
            reference = song.stem_path("drums") or song.source_path()
            if not reference:
                _say(f"{song.slug}: nothing to fit against — separate the stems "
                     f"or put the source mix in place first")
                continue
            _say(f"── {song.title}: fitting anchors against {reference.name}")
            analysis = analyze_tempo(reference)
            # The stored anchor, when there is one, *starts the chain* rather
            # than being patched over bar 1 afterwards -- see fit_anchors for
            # what patching it afterwards produced.
            anchors = fit_anchors(
                analysis.beat_times, timeline, bars=bars,
                every_beats=args.every_beats,
                start_at=amap.offset if (amap.anchors and args.keep_bar_one)
                else None)
            if not anchors:
                _say("   no beat landed near a bar line — the anchor is probably "
                     "wrong, or this is the wrong reference file")
                continue
            fitted = AlignMap(
                anchors=anchors,
                source=(song.source_audio or reference.name),
                detected_bpm=song.bpm,
            )
            # Leave-one-out, not "fit against the beats it was fitted from":
            # at one anchor per beat the latter is circular and reports 0 ms
            # however bad the map is. See align.residual_holdout_ms.
            worst, mean = residual_holdout_ms(fitted, timeline)
            if worst is None:
                worst, mean = residual_ms(fitted, analysis.beat_times, timeline)
                fitted.note = "residual against the beats it was fitted from"
            else:
                fitted.note = ("leave-one-out over the anchors -- the gap "
                               "between them is the only thing interpolation "
                               "can get wrong")
            fitted.residual_max_ms, fitted.residual_mean_ms = worst, mean
            _say(f"   {len(fitted.anchors)} anchors every "
                 f"{args.every_beats} beat(s), residual {worst:.0f} ms worst / "
                 f"{mean:.0f} ms mean")
            if worst > 50.0:
                # The residual is measured against librosa's own beat times, so
                # part of it is the beat tracker rather than the map. With an
                # anchor on every bar the map passes exactly through every bar
                # line and what is left is the swing inside the bar, which a
                # linear segment cannot follow and a reference track does not
                # need it to.
                _say(f"   ^ worst case is over the 50 ms in "
                     f"docs/practice-tracks.md."
                     + (" Try --every-beats 1." if args.every_beats > 1 else
                        " With an anchor on every beat this is the beat "
                        "tracker's own jitter, not a bad fit — fine for a "
                        "reference, which is all this is."))
            problem = plan_problem(fitted, timeline, bars=bars)
            if problem and not args.force:
                _say(f"   ! {problem}")
                _say(f"     NOT written — the map you have still works. Try a "
                     f"finer --every-beats, or --force to write it anyway.")
                continue
            if args.dry_run:
                _say("   dry run — nothing written.")
            else:
                save_align(target, fitted)
                _stamp(song, target, "align fit", [song.stem_path("drums")])
                _say(f"→  {target}")
            amap = fitted

        if not args.warp:
            continue
        if amap.mode == "none":
            _say(f"{song.slug}: no anchors yet — run `rambass align {song.slug} "
                 f"--fit` first")
            continue
        source = song.stem_path(args.stem)
        if not source:
            _say(f"{song.slug}: no stems/{args.stem}.wav to warp")
            continue
        plan = warp_plan(amap, timeline, bars=bars)
        samples, sample_rate = load_audio(source)
        # Both channels in one call: WSOLA picks its offset per frame, and
        # picking it per channel would decorrelate the sides.
        warped = warp_samples(samples, sample_rate, plan)
        rates = [segment.rate for segment in plan]
        _say(f"── {song.title}: warped {source.name} over {len(plan)} segments, "
             f"rate {min(rates):.3f}-{max(rates):.3f}")
        if args.dry_run:
            _say("   dry run — nothing written.")
            continue
        out = song.path("practice", f"{args.stem}-aligned.wav")
        write_wav(out, warped, sample_rate)
        _stamp(song, out, "align warp", [target, source])
        _say(f"→  {out}")
        _say("   Drop it on a track at the count-in and play the new drums "
             "against it. It is a reference, not a deliverable.")
    _say()
    return 0


def cmd_drums_missing(args: argparse.Namespace) -> int:
    """The ledger: everything the pipeline removed or never named.

    Paolo: "There needs to be a clear list of missing hits so I dont mistakenly
    forget some in the process." Every deliberate omission upstream is knowable,
    so this writes them down in Reaper bar numbers and ticks off the ones already
    covered by `drums.additions`.
    """
    from .midiio import read_drum_midi
    from .restore import (
        NOTICEABLE, checklist, crash_candidates, group_missing, missing_hits,
        propose_additions,
    )

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        before_path = song.drum_midi_path(args.before)
        after_path = song.drum_midi_path(args.after)
        for path in (before_path, after_path):
            if not path.exists():
                _say(f"{song.slug}: no {path.name} — run the earlier stages first")
                break
        else:
            drum_map = load_drum_map(song.drum_map, project)
            before = read_drum_midi(before_path, drum_map)
            after = read_drum_midi(after_path, drum_map)
            before.timeline = after.timeline = song.timeline()
            end_bar = (song.bars or song.total_bars()) + 1
            items = missing_hits(
                before, after, song.sections, end_bar=end_bar,
                subdivision=song.drum_subdivision,
                instruments=None if args.everything else NOTICEABLE,
                reason=f"in {before_path.name}, gone from {after_path.name}")
            if not args.no_crashes:
                items = sorted(
                    items + crash_candidates(after, song.sections, end_bar=end_bar),
                    key=lambda item: item.position)

            groups = group_missing(items)
            _say(f"── {song.title}: {len(groups)} things to put back, "
                 f"{len(items)} hits ({before_path.name} → {after_path.name})")
            _say(f"   {'reaper':>8}  {'instrument':<16}{'section':<18}why")
            for group in groups:
                many = f" x{group.count}" if group.count > 1 else ""
                _say(f"   {group.bar + song.count_in_bars:>5}.{group.beat:<3g}  "
                     f"{group.instrument + many:<16}{group.section:<18}{group.reason}")
            counts: dict[str, int] = {}
            for item in items:
                counts[item.instrument] = counts.get(item.instrument, 0) + 1
            if counts:
                _say("   " + ", ".join(f"{n} {k}" for k, n in
                                       sorted(counts.items(), key=lambda kv: -kv[1])))

            if args.propose:
                proposed, skipped = propose_additions(
                    items, existing=song.drum_additions)
                if proposed:
                    song.drum_additions = list(song.drum_additions) + proposed
                    save_song(song)
                    _say(f"   proposed {len(proposed)} additions into "
                         f"drums.additions ({skipped} left for your ears) — "
                         f"read the diff, then `rambass drums restore "
                         f"{song.slug}`")
                else:
                    _say(f"   nothing confident enough to propose "
                         f"({skipped} left for your ears)")

            target = song.path("qa", "missing-hits.md")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                checklist(song.title, items, count_in_bars=song.count_in_bars,
                          additions=song.drum_additions),
                encoding="utf-8")
            _say(f"→  {target}")
    _say()
    return 0


def cmd_review_rebuild(args: argparse.Namespace) -> int:
    """Run every stale or missing pipeline step for a song, in order.

    The console's rebuild button, as a command — and a command *first*, so a
    terminal gets exactly what the button does. It packages `rambass stale`'s
    judgement without changing it: `stale` and `missing` steps run, via the
    exact commands that report already prints; `edited` and `unknown` are
    reported and never touched, because a hand edit's risk runs the opposite
    way — re-running the step throws it away. `--force <artifact>` overrides
    for one artifact at a time, and writes a `.bak` first: a one-key rebuild
    must not make that mistake easier to make than the terminal already does.
    """
    import shutil

    from . import review
    from .provenance import stale_report

    project = _project()
    failed = False
    for song in _songs(project, args.song, args.album, False):
        auto, held = review.rebuild_selection(stale_report(song))
        if args.step:
            auto = [e for e in auto if e.step == args.step]

        forced = []
        if args.force:
            match = [e for e in held if e.artifact == args.force]
            if not match:
                held_names = ", ".join(e.artifact for e in held) or "none"
                raise ProjectError(
                    f"--force {args.force}: not a held (edited/unknown) "
                    f"artifact of {song.slug}. Held right now: {held_names}. "
                    f"A stale artifact rebuilds without --force.")
            path = song.path(*args.force.split("/"))
            if path.exists():
                backup = path.with_suffix(path.suffix + ".bak")
                shutil.copy2(path, backup)
                _say(f"{song.slug}: kept a copy of the hand-edited file at "
                     f"{backup.name}")
            forced = match

        todo = auto + forced
        if not todo:
            _say(f"{song.slug}: nothing stale or missing — nothing to rebuild")
        elif args.dry_run:
            _say(f"{song.slug}: would run, in order:")
            for entry in todo:
                _say(f"   {entry.command}")
        else:
            report = review.run_rebuild(
                todo, runner=review.subprocess_runner(project.root))
            _say(f"{song.slug}: ran {report['ran']} of {len(todo)} steps")
            if report["failed"]:
                _say(f"   FAILED: {report['failed']} — stopping here; the "
                     f"steps after it still need their inputs")
                failed = True

        for entry in held:
            if entry in forced:
                continue
            _say(f"   left alone ({entry.state}): {entry.artifact} — "
                 f"{'; '.join(entry.reasons)}")
    return 1 if failed else 0


def cmd_drums_restore(args: argparse.Namespace) -> int:
    """Stage 7: reapply the hand edits declared in ``song.yaml``.

    Its own variant on purpose, the same argument `drums consolidate` makes: the
    unrestored part stays on disk so the two can be A/B'd, and a re-run of the
    whole pipeline reproduces the restored file rather than losing it.
    """
    from .midiio import read_drum_midi, write_drum_midi
    from .restore import apply_edits

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        source = song.drum_midi_path(args.input)
        if not source.exists():
            _say(f"{song.slug}: no {source.name} — consolidate it first")
            continue
        if not song.drum_additions and not song.drum_removals:
            _say(f"{song.slug}: no drums.additions or drums.removals in "
                 f"song.yaml. `rambass drums missing {song.slug}` lists what "
                 f"Stage 7 has to put back.")
            continue
        drum_map = load_drum_map(song.drum_map, project)
        performance = read_drum_midi(source, drum_map)
        performance.timeline = song.timeline()
        performance, report = apply_edits(
            performance,
            additions=song.drum_additions, removals=song.drum_removals)
        _say(f"── {song.title}: {len(performance.hits)} hits from {source.name}")
        _say(f"   restore       +{report['added']} added, "
             f"-{report['removed']} removed, "
             f"{report['already_there']} already there"
             + (f", {report['velocity_from_median']} took the instrument's "
                f"median velocity" if report["velocity_from_median"] else ""))
        for bar, beat, instrument in report["stale_removals"]:
            _say(f"   ! stale removal at bar {bar} beat {beat:g}"
                 f"{' for ' + instrument if instrument else ''} matched nothing — "
                 f"the hit it names is already gone, so drop the line")
        if args.dry_run:
            _say("   dry run — nothing written.")
            continue
        target = song.drum_midi_path(args.output)
        write_drum_midi(target, performance, drum_map)
        _stamp(song, target, "drums restore", [source])
        _say(f"→  {target}")
    _say()
    return 0


def cmd_drums_consolidate(args: argparse.Namespace) -> int:
    """Stage 6: replace each section with the pattern its bars agree on.

    Its own command and its own variant, deliberately. `consolidate` removes the
    fills by design — a fill is the bar that does not repeat, so no threshold can
    keep it and be doing its job — and on a song like Manlio that is the breaks,
    the closing fill and every tom run, all to be put back by hand at Stage 7.
    Folding that into `drums clean` would make a large musical change a side
    effect of a command that is run constantly, and would overwrite the only copy
    of the unconsolidated part. Keeping both on disk is what lets it be judged by
    ear.
    """
    from .midiio import read_drum_midi, write_drum_midi
    from .quantize import ConsolidateSettings, consolidate

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        source = song.drum_midi_path(args.input)
        if not source.exists():
            _say(f"{song.slug}: no {source.name} — transcribe and clean it first")
            continue
        spans = song.consolidation_spans()
        if not spans:
            _say(f"{song.slug}: no sections, so there is nothing to vote within. "
                 f"Add them with `rambass section` — `rambass sections {song.slug}` "
                 f"reviews them.")
            continue

        drum_map = load_drum_map(song.drum_map, project)
        performance = read_drum_midi(source, drum_map)
        performance.timeline = song.timeline()
        _say(f"── {song.title}: {len(performance.hits)} hits from {source.name}, "
             f"{len(spans)} sections")
        _warn_if_stale(song, source)

        performance, report = consolidate(performance, spans, settings=ConsolidateSettings(
            subdivision=args.subdivision or song.drum_subdivision,
            threshold=args.threshold,
            unit_bars=args.unit_bars,
        ))

        _say(f"   {'section':<16}{'spans':>6}{'reps':>6}{'unit':>6}"
             f"{'hits':>12}{'agree':>7}")
        for entry in report["sections"]:
            if "skipped" in entry:
                _say(f"   {entry['name']:<16}{entry['spans']:>6}{'—':>6}{'—':>6}"
                     f"{entry['hits_before']:>6} kept{'':>7}   {entry['skipped']}")
                continue
            _say(f"   {entry['name']:<16}{entry['spans']:>6}{entry['repeats']:>6}"
                 f"{entry['unit_bars']:>5}b{entry['hits_before']:>6} ->"
                 f"{entry['hits_after']:>4}{entry['coverage']:>7.0%}")
        _say(f"   {'total':<16}{'':>6}{'':>6}{'':>6}"
             f"{report['hits_before']:>6} ->{report['hits_after']:>4}"
             f"   ({report['untouched']} outside every section, untouched)")

        if args.dry_run:
            _say("   dry run — nothing written.")
            continue
        target = song.drum_midi_path(args.output)
        write_drum_midi(target, performance, drum_map)
        _stamp(song, target, "drums consolidate", [source])
        _say(f"→  {target}")
    _say()
    _say("Stage 6 deliberately removes the fills: a fill is the bar that does not")
    _say("repeat, so no threshold keeps it. Put them back by hand — Stage 7 of")
    _say("docs/drums-rebuild.md. The input variant is untouched, so A/B the two.")
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
    """Render the rehearsal / overdub click for the song proper."""
    from .click import render_click_file

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        bars = args.bars or song.total_bars()
        target = Path(args.out) if args.out else song.path("render", "click.wav")
        render_click_file(
            target, song.timeline(), bars,
            sample_rate=args.sample_rate,
            accent_downbeat=bool(song.click.get("accent_downbeat", True)),
            level_db=args.level,
        )
        if not args.out:
            _stamp(song, target, "click", [])
        _say(f"{song.title}: {bars} bars at {song.bpm:g} BPM -> {target.name} "
             f"(starts at bar 1 — the count-in is the sticks stem)")
    _say()
    _say("for rehearsal and for tracking overdubs onto a finished base. Keep it")
    _say("out of the PA, and never render it into the backing track.")
    return 0


def cmd_countin(args: argparse.Namespace) -> int:
    """Render the drumstick count-in as its own stem."""
    from .audio import load_audio
    from .click import render_sticks_file

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        bars = args.bars or song.count_in_bars
        if bars < 1:
            _say(f"{song.slug}: count_in.bars is {song.count_in_bars}, nothing to render")
            continue

        sample = None
        if args.sample:
            data, _ = load_audio(args.sample, args.sample_rate, channels=1)
            sample = data[:, 0]

        target = Path(args.out) if args.out else song.path("render", "sticks.wav")
        render_sticks_file(
            target, song.timeline(), bars,
            sample_rate=args.sample_rate,
            level_db=args.level,
            sample=sample,
        )
        timeline = song.timeline()
        _say(f"{song.title}: {bars} bar count-in at {song.bpm:g} BPM "
             f"({timeline.count_in_seconds:.2f}s"
             + (f", from {Path(args.sample).name}" if args.sample else ", synthesised")
             + f") -> {target.name}")
    _say()
    _say("this is a separate stem — it sits at 0 on the timeline and the backing")
    _say("track starts where it ends. Nothing was written into the base.")
    return 0


def cmd_reaper_build(args: argparse.Namespace) -> int:
    from .reaper import build_song_script

    project = _project()
    project.reaper_build_dir.mkdir(parents=True, exist_ok=True)
    for song in _songs(project, args.song, args.album, args.all):
        script = build_song_script(
            song,
            sticks_wav=song.path("render", "sticks.wav"),
            click_wav=song.path("render", "click.wav"),
            backing_wav=song.backing_track_path(),
            drum_midi=(song.drum_midi_path(args.midi) if args.midi
                       else song.best_drum_midi()),
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
    """Assemble the whole show into one Reaper project."""
    from .reaper import build_setlist_script, plan_show
    from .setlist import Setlist, find_setlist

    project = _project()
    setlist = Setlist.load(find_setlist(project, args.setlist))
    songs = setlist.resolve(project)
    slots = plan_show(songs, gap_seconds=args.gap)

    _say(f"{setlist.name} — {len(songs)} songs")
    _say()
    _say(f"{'#':>3}  {'song':<30} {'start':>8}  {'length':>8}  from      needs")
    _say("-" * 92)
    for slot in slots:
        source = "audio" if slot.measured else "bars?"
        needs = ", ".join(slot.missing) or "-"
        _say(f"{slot.index:>3}  {slot.song.title:<30} "
             f"{_mmss(slot.start):>8}  {_mmss(slot.length):>8}  {source:<8}  {needs}")

    ready = [s for s in slots if not s.missing]
    guessed = [s for s in slots if not s.measured]
    _say()
    _say(f"{len(ready)}/{len(slots)} songs have everything the show needs")
    if guessed:
        _say(f"{len(guessed)} region length(s) came from a guessed bar count, so "
             f"those regions will not match their audio")

    script = build_setlist_script(
        songs, gap_seconds=args.gap, include_video=not args.no_video
    )
    target = project.reaper_build_dir / f"setlist-{setlist.path.stem}.rbs"
    script.write(target, header_comment=f"setlist: {setlist.name}")
    _say()
    _say(f"-> {target}")
    _say("rebuild this whenever the order changes — nothing per-song is touched")
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
        _stamp(song, target, "gx100 midi", [])
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


# ── commands: lyrics ─────────────────────────────────────────────────────
def _cue_path(song: Song, explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    found = song.lyrics_path()
    if found is None or found.suffix.lower() == ".md":
        return song.path("lyrics.srt")
    return found


def _load_cues(song: Song) -> tuple[list, str]:
    """Cues for a song plus a label saying where they came from.

    A hand-timed SRT always wins over the bar-cue markdown draft. When both
    exist the markdown is still read, but only for its ``image:`` cues — words
    from the SRT, pictures from the markdown.
    """
    from .lyrics import from_bar_cues, load
    from .reaper import project_length_seconds

    path = song.lyrics_path()
    if path is None:
        raise ProjectError(
            f"{song.slug}: no lyrics file. Write {song.path('lyrics.srt')}, "
            f"import one with `rambass lyrics import`, or draft one with "
            f"`rambass lyrics transcribe`."
        )
    if path.suffix.lower() == ".md":
        cues = from_bar_cues(
            path.read_text(encoding="utf-8"),
            song.timeline(),
            end_seconds=project_length_seconds(song),
        )
    else:
        cues = load(path)
    return cues, path.name


def cmd_lyrics_import(args: argparse.Namespace) -> int:
    from .lyrics import load, problems, save, shift, stats

    project = _project()
    song = load_song(project.find_song_dir(args.song))
    cues = load(args.file)
    if args.shift:
        cues = shift(cues, args.shift)
    target = song.path(f"lyrics{Path(args.file).suffix.lower()}")
    if args.as_srt:
        target = song.path("lyrics.srt")
    save(target, cues, title=song.title)

    _say(f"{song.title}: {len(cues)} cues -> {target}")
    for key, value in stats(cues).items():
        _say(f"   {key:<16} {value}")
    found = problems(cues)
    for problem in found[:10]:
        _say(f"   ! {problem}")
    if len(found) > 10:
        _say(f"   ! ... and {len(found) - 10} more (see `rambass lyrics check`)")
    if song.lyrics_file != target.name:
        song.lyrics_file = target.name
        save_song(song)
        _say(f"   song.yaml now points video.lyrics at {target.name}")
    _mark(song, "video", "wip")
    return 0


def cmd_lyrics_transcribe(args: argparse.Namespace) -> int:
    from .lyrics import problems, save, stats, transcribe

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        source = Path(args.file) if args.file else (
            song.stem_path("vocals") or song.source_path()
        )
        if not source:
            _say(f"{song.slug}: nothing to transcribe — no stems/vocals.* and no source/")
            continue
        from_vocals = song.stem_path("vocals") is not None and not args.file
        prompt = args.prompt
        if not prompt and args.prompt_file:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8")

        _say(f"── {song.title}: transcribing {source.name}"
             + ("" if from_vocals else "  (full mix — separate the vocals first "
                                       "with `rambass stems` for much better results)"))
        cues, report = transcribe(
            source,
            language=args.language,
            model=args.model,
            max_chars=args.max_chars,
            prompt=prompt,
            device=args.device,
        )
        for key, value in report.items():
            _say(f"   {key:<22} {value}")
        target = song.path(args.output)
        save(target, cues, title=song.title)
        _say(f"→  {target}  ({len(cues)} cues)")
        for key, value in stats(cues).items():
            _say(f"   {key:<16} {value}")
        found = problems(cues)
        if found:
            _say(f"   {len(found)} thing(s) to fix — `rambass lyrics check {song.slug}`")
        _say("   this is a draft: read every line against the audio before rendering")
    return 0


def cmd_lyrics_export(args: argparse.Namespace) -> int:
    from .lyrics import WRITERS, save, shift

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        try:
            cues, origin = _load_cues(song)
        except ProjectError as exc:
            _say(f"{exc}")
            continue
        if args.offset:
            cues = shift(cues, args.offset)
        if args.count_in:
            cues = shift(cues, song.timeline().count_in_seconds)
        for fmt in args.format:
            if fmt not in WRITERS:
                raise ProjectError(f"unknown format {fmt!r}; "
                                   f"known: {', '.join(sorted(WRITERS))}")
            target = Path(args.out) if args.out else song.path("video", f"{song.slug}.{fmt}")
            save(target, cues, title=song.title)
            _say(f"{song.title}: {origin} -> {target}  ({len(cues)} cues)")
    return 0


def cmd_lyrics_check(args: argparse.Namespace) -> int:
    from .lyrics import problems, stats
    from .reaper import project_length_seconds

    project = _project()
    total = 0
    for song in _songs(project, args.song, args.album, args.all):
        try:
            cues, origin = _load_cues(song)
        except ProjectError:
            if args.quiet:
                continue
            _say(f"{song.album}/{song.slug}: no lyrics yet")
            continue
        duration = project_length_seconds(song) if song.bars else None
        found = problems(cues, duration=duration)
        summary = stats(cues)
        _say(f"{song.album}/{song.slug}  ({origin})  "
             f"{summary['cues']} cues, {summary['words']} words, "
             f"{summary['first']}s-{summary['last']}s")
        for problem in found:
            _say(f"   ! {problem}")
        total += len(found)
    if total:
        _say(f"\n{total} problem(s) across all songs")
        return 1
    _say("\nall cue files look sane")
    return 0


def cmd_lyrics_shift(args: argparse.Namespace) -> int:
    from .lyrics import retime, save, shift

    project = _project()
    song = load_song(project.find_song_dir(args.song))
    cues, origin = _load_cues(song)
    if args.seconds:
        cues = shift(cues, args.seconds)
        _say(f"{song.title}: shifted {len(cues)} cues by {args.seconds:+g}s")
    if args.from_bpm and args.to_bpm:
        cues = retime(cues, args.from_bpm, args.to_bpm)
        _say(f"{song.title}: retimed {args.from_bpm:g} -> {args.to_bpm:g} BPM")
    target = _cue_path(song, args.out)
    if origin.endswith(".md") and not args.out:
        raise ProjectError(
            "this song's cues live in lyrics.md, which is anchored to bars — "
            "change the tempo in song.yaml instead of shifting the cues, or pass "
            "--out to write a shifted subtitle file"
        )
    save(target, cues, title=song.title)
    _say(f"→ {target}")
    return 0


def cmd_lyrics_bars(args: argparse.Namespace) -> int:
    """Convert a subtitle file into the bar-anchored markdown format."""
    from .lyrics import to_bar_cues

    project = _project()
    song = load_song(project.find_song_dir(args.song))
    cues, origin = _load_cues(song)
    text = to_bar_cues(cues, song.timeline())
    target = Path(args.out) if args.out else song.path("lyrics.bars.md")
    target.write_text(f"# {song.title}\n\n{text}", encoding="utf-8")
    _say(f"{song.title}: {origin} -> {target}")
    _say("   note: cue times were snapped to the nearest beat — this is lossy, "
         "and is meant for re-timing, not as a round trip")
    return 0


# ── commands: reaper import ──────────────────────────────────────────────
def cmd_reaper_import(args: argparse.Namespace) -> int:
    from .gx100 import load_program_map
    from .reaper import import_into_song, parse_rpp

    project = _project()
    song = load_song(project.find_song_dir(args.song))
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    parsed = parse_rpp(text)
    data, notes = import_into_song(
        parsed, song, offset=args.offset, program_map=load_program_map(project)
    )

    _say(f"── {Path(args.file).name} -> {song.album}/{song.slug}")
    for note in notes:
        _say(f"   {note}")
    _say("")
    _say(f"   would set: bpm {data['bpm']:g}, "
         f"{data['time_signature'][0]}/{data['time_signature'][1]}, "
         f"{data['bars']} bars, {len(data['sections'])} sections, "
         f"{len(data['patch_changes'])} patch changes")

    if not args.write:
        _say("")
        _say("   nothing written — re-run with --write to apply this to song.yaml")
        return 0

    song.bpm = data["bpm"]
    song.time_signature = data["time_signature"]
    if data["bars"]:
        song.bars = data["bars"]
    if data["sections"]:
        song.sections = data["sections"]
    if data["patch_changes"] and not args.keep_patches:
        song.patch_changes = data["patch_changes"]
    if data["media"] and not song.source_audio:
        song.source_audio = data["media"][0]
    song.status["analyze"] = "done"
    song.notes = (song.notes + "\n" if song.notes else "") + (
        f"imported from {Path(args.file).name} "
        f"(musical zero at {data['offset']:.3f}s in that project)"
    )
    song.validate()
    save_song(song)
    _say("")
    _say(f"   written to {song.path('song.yaml')}")
    _say("   check the section bars against the audio: the marker positions came "
         "from a project whose musical zero had to be guessed")
    return 0


def _image_cues(song: Song):
    """Image cues, which only the bar-anchored markdown format can express."""
    from .video import parse_lyrics

    markdown = song.path("lyrics.md")
    if not markdown.is_file():
        return []
    return [c for c in parse_lyrics(markdown.read_text(encoding="utf-8")) if c.image]


def cmd_video_ass(args: argparse.Namespace) -> int:
    from .lyrics import format_ass

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        try:
            cues, origin = _load_cues(song)
        except ProjectError as exc:
            _say(f"{exc}")
            continue
        target = song.path("video", f"{song.slug}.ass")
        target.parent.mkdir(parents=True, exist_ok=True)
        card_seconds = _card_seconds(song, args)
        target.write_text(
            format_ass(
                cues, title=song.title,
                card_seconds=card_seconds, card_subtitle=args.subtitle,
            ),
            encoding="utf-8",
        )
        _stamp(song, target, "video ass", [song.path("lyrics.srt")])
        _say(f"{song.title}: {origin} -> {target}  ({len(cues)} cues"
             + (f", title card for {card_seconds:.1f}s" if card_seconds else "")
             + ")")
    return 0


def cmd_video_render(args: argparse.Namespace) -> int:
    from .lyrics import format_ass
    from .reaper import project_length_seconds
    from .video import image_segments, render_video

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        try:
            cues, origin = _load_cues(song)
        except ProjectError as exc:
            _say(f"{exc}")
            continue

        timeline = song.timeline()
        duration = args.duration or max(
            project_length_seconds(song),
            (cues[-1].end + 2.0) if cues else 0.0,
        )

        ass_path = song.path("video", f"{song.slug}.ass")
        ass_path.parent.mkdir(parents=True, exist_ok=True)
        card_seconds = _card_seconds(song, args)
        ass_path.write_text(
            format_ass(
                cues, title=song.title,
                card_seconds=card_seconds, card_subtitle=args.subtitle,
            ),
            encoding="utf-8",
        )

        audio = None
        if args.with_audio:
            for candidate in (song.path("render", f"{song.slug}.wav"), song.source_path()):
                if candidate and Path(candidate).exists():
                    audio = candidate
                    break

        images = image_segments(_image_cues(song), timeline, duration)
        style = {"width": args.width, "height": args.height} if args.width else None
        _say(f"── {song.title}: {len(cues)} cues from {origin}, "
             f"{len(images)} image cues, {duration:.1f}s"
             + (f", title card for {card_seconds:.1f}s" if card_seconds else "")
             + (f", audio from {Path(audio).name}" if audio else ", silent"))
        target = song.path("video", f"{song.slug}.mp4")
        render_video(
            target, ass_path,
            duration=duration,
            images=images,
            image_dir=song.dir,
            audio=audio,
            style=style,
            crf=args.crf,
        )
        _stamp(song, ass_path, "video ass", [song.path("lyrics.srt")])
        _stamp(song, target, "video render", [ass_path])
        _say(f"→ {target}")
        _mark(song, "video")
    return 0


def _card_seconds(song: Song, args: argparse.Namespace) -> float:
    """How long the title card holds at the head of a song's own video.

    The count-in, because that is exactly the stretch of video with no lyric on
    it — cues start at bar 1 — and because the show project parks the transport
    at the region start between songs, so this is what is on the projector while
    the band talks. A song with no count-in gets no card in its video; it needs a
    standalone one from `rambass video card`.
    """
    if getattr(args, "no_card", False):
        return 0.0
    return song.timeline().count_in_seconds


def cmd_video_card(args: argparse.Namespace) -> int:
    """Render a standalone title card — the whole screen for a song with no video.

    Two songs in the set are a cappella and can never have a lyric video: there
    is no backing track to time cues against. The Tier C songs have no cues yet
    either. A still title card is what goes on the screen instead, and the show
    project holds it for the length of the region.
    """
    from .reaper import card_path, project_length_seconds
    from .video import card_ass, render_card

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        duration = args.duration or project_length_seconds(song)
        style = {"width": args.width, "height": args.height} if args.width else None

        ass_path = song.path("video", f"{song.slug}-card.ass")
        ass_path.parent.mkdir(parents=True, exist_ok=True)
        ass_path.write_text(
            card_ass(song.title, subtitle=args.subtitle, duration=duration, style=style),
            encoding="utf-8",
        )
        _say(f"{song.title}: {ass_path}")
        if args.ass_only:
            continue

        target = (
            song.path("video", f"{song.slug}-card.mp4") if args.mp4 else card_path(song)
        )
        render_card(
            target, ass_path,
            duration=duration, background=args.background, style=style, crf=args.crf,
        )
        _say(f"→ {target}")
    return 0


def cmd_video_probe(args: argparse.Namespace) -> int:
    """Report which software wrote a video file.

    QuickTime and MP4 files carry the encoder in their metadata, so the question
    "what made this video?" has a definite answer that does not need guessing at
    filenames. Apple tools write ``com.apple.quicktime.software`` and a handler
    of ``Core Media Video``; ffmpeg and anything built on it writes
    ``encoder: Lavf...`` with a ``VideoHandler`` handler; DaVinci Resolve, Adobe
    and Final Cut each stamp their own name.
    """
    from .audio import ffprobe_path, run

    result = run([
        ffprobe_path(), "-v", "error", "-hide_banner",
        "-show_entries",
        "format=format_name,duration,bit_rate:format_tags:stream=index,codec_name,"
        "codec_type,width,height,r_frame_rate:stream_tags",
        "-of", "default=noprint_wrappers=0",
        str(args.file),
    ])
    _say(result.stdout.strip() or "(ffprobe returned nothing)")
    interesting = [
        line for line in result.stdout.splitlines()
        if any(key in line.lower() for key in
               ("encoder", "software", "handler_name", "writing", "creation_time"))
    ]
    if interesting:
        _say("")
        _say("identifying tags:")
        for line in interesting:
            _say(f"   {line.strip()}")
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


def cmd_setlist_arc(args: argparse.Namespace) -> int:
    """Show a running order's shape and what it gets wrong."""
    from .arrange import review, table
    from .setlist import Setlist, find_setlist

    project = _project()
    setlist = Setlist.load(find_setlist(project, args.setlist))
    songs = setlist.resolve(project)

    _say(f"{setlist.name} — {len(songs)} songs"
         + ("   [already played — reviewed, not judged]" if setlist.historical else ""))
    _say()
    _say(table(songs))
    _say()

    findings = review(songs)
    problems = [f for f in findings if f.severity == "problem"]
    watch = [f for f in findings if f.severity == "watch"]

    if not findings:
        _say("no issues — this order holds up against the usual principles")
        return 0
    for finding in problems:
        _say(f"  !  {finding.where}")
        _say(f"     {finding.what}")
    for finding in watch:
        _say(f"  ?  {finding.where}")
        _say(f"     {finding.what}")
    _say()
    _say(f"{len(problems)} problem(s), {len(watch)} to look at")
    if setlist.historical:
        _say("(a played set — findings are hindsight, not a failing grade)")
        return 0
    return 1 if problems else 0


def cmd_set_status(args: argparse.Namespace) -> int:
    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        song.status[args.stage] = args.state
        save_song(song)
        _say(f"{song.album}/{song.slug}: {args.stage} = {args.state}")
    return 0


def cmd_scope(args: argparse.Namespace) -> int:
    """Cut a song from the show, or put it back."""
    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        if args.state == "out":
            song.excluded = True
            song.exclude_reason = args.reason
            song.status = song.default_status()
            _say(f"cut  {song.album}/{song.slug}"
                 + (f" — {args.reason}" if args.reason else ""))
        else:
            song.excluded = False
            song.exclude_reason = ""
            # Put the stages back to what this kind of song should start from,
            # keeping anything already finished.
            defaults = song.default_status()
            song.status = {
                stage: (song.status.get(stage, "todo")
                        if song.status.get(stage) in ("done", "wip")
                        and defaults[stage] != "n/a"
                        else defaults[stage])
                for stage in defaults
            }
            _say(f"back in the set  {song.album}/{song.slug}")
        save_song(song)
    return 0


def cmd_accompaniment(args: argparse.Namespace) -> int:
    """Set how a song's accompaniment arrives — including 'a-cappella'."""
    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        song.drums_origin = args.origin
        if args.reason:
            note = f"a cappella: {args.reason}" if args.origin == "a-cappella" else args.reason
            if note not in song.notes:
                song.notes = (song.notes + "\n" if song.notes else "") + note
        defaults = song.default_status()
        song.status = {
            stage: (song.status.get(stage, "todo")
                    if defaults[stage] != "n/a" and song.status.get(stage) in ("done", "wip")
                    else defaults[stage])
            for stage in defaults
        }
        song.validate()
        save_song(song)
        applicable = [s for s, v in song.status.items() if v != "n/a"]
        _say(f"{song.album}/{song.slug}: {args.origin} — "
             f"stages that still apply: {', '.join(applicable) or 'none'}")
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


def parse_position(text: str) -> tuple[int, float]:
    """``"22.3"`` -> ``(22, 3.0)``. Reaper's own notation for a position.

    Beats are 1-based, so a bare ``"20"`` means bar 20 beat 1. A third part is a
    fraction of a beat — ``"22.3.5"`` is the second eighth of beat 3 — which a
    12/8 shuffle needs and a whole beat cannot name.
    """
    parts = str(text).strip().split(".")
    if not 1 <= len(parts) <= 3 or not all(parts):
        raise ProjectError(
            f"{text!r} is not a position; write it as Reaper does, bar.beat "
            f"(for example 22.3), or just the bar"
        )
    try:
        bar = int(parts[0])
        beat = float(parts[1]) if len(parts) > 1 else 1.0
        if len(parts) == 3:
            beat += float(f"0.{parts[2]}")
    except ValueError:
        raise ProjectError(
            f"{text!r} is not a position; write it as Reaper does, bar.beat "
            f"(for example 22.3), or just the bar"
        ) from None
    if bar < 1 or beat < 1:
        raise ProjectError(f"{text!r}: bars and beats are 1-based")
    return bar, beat


def cmd_sections(args: argparse.Namespace) -> int:
    """List the sections and review them. Suggests; never edits."""
    from .midiio import read_drum_midi
    from .reaper import reaper_position
    from .sections import check_sections

    project = _project()
    for song in _songs(project, args.song, args.album, args.all):
        ordered = sorted(song.sections, key=lambda s: s.position)
        _say(f"── {song.title}: {len(ordered)} sections, {song.total_bars()} bars")
        if not ordered:
            _say("   none yet. `rambass section <song> <bar.beat> <name>` adds one.")
            continue

        timeline = song.timeline()
        spans = song.consolidation_spans()
        lengths = {(s.name, s.start_bar, s.start_beat): s for s in spans}
        _say(f"   {'reaper':>8}{'bars':>7}   section")
        for section in ordered:
            span = lengths.get((section.name, section.bar, section.beat))
            length = 0.0
            if span is not None:
                length = (timeline.bar_beat_to_seconds(span.end_bar, span.end_beat)
                          - timeline.bar_beat_to_seconds(span.start_bar, span.start_beat)
                          ) / timeline.bar_length_seconds(section.bar)
            _say(f"   {reaper_position(song, section):>8}{length:>7.2f}   {section.name}")

        performance = None
        path = song.drum_midi_path(args.midi)
        if path.exists():
            performance = read_drum_midi(path, load_drum_map(song.drum_map, project))
            performance.timeline = timeline
        else:
            _say(f"   (no {path.name} — the pattern checks need it)")

        findings = check_sections(song, performance)
        if not findings:
            _say("   nothing to suggest.")
            continue
        _say()
        for finding in findings:
            _say(f"   {finding}")
        _say()
        _say("   suggestions only — nothing was changed. Sectioning is a musical")
        _say("   judgement; this just says where the drums disagree with the list.")
    return 0


def cmd_section_add(args: argparse.Namespace) -> int:
    project = _project()
    song = load_song(project.find_song_dir(args.song))

    # Reaper's bar 1 is the first count-in bar, so its ruler runs count_in bars
    # ahead of the musical one — reading a section boundary off the screen and
    # typing it straight in puts every section two bars late. --reaper-bar does
    # the subtraction, because doing it in your head every time is the kind of
    # arithmetic that is right nine times and wrong once.
    bar, beat = parse_position(args.bar)
    quoted = f"{bar}.{beat:g}"
    if args.reaper_bar:
        bar -= song.count_in_bars
        if bar < 1:
            raise ProjectError(
                f"Reaper bar {quoted} is inside the {song.count_in_bars}-bar "
                f"count-in, so it is before the music starts"
            )

    # Matched on bar *and* beat: a 2.5-bar break puts two sections in one bar,
    # and replacing by bar alone would silently delete the one already there.
    song.sections = [s for s in song.sections if (s.bar, s.beat) != (bar, beat)]
    song.sections.append(Section(name=args.name, bar=bar, beat=beat))
    song.sections.sort(key=lambda s: s.position)
    song.validate()
    save_song(song)
    where = f" (Reaper {quoted})" if args.reaper_bar else ""
    timeline = song.timeline()
    _say(f"{song.title}: bar {bar} beat {beat:g}{where} at "
         f"{timeline.audio_time(bar, beat):.2f}s -> {args.name}")
    return 0


# ── parser ───────────────────────────────────────────────────────────────
def _add_song_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("song", nargs="*", help="song reference (slug, album/slug or path)")
    parser.add_argument("--album", help="operate on every song in this album")
    parser.add_argument("--all", action="store_true", help="operate on every song")


def _add_card_args(parser: argparse.ArgumentParser) -> None:
    """The title card that fills a lyric video's count-in."""
    parser.add_argument("--subtitle", default="",
                        help="second, smaller line under the title on the card")
    parser.add_argument("--no-card", action="store_true",
                        help="leave the count-in blank instead of showing the title")


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
    p.add_argument("--all-songs", action="store_true",
                   help="include songs cut from the set")
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

    p = sub.add_parser(
        "stale",
        help="which derived files are out of date, and why (input, song.yaml, "
             "or the code that made them)",
    )
    _add_song_args(p)
    p.add_argument("--quiet", action="store_true",
                   help="only show what is not current")
    p.set_defaults(func=cmd_stale)

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
                   choices=("backing-track", "recorded", "extracted", "programmed"))
    p.add_argument("--create-album", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("analyze", help="detect tempo, drift and the first downbeat")
    _add_song_args(p)
    p.add_argument("--file", help="analyse this audio file instead of source/")
    p.add_argument("--write", action="store_true", help="write the tempo into song.yaml")
    # Rounding to the nearest half-BPM used to be harmless, because the tempo was
    # only ever a starting point. It is not any more: transcription places every
    # hit against this number, and 0.25 BPM out is a second of slip across a
    # five-minute song. Keep the measured value; round it at the end if you want
    # a tidy click, because everything downstream is anchored to bars.
    p.add_argument("--round-to", type=float, default=0.01,
                   help="BPM rounding (default 0.01; 0 keeps the measured value)")
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
    p.add_argument("--no-parts", action="store_true",
                   help="ignore stems/parts/ and read the whole drum mix")
    p.add_argument("--no-follow", action="store_true",
                   help="do not correct for how far the take drifted from the "
                        "grid before placing hits (the output is metronomic "
                        "either way — see transcribe.dewander)")
    p.add_argument("--hat-delta", type=float, default=None,
                   help="hi-hat picker sensitivity; lower finds the subtle "
                        "strokes. 0.12 is the default, 0.07 is about as low as "
                        "pays on Tutti in Fila and below that the extra hits "
                        "are noise (see transcribe.bands_with_hat_delta)")
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
    p.add_argument("--subdivision", type=int, default=None,
                   help="grid per beat: 4 = 16ths, 3 = triplet 8ths (shuffle "
                        "feel). Default: drums.subdivision from song.yaml")
    p.add_argument("--cymbal-subdivision", type=int, default=None,
                   help="grid per beat for the crash family, which is never "
                        "snapped as tight as the rest of the kit. Default: "
                        "drums.cymbal_subdivision from song.yaml")
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
    p.add_argument("--no-voicing", action="store_true",
                   help="ignore the sections' declared backbeat articulation")
    p.add_argument("--backbeat-velocity", type=int, default=None,
                   help="velocity for a declared backbeat articulation. Once "
                        "you have settled it by ear, put it in song.yaml as "
                        "drums.backbeat_velocity so a re-run keeps it; without "
                        "either, the default is the median of what the detector "
                        "already named, which is the only measurement on the "
                        "right instrument (see restore.voice_backbeats)")
    p.set_defaults(func=cmd_drums_clean)

    p = drums_sub.add_parser(
        "consolidate",
        help="Stage 6: replace each section with the pattern its bars agree on",
    )
    _add_song_args(p)
    p.add_argument("--input", default="quantized",
                   help="input variant (default: quantized — vote on the grid)")
    p.add_argument("--output", default="consolidated", help="output variant")
    p.add_argument("--threshold", type=float, default=0.55,
                   help="keep a hit where this share of the repetitions played "
                        "it (default 0.55; docs/drums-rebuild.md argues 0.5-0.6)")
    p.add_argument("--subdivision", type=int, default=None,
                   help="slot grid to vote on; default drums.subdivision")
    p.add_argument("--unit-bars", type=int, default=0,
                   help="repeat length in bars; 0 works out 1 or 2 per section")
    p.add_argument("--dry-run", action="store_true",
                   help="report what it would do and write nothing")
    p.set_defaults(func=cmd_drums_consolidate)

    p = sub.add_parser(
        "align",
        help="map bars to seconds in the original recording, and warp a "
             "reference onto the grid (practice only)",
    )
    _add_song_args(p)
    p.add_argument("--fit", action="store_true",
                   help="re-fit the anchors from detected beats")
    p.add_argument("--warp", action="store_true",
                   help="render practice/<stem>-aligned.wav on the fixed grid")
    p.add_argument("--stem", default="no_drums",
                   help="which stem to warp (default: no_drums, the "
                        "band-minus-drums bed)")
    p.add_argument("--every-beats", type=int, default=1,
                   help="anchor every N beats when fitting. 1 is per beat and "
                        "is the default because it measurably wins: on Manlio "
                        "the warped backbeats land within 38 ms at p90 against "
                        "58 ms per bar and 107 ms for a single offset")
    p.add_argument("--keep-bar-one", action="store_true", default=True,
                   help="keep an existing bar-1 anchor, which may have been "
                        "corrected by ear")
    p.add_argument("--refit-bar-one", dest="keep_bar_one", action="store_false",
                   help="let the fit replace bar 1 as well")
    p.add_argument("--force", action="store_true",
                   help="write a fitted map even if it cannot be warped")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_align)

    p = drums_sub.add_parser(
        "missing",
        help="Stage 7: list every hit the pipeline removed or never named",
    )
    _add_song_args(p)
    p.add_argument("--before", default="quantized",
                   help="the fuller variant (default: quantized)")
    p.add_argument("--after", default="consolidated",
                   help="the variant to check against it (default: consolidated)")
    p.add_argument("--no-crashes", action="store_true",
                   help="skip the section-boundary crash candidates")
    p.add_argument("--everything", action="store_true",
                   help="include the groove too (hats, kick, snare, side-stick), "
                        "which the vote is supposed to regularise")
    p.add_argument("--propose", action="store_true",
                   help="write the confident crash candidates into "
                        "drums.additions for you to review in the diff")
    p.set_defaults(func=cmd_drums_missing)

    p = drums_sub.add_parser(
        "restore",
        help="Stage 7: reapply drums.additions and drums.removals from song.yaml",
    )
    _add_song_args(p)
    p.add_argument("--input", default="consolidated",
                   help="input variant (default: consolidated)")
    p.add_argument("--output", default="restored", help="output variant")
    p.add_argument("--dry-run", action="store_true",
                   help="report what it would do and write nothing")
    p.set_defaults(func=cmd_drums_restore)

    p = drums_sub.add_parser("remap", help="move a drum MIDI onto another kit's mapping")
    p.add_argument("song")
    p.add_argument("to_map", metavar="to-map")
    p.add_argument("--from-map", dest="from_map")
    p.add_argument("--input", default="quantized")
    p.add_argument("--file")
    p.add_argument("--set-default", action="store_true")
    p.set_defaults(func=cmd_drums_remap)

    review = sub.add_parser(
        "review", help="the project console: rebuilds, notes, A/B review")
    review_sub = review.add_subparsers(dest="review_command")

    p = review_sub.add_parser(
        "rebuild",
        help="run every stale/missing pipeline step for a song, in order",
    )
    p.add_argument("song", nargs="*", help="song reference (slug, album/slug or path)")
    p.add_argument("--album", help="operate on every song in this album")
    p.add_argument("--step", help="run only the step with this name")
    p.add_argument("--force", metavar="ARTIFACT",
                   help="rebuild one edited/unknown artifact anyway "
                        "(a .bak of the file is kept)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the commands and run nothing")
    p.set_defaults(func=cmd_review_rebuild)

    p = sub.add_parser(
        "click",
        help="render the rehearsal/overdub click (song only, its own stem)",
    )
    _add_song_args(p)
    p.add_argument("--bars", type=int, default=0, help="override the song length")
    p.add_argument("--out", help="explicit output path")
    p.add_argument("--sample-rate", type=int, default=48000)
    p.add_argument("--level", type=float, default=-9.0, help="click level in dBFS")
    p.set_defaults(func=cmd_click)

    p = sub.add_parser(
        "countin",
        help="render the drumstick count-in as its own stem",
    )
    _add_song_args(p)
    p.add_argument("--out", help="explicit output path")
    p.add_argument("--bars", type=int, default=0, help="override count_in.bars")
    p.add_argument("--level", type=float, default=-8.0, help="stick level in dBFS")
    p.add_argument("--sample",
                   help="use a real recorded stick hit instead of the synth — "
                        "a phone recording of the drummer's own sticks beats any "
                        "synthesised one")
    p.add_argument("--sample-rate", type=int, default=48000)
    p.set_defaults(func=cmd_countin)

    reaper = sub.add_parser("reaper", help="generate Reaper build scripts")
    reaper_sub = reaper.add_subparsers(dest="reaper_command", metavar="<subcommand>")

    p = reaper_sub.add_parser("build", help="one project per song")
    _add_song_args(p)
    p.add_argument("--midi", default=None,
                   help="drum MIDI variant to place; the default is the most "
                        "finished one that exists (restored, else consolidated, "
                        "else quantized)")
    p.add_argument("--no-reference", action="store_true",
                   help="leave out the reference stems and mix")
    p.set_defaults(func=cmd_reaper_build)

    p = reaper_sub.add_parser("import", help="read tempo/markers/patches from an existing .RPP")
    p.add_argument("song")
    p.add_argument("file")
    p.add_argument("--offset", type=float, default=None,
                   help="project time of musical bar 1 (default: first audio item)")
    p.add_argument("--write", action="store_true", help="apply it to song.yaml")
    p.add_argument("--keep-patches", action="store_true",
                   help="do not overwrite existing gx100 changes")
    p.set_defaults(func=cmd_reaper_import)

    p = reaper_sub.add_parser("setlist", help="one project for the whole show")
    p.add_argument("setlist")
    p.add_argument("--gap", type=float, default=4.0, help="seconds between songs")
    p.add_argument("--no-video", action="store_true",
                   help="leave the video track out (e.g. video runs from another machine)")
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
    _add_card_args(p)
    p.set_defaults(func=cmd_video_ass)

    p = video_sub.add_parser("render", help="render the MP4")
    _add_song_args(p)
    p.add_argument("--with-audio", action="store_true", help="mux the backing track in")
    p.add_argument("--crf", type=int, default=20)
    p.add_argument("--duration", type=float, default=0.0,
                   help="override the length in seconds")
    p.add_argument("--width", type=int, default=0, help="output width (with --height)")
    p.add_argument("--height", type=int, default=0)
    _add_card_args(p)
    p.set_defaults(func=cmd_video_render)

    p = video_sub.add_parser(
        "card", help="render a still title card for a song with no lyric video")
    _add_song_args(p)
    p.add_argument("--subtitle", default="",
                   help="second, smaller line under the title")
    p.add_argument("--background", help="image behind the text")
    p.add_argument("--duration", type=float, default=0.0,
                   help="seconds (only matters with --mp4)")
    p.add_argument("--mp4", action="store_true",
                   help="a still video instead of a PNG, for a screen that "
                        "will not take an image")
    p.add_argument("--ass-only", action="store_true",
                   help="write the subtitle file and stop — no ffmpeg needed")
    p.add_argument("--crf", type=int, default=20)
    p.add_argument("--width", type=int, default=0, help="output width (with --height)")
    p.add_argument("--height", type=int, default=0)
    p.set_defaults(func=cmd_video_card)

    p = video_sub.add_parser("probe", help="report which software wrote a video file")
    p.add_argument("file")
    p.set_defaults(func=cmd_video_probe)

    lyrics = sub.add_parser("lyrics", help="timed lyric cues: import, transcribe, export")
    lyrics_sub = lyrics.add_subparsers(dest="lyrics_command", metavar="<subcommand>")

    p = lyrics_sub.add_parser("import", help="bring an existing .srt/.vtt/.lrc into a song")
    p.add_argument("song")
    p.add_argument("file")
    p.add_argument("--shift", type=float, default=0.0,
                   help="move every cue by this many seconds on the way in")
    p.add_argument("--as-srt", action="store_true",
                   help="store as lyrics.srt even if the input was another format")
    p.set_defaults(func=cmd_lyrics_import)

    p = lyrics_sub.add_parser("transcribe", help="draft cues from audio with Whisper")
    _add_song_args(p)
    p.add_argument("--file", help="transcribe this file instead of stems/vocals.*")
    p.add_argument("--model", default="medium",
                   help="whisper model: tiny/base/small/medium/large-v3 (default medium)")
    p.add_argument("--language", default="it")
    p.add_argument("--device", default="auto", help="auto, cpu, cuda")
    p.add_argument("--max-chars", type=int, default=42, help="characters per rendered line")
    p.add_argument("--prompt", default="",
                   help="bias the decoder — paste the real words if you have them")
    p.add_argument("--prompt-file", help="read the bias text from a file")
    p.add_argument("--output", default="lyrics.draft.srt",
                   help="where to write the draft (default lyrics.draft.srt, so it "
                        "cannot clobber a hand-timed lyrics.srt)")
    p.set_defaults(func=cmd_lyrics_transcribe)

    p = lyrics_sub.add_parser("export", help="write cues out as srt/vtt/lrc/ass/txt")
    _add_song_args(p)
    p.add_argument("--format", nargs="+", default=["srt"],
                   help="one or more of: srt vtt lrc ass txt")
    p.add_argument("--out", help="explicit output path (single song, single format)")
    p.add_argument("--offset", type=float, default=0.0, help="shift every cue")
    p.add_argument("--count-in", action="store_true",
                   help="shift by the song's count-in, for cues timed to the album master")
    p.set_defaults(func=cmd_lyrics_export)

    p = lyrics_sub.add_parser("check", help="validate cue timings and line lengths")
    _add_song_args(p)
    p.add_argument("--quiet", action="store_true", help="skip songs with no lyrics yet")
    p.set_defaults(func=cmd_lyrics_check)

    p = lyrics_sub.add_parser("shift", help="move or rescale a song's cues in place")
    p.add_argument("song")
    p.add_argument("seconds", type=float, nargs="?", default=0.0)
    p.add_argument("--from-bpm", type=float, help="rescale from this tempo")
    p.add_argument("--to-bpm", type=float, help="rescale to this tempo")
    p.add_argument("--out", help="write here instead of over the source file")
    p.set_defaults(func=cmd_lyrics_shift)

    p = lyrics_sub.add_parser("bars", help="convert cues to the bar-anchored md format")
    p.add_argument("song")
    p.add_argument("--out")
    p.set_defaults(func=cmd_lyrics_bars)

    p = sub.add_parser("setlist", help="show a setlist running order")
    p.add_argument("setlist")
    p.add_argument("--gap", type=float, default=30.0, help="seconds of talking between songs")
    p.add_argument("--out")
    p.set_defaults(func=cmd_setlist_show)

    p = sub.add_parser(
        "arc",
        help="show a setlist's energy shape and check the order against how set "
             "lists are actually built",
    )
    p.add_argument("setlist")
    p.set_defaults(func=cmd_setlist_arc)

    p = sub.add_parser("mark", help="set a pipeline stage's status")
    _add_song_args(p)
    p.add_argument("stage", choices=STAGES)
    p.add_argument("state", choices=STATUS_VALUES)
    p.set_defaults(func=cmd_set_status)

    p = sub.add_parser("scope", help="cut a song from the show, or put it back")
    _add_song_args(p)
    p.add_argument("state", choices=("in", "out"))
    p.add_argument("--reason", default="", help="why it was cut")
    p.set_defaults(func=cmd_scope)

    p = sub.add_parser(
        "accompaniment",
        help="set where a song's accompaniment comes from (a-cappella, "
             "backing-track, recorded, extracted, programmed)",
    )
    _add_song_args(p)
    p.add_argument("origin", choices=DRUM_ORIGINS)
    p.add_argument("--reason", default="", help="note to record on the song")
    p.set_defaults(func=cmd_accompaniment)

    p = sub.add_parser("patch", help="add or replace a GX-100 patch change")
    p.add_argument("song")
    p.add_argument("bar", type=int)
    p.add_argument("memory")
    p.add_argument("--name", default="")
    p.set_defaults(func=cmd_patch_add)

    p = sub.add_parser("section", help="add or replace a section marker")
    p.add_argument("song")
    p.add_argument("bar", metavar="POSITION",
                   help="bar, or bar.beat as Reaper writes it (e.g. 22.3)")
    p.add_argument("name")
    p.add_argument("--reaper-bar", action="store_true",
                   help="the bar number as Reaper shows it, which counts the "
                        "count-in bars; this subtracts them")
    p.set_defaults(func=cmd_section_add)

    p = sub.add_parser("sections", help="list a song's sections and review them")
    _add_song_args(p)
    p.add_argument("--midi", default="quantized",
                   help="drum variant to review the sections against; the "
                        "pattern checks are skipped if it is missing")
    p.set_defaults(func=cmd_sections)

    return parser


def main(argv: list[str] | None = None) -> int:
    # First, before anything can try to print a box-drawing character.
    use_utf8()
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
