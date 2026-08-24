"""The project console's data layer: rebuilds, step tables, notes, clips.

Paolo reviews a rebuilt drum part by ear, against the original, section by
section — and everything he needs for that lives in modules that already
exist: :mod:`~rambass.provenance` knows what is out of date and which command
fixes it, :mod:`~rambass.timeline` knows where a bar is, :mod:`~rambass.align`
knows where the same bar is in the breathing original recording, and
:mod:`~rambass.restore` knows how a machine-proposed edit becomes a reviewed
one. This module is the thin layer that turns those into the console's
building blocks; :mod:`~rambass.console` serves them over HTTP.

Everything here is pure or nearly so — plain data in, plain data out — for the
same reason ``quantize.py`` is: it is what makes the console testable without
a browser, ffmpeg or an audio file. See docs/review-ui.md for the scope.
"""

from __future__ import annotations

from dataclasses import dataclass

from .project import ProjectError

# ── rebuilding: what a one-key rebuild may touch ─────────────────────────────
#
# `rambass stale` never rebuilds on its own, and the console does not change
# that judgement — it only packages it. The split below is provenance.py's own
# rule kept intact: `stale` and `missing` are safe to rebuild because nothing
# of a human's is lost; `edited` is a hand edit whose risk runs the OPPOSITE
# way (re-running the step throws the edit away); `unknown` carries no
# provenance at all, so nothing can say whether it is safe. The held set is
# reported, never touched — forcing one through is a separate, per-artifact,
# explicitly confirmed act that writes a .bak first.


def rebuild_selection(entries) -> tuple[list, list]:
    """Split a :func:`~rambass.provenance.stale_report` into (auto, held).

    *auto* is what a rebuild may run, in the report's own pipeline order.
    *held* is what it must only report: hand-edited or unstamped artifacts.
    """
    auto = [e for e in entries if e.state in ("stale", "missing")]
    held = [e for e in entries if e.state in ("edited", "unknown")]
    return auto, held


def run_rebuild(entries, *, runner) -> dict:
    """Run each entry's command via *runner*, stopping at the first failure.

    *runner* takes the command string and returns ``(exit_code, output)``. The
    CLI and the console inject a subprocess runner (the real ``rambass`` CLI, so
    this can never produce a result a human typing the same command would not);
    tests inject a recorder. Trusting the input is deliberate — the selection
    above is the only gate, and callers pass its *auto* side.

    ``outputs`` maps each command to what it printed. The runner used to return
    a bare exit code, which sent every reason a command had to the terminal
    ``rambass console`` was started from — so the browser could say "FAIL" and
    nothing else, and a refusal nobody could read is a refusal nobody can act
    on.
    """
    report: dict = {"ran": 0, "failed": "", "log": [], "outputs": {}}
    for entry in entries:
        report["log"].append(entry.command)
        code, output = runner(entry.command)
        report["outputs"][entry.command] = output
        if code != 0:
            report["failed"] = entry.command
            break
        report["ran"] += 1
    return report


def subprocess_runner(cwd):
    """A runner that executes ``rambass ...`` command lines for real.

    ``sys.executable -m rambass`` rather than a bare ``rambass`` from PATH, so
    the rebuild uses exactly the interpreter and checkout the console runs
    from — a venv mismatch here would rebuild with different code than the
    staleness report reasoned about.

    Output is **teed**, not captured: every line is printed as it arrives *and*
    accumulated for the caller. A plain ``capture_output=True`` would have
    silenced the terminal and held a three-minute separation's progress until it
    finished, so the fix for an unreadable browser would have broken the one
    place that already worked.
    """
    import shlex
    import subprocess
    import sys

    def run(command: str) -> tuple[int, str]:
        words = shlex.split(command)
        if words and words[0] == "rambass":
            words = [sys.executable, "-m", "rambass"] + words[1:]
        lines: list[str] = []
        # stderr onto stdout: a ProjectError is printed to stderr and is exactly
        # the sentence the reader needs, so the two must not be interleaved by
        # separate pipes or read in an order that can deadlock.
        process = subprocess.Popen(
            words, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        assert process.stdout is not None
        with process.stdout as stream:
            for line in stream:
                print(line, end="")
                lines.append(line)
        return process.wait(), "".join(lines)

    return run


# ── the step tables: what each stage screen lists ────────────────────────────
#
# The content is docs/workflow.md's per-song sequence held as data, so the
# console reads the same list the docs describe and the two cannot drift the
# way a hand-copied checklist would. The per-album fork is Paolo's own table
# in workflow.md: Diversamente Giovani collapses to "import the project, fix
# the count-in, do the lyrics", an a-cappella song needs a title card and
# nothing else (CLAUDE.md: that is a finished state, not a to-do), and only
# an extracted song runs the drum pipeline.


@dataclass(frozen=True)
class StepRow:
    """One line of a stage screen.

    *kind* is what the button does: ``run`` is one deterministic command with
    nothing to inspect but its report; ``open`` needs a screen of its own
    (ears, not a report); ``manual`` is a human act the console can only
    describe. *artifact* names the provenance artifact the step produces, so
    the screen can show done-but-stale, or ``""`` when there is none.

    *state* is for the rows :mod:`~rambass.provenance` cannot track, which it
    would otherwise report as ``""`` -- and the console draws ``""`` as the
    same grey dot it uses for a stage that does not apply. So a mix that is
    sitting in ``source/`` looked identical to a stage that will never happen,
    and the analyze row showed a todo dot beside a Run button however settled
    its tempo was, which is what invited the click that overwrote Manlio's
    60.0. A row that can establish its own state says so here, in the
    vocabulary :func:`~rambass.provenance.stale_report` uses (``ok`` /
    ``missing``); ``""`` still means genuinely untracked.
    """

    label: str
    command: str = ""
    kind: str = "run"
    artifact: str = ""
    note: str = ""
    state: str = ""


#: The dashboard's drum columns collapse onto one screen: on Tutti in Fila
#: these are not three sit-down sessions but one pass that loops on itself —
#: clean, listen, consolidate, listen, restore, listen again.
DRUM_CLUSTER = ("drums_midi", "quantize", "kit")


def steps_for(song, stage: str) -> list[StepRow]:
    """The checklist for one song's one stage, resolved per album and origin.

    *stage* is a :data:`~rambass.manifest.STAGES` name, or ``"drums"`` for the
    :data:`DRUM_CLUSTER`. An empty list means the stage does not apply — the
    grey ``n/a`` cell, not an empty to-do list.
    """
    origin = song.drums_origin
    slug = song.slug
    if stage in DRUM_CLUSTER:
        stage = "drums"

    if origin == "a-cappella":
        # `reaper.required_artifacts` returns ("screen",) for these: the card
        # is the whole job. Do not add backing-track steps back.
        if stage == "video":
            return [StepRow("Title card", f"rambass video card {slug}",
                            note="the a-cappella songs need a card and nothing else")]
        if stage == "source":
            # No state: optional means optional, and a todo dot here would nag
            # about something CLAUDE.md calls a finished state already.
            return [StepRow("Reference recording in source/", kind="manual",
                            note="optional — for rehearsal only")]
        if stage == "rehearsed":
            return [StepRow("Play it through with the band", kind="manual")]
        return []

    if stage == "source":
        # The file being there *is* the completion test for this step, so it is
        # read off disk rather than from `song.status` -- `rambass mark` is a
        # human's tick, and this one thing the console can check itself.
        return [StepRow("Original mix in source/", kind="manual",
                        state="ok" if song.source_path() else "missing",
                        note="the WAV, not the MP3 — separation quality sets "
                             "the ceiling (drums-rebuild.md Stage 0)")]
    if stage == "analyze":
        # There is no artifact to hash -- the tempo lands in `song.yaml` -- so
        # the manifest's own status is the only record that it is settled.
        return [StepRow("Find the tempo", f"rambass analyze {slug} --write",
                        state=("ok" if song.status.get("analyze") == "done"
                               else "missing"),
                        note="refined against the recording; a bin centre is "
                             "not a measurement")]
    if stage == "stems":
        if origin != "extracted":
            return []
        return [StepRow("Separate drum stem", f"rambass stems {slug} --drums-only",
                        artifact="stems/drums.wav")]
    if stage == "drums":
        if origin != "extracted":
            return []
        return [
            StepRow("Transcribe to MIDI", f"rambass drums transcribe {slug}",
                    artifact="midi/drums-raw.mid"),
            StepRow("Clean: de-flam, quantise, velocities",
                    f"rambass drums clean {slug}",
                    artifact="midi/drums-quantized.mid"),
            StepRow("Sections & consolidate",
                    f"rambass drums consolidate {slug}",
                    artifact="midi/drums-consolidated.mid",
                    note="mark the sections first: rambass section <bar> <name>"),
            StepRow("Missing hits & restore", f"rambass drums restore {slug}",
                    kind="open", artifact="midi/drums-restored.mid",
                    note="needs ears, not a report — opens the review tool"),
            StepRow("Choose kit & drum map", kind="manual",
                    note="audition against the song's character block, then "
                         f"rambass drums remap {slug} <map> --set-default"),
        ]
    if stage == "render":
        if origin == "backing-track":
            return [
                StepRow("Import the band's base", kind="manual",
                        note="the mixed BASE from the album sessions — see "
                             "existing-work.yaml for where it lives"),
                StepRow("Fix the count-in", f"rambass countin {slug}",
                        artifact="render/sticks.wav"),
            ]
        return [
            StepRow("Render the click", f"rambass click {slug}",
                    artifact="render/click.wav"),
            StepRow("Render the count-in sticks", f"rambass countin {slug}",
                    artifact="render/sticks.wav"),
            StepRow("Bounce the base", kind="manual",
                    note="DRUMS MIDI alone, from the BAR 1 marker, never with "
                         "the click (drums-rebuild.md Stage 9)"),
        ]
    if stage == "lyrics":
        return [
            StepRow("Timed cues in lyrics.srt", kind="manual",
                    note=f"import a hand-timed file (rambass lyrics import "
                         f"{slug} <file>) or draft one below"),
            StepRow("Draft cues with Whisper",
                    f"rambass lyrics transcribe {slug}",
                    note="a draft to correct, not a result"),
            StepRow("Check the cues", f"rambass lyrics check {slug}"),
        ]
    if stage == "video":
        return [
            StepRow("Subtitles", f"rambass video ass {slug}",
                    artifact="video/{slug}.ass",
                    note="proof-read this before rendering"),
            StepRow("Render the MP4", f"rambass video render {slug}",
                    artifact="video/{slug}.mp4"),
        ]
    if stage == "gx100":
        return [StepRow("Patch-change MIDI", f"rambass gx100 midi {slug}",
                        artifact="midi/gx100.mid")]
    if stage == "rehearsed":
        return [StepRow("Play it through on the gig rig", kind="manual")]
    return []


# ── clip spans: where a section is, on both clocks ───────────────────────────
#
# The A/B tool plays the same section from two files that run on different
# clocks. The candidate is the Stage 9 bounce: rendered from the BAR 1 marker,
# so its first sample IS musical zero and a position in it is
# `Timeline.bar_beat_to_seconds` — using `audio_time` here would shift every
# clip by the count-in, CLAUDE.md's two-clocks mistake through a new door. The
# reference is the original recording, which breathes; where a bar sits in it
# is `practice/align.yaml`'s job (`AlignMap.source_at`), the same map the
# practice warp uses, and nothing here re-derives it.


@dataclass(frozen=True)
class ClipSpan:
    """One section, addressed on both clocks. Bars are musical, as always."""

    name: str
    start_bar: int
    start_beat: float
    end_bar: int
    end_beat: float
    candidate_start: float      #: seconds into the bar-1-anchored candidate wav
    duration: float             #: seconds of the span on the fixed grid
    reference_start: float      #: seconds into the original recording
    reference_duration: float
    approximate: bool           #: True when the map is a bare offset


def clip_spans(song, align_map) -> list[ClipSpan]:
    """A :class:`ClipSpan` per section (one for the whole part when none).

    *align_map* is anything with ``mode`` and ``source_at`` — normally
    :class:`~rambass.align.AlignMap` from :func:`~rambass.align.load_align`,
    duck-typed so this module stays out of the numpy tier.
    """
    mode = getattr(align_map, "mode", "none")
    if mode == "none":
        raise ProjectError(
            f"{song.slug}: no usable practice/align.yaml, so the reference "
            f"clips cannot be placed in the original recording.\n"
            f"  fit one with:  rambass align {song.slug} --fit")

    timeline = song.timeline()
    end_of_part = ((song.bars or song.total_bars()) + 1, 1.0)
    ordered = sorted(song.sections, key=lambda s: (s.bar, s.beat))
    if not ordered:
        boundaries = [("song", 1, 1.0, *end_of_part)]
    else:
        boundaries = []
        for index, section in enumerate(ordered):
            if index + 1 < len(ordered):
                stop = (ordered[index + 1].bar, ordered[index + 1].beat)
            else:
                stop = end_of_part
            boundaries.append((section.name, section.bar, section.beat, *stop))

    out: list[ClipSpan] = []
    for name, bar, beat, end_bar, end_beat in boundaries:
        start = timeline.bar_beat_to_seconds(bar, beat)
        stop = timeline.bar_beat_to_seconds(end_bar, end_beat)
        source_start = align_map.source_at(start, timeline)
        source_stop = align_map.source_at(stop, timeline)
        out.append(ClipSpan(
            name=name, start_bar=bar, start_beat=beat,
            end_bar=end_bar, end_beat=end_beat,
            candidate_start=start, duration=stop - start,
            reference_start=source_start,
            reference_duration=source_stop - source_start,
            approximate=(mode == "offset"),
        ))
    return out


# ── the notes ledger: qa/review.yaml ─────────────────────────────────────────
#
# A review note is a raw "something might be wrong here" — most turn out to be
# nothing, some become an addition or a removal, a few just get written down.
# It is a sidecar and not a `song.yaml` field on purpose: `drums.additions`
# holds vetted edits that `drums restore` applies blindly, and unreviewed
# chatter does not belong next to those. Musical bars, never Reaper's, for the
# reason CLAUDE.md gives everywhere: a stored ruler number slides the day
# `count_in.bars` changes.

NOTE_KINDS = ("missing-hit", "extra-hit", "wrong-instrument", "timing",
              "velocity", "other")
NOTE_STATUSES = ("open", "promoted", "dismissed")


@dataclass
class Note:
    """One thing heard during a review pass, anchored to a musical bar."""

    bar: int
    beat: float = 1.0
    phase: str = "drums"
    section: str = ""
    kind: str = "other"
    instrument: str = ""
    velocity: int = 0
    comment: str = ""
    status: str = "open"
    created: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> Note:
        return cls(
            bar=int(data["bar"]),
            beat=float(data.get("beat", 1.0)),
            phase=str(data.get("phase", "drums")),
            section=str(data.get("section", "")),
            kind=str(data.get("kind", "other")),
            instrument=str(data.get("instrument", "")),
            velocity=int(data.get("velocity", 0) or 0),
            comment=str(data.get("comment", "")),
            status=str(data.get("status", "open")),
            created=str(data.get("created", "")),
        )

    def to_dict(self) -> dict:
        out: dict = {"bar": self.bar}
        if self.beat != 1.0:
            out["beat"] = round(self.beat, 3)
        if self.phase != "drums":
            out["phase"] = self.phase
        if self.section:
            out["section"] = self.section
        out["kind"] = self.kind
        if self.instrument:
            out["instrument"] = self.instrument
        if self.velocity:
            out["velocity"] = self.velocity
        if self.comment:
            out["comment"] = self.comment
        out["status"] = self.status
        if self.created:
            out["created"] = self.created
        return out


def load_review(path) -> tuple[str, list[Note]]:
    """``(version, notes)`` from ``qa/review.yaml``. Missing file: empty ledger."""
    from pathlib import Path

    import yaml

    path = Path(path)
    if not path.is_file():
        return "", []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return (str(data.get("version", "")),
            [Note.from_dict(item) for item in data.get("notes") or []])


def save_review(path, notes, *, version: str) -> None:
    from pathlib import Path

    import yaml

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# What a review pass heard, bar by bar. Musical bars (no count-in).\n"
        "# Promote a note into drums.additions / drums.removals with\n"
        "# `rambass review promote`; qa/review.md is this file for reading\n"
        "# next to the Reaper ruler.\n"
    )
    payload = {
        "version": version,
        "notes": [note.to_dict() for note in
                  sorted(notes, key=lambda n: (n.bar, n.beat))],
    }
    path.write_text(
        header + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8")


def review_markdown(title: str, notes, *, count_in_bars: int) -> str:
    """The ledger for reading next to the ruler — Reaper numbers, made once here.

    Same split as :func:`rambass.restore.checklist`: the stored data is
    musical, the document a human reads beside the ruler adds the count-in at
    the edge and nowhere else.
    """
    lines = [f"# {title} — review notes", ""]
    if not notes:
        lines += ["No notes. Either the part is right, or nobody has listened "
                  "yet — the ledger cannot tell those apart.", ""]
        return "\n".join(lines)
    lines += [f"Bar numbers are **Reaper's** ruler, with the {count_in_bars}-bar "
              f"count-in included.", ""]
    section = None
    marks = {"open": " ", "promoted": "x", "dismissed": "-"}
    for note in sorted(notes, key=lambda n: (n.bar, n.beat)):
        if note.section != section:
            section = note.section
            lines += ["", f"## {section or '(outside every section)'}", ""]
        what = note.instrument or note.kind
        lines.append(
            f"- [{marks.get(note.status, ' ')}] "
            f"`{note.bar + count_in_bars}.{note.beat:g}` **{what}** "
            f"({note.kind}) — {note.comment or 'no comment'}")
    lines.append("")
    return "\n".join(lines)


#: What `promote` will turn into a real edit on its own. The same division as
#: :data:`rambass.restore.PROPOSABLE`: a named hit at a named position is one
#: decision already made; a timing or velocity complaint is a judgement still
#: to make, and stays a human's.
PROMOTABLE = ("missing-hit", "extra-hit")


def promote_notes(song, notes) -> tuple[int, int]:
    """Turn confident open notes into ``drums.additions`` / ``drums.removals``.

    Machine proposes, ``git diff`` reviews, ``drums restore`` applies —
    :func:`rambass.restore.propose_additions`'s split, applied to the ledger.
    Mutates *song* and the notes' ``status``; the caller saves both. Returns
    ``(promoted, skipped)``.
    """
    from .drummap import CANONICAL

    additions = {(a.bar, round(a.beat, 3), a.instrument)
                 for a in song.drum_additions}
    removals = {(r.bar, round(r.beat, 3), r.instrument)
                for r in song.drum_removals}
    promoted = skipped = 0
    for note in notes:
        key = (note.bar, round(note.beat, 3), note.instrument)
        if (note.status != "open" or note.kind not in PROMOTABLE
                or note.instrument not in CANONICAL):
            skipped += 1
            continue
        if note.kind == "missing-hit":
            if key in additions:
                skipped += 1
                continue
            from .manifest import Addition

            song.drum_additions = list(song.drum_additions) + [Addition(
                bar=note.bar, beat=note.beat, instrument=note.instrument,
                velocity=note.velocity,
                note=note.comment or "promoted from qa/review.yaml")]
            additions.add(key)
        else:
            if key in removals:
                skipped += 1
                continue
            from .manifest import Removal

            song.drum_removals = list(song.drum_removals) + [Removal(
                bar=note.bar, beat=note.beat, instrument=note.instrument,
                note=note.comment or "promoted from qa/review.yaml")]
            removals.add(key)
        note.status = "promoted"
        promoted += 1
    return promoted, skipped


def clip_name(span: ClipSpan, side: str) -> str:
    """A filename for one cut clip: position-sorted, readable, ascii-safe."""
    from .project import slugify

    return (f"{span.start_bar:03d}-{int(span.start_beat)}-"
            f"{slugify(span.name) or 'span'}-{side}.wav")


def cut_clip(source, target, *, start: float, duration: float) -> None:
    """One ffmpeg invocation: *duration* seconds of *source* from *start*.

    Decode-and-trim only — no resampling tricks, no fades. The clip exists to
    be listened to against its twin, and any processing here would be one more
    thing the ear has to discount.
    """
    from pathlib import Path

    from .audio import ffmpeg_path, run

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    run([ffmpeg_path(), "-v", "error", "-nostdin", "-y",
         "-ss", f"{max(start, 0.0):.3f}", "-t", f"{max(duration, 0.05):.3f}",
         "-i", str(source), str(target)])


def candidate_path(song, override=None):
    """Where the rendered-MIDI wav to review lives.

    ``qa/candidate.wav`` first — a review-pass bounce, deliberately not
    ``render/``, which docs/practice-tracks.md reserves for the gig deliverable
    — then the real base if one exists. Raises with instructions otherwise.
    """
    from pathlib import Path

    if override:
        path = Path(override)
        if not path.is_file():
            raise ProjectError(f"--candidate {override}: no such file")
        return path
    for candidate in (song.path("qa", "candidate.wav"),
                      song.path("render", f"{song.slug}.wav")):
        if candidate.is_file():
            return candidate
    raise ProjectError(
        f"{song.slug}: nothing to review — bounce the drum MIDI through the "
        f"kit (EZdrummer, from the BAR 1 marker, no click) to "
        f"qa/candidate.wav, or pass --candidate <wav>")


@dataclass(frozen=True)
class ClipSource:
    """One side's audio source, and what to do when it is not there yet.

    The two sides fail for unrelated reasons: the reference stem is one
    command away, and the candidate is a bounce through the kit in a DAW that
    nothing here can produce. Reporting them together -- one message naming
    both, one error that kills both requests -- is how the reference canvas
    went blank for a `stems/drums.wav` that was already on disk. So each side
    answers for itself, and only the side a command can make carries one.
    """

    side: str
    label: str
    path: object = None
    hint: str = ""
    command: str = ""

    @property
    def available(self) -> bool:
        return self.path is not None

    def to_dict(self) -> dict:
        return {"side": self.side, "label": self.label,
                "available": self.available, "hint": self.hint,
                "command": self.command}


def clip_sources(song, candidate_override=None) -> dict[str, ClipSource]:
    """Both clip sources, resolved independently and without raising.

    The hints are :func:`candidate_path`/:func:`reference_path`'s own messages
    rather than a second wording, so the console and the CLI cannot drift on
    what to do next.
    """
    try:
        candidate = ClipSource("cand", "candidate",
                               candidate_path(song, candidate_override))
    except ProjectError as exc:
        candidate = ClipSource("cand", "candidate", hint=str(exc))
    try:
        reference = ClipSource("ref", "reference", reference_path(song))
    except ProjectError as exc:
        # The one side a button can make. It is the stems step row's own
        # command string, which is what `run_step_command` whitelists -- a
        # button offering anything else would be refused by the console.
        reference = ClipSource(
            "ref", "reference", hint=str(exc),
            command=f"rambass stems {song.slug} --drums-only")
    return {"cand": candidate, "ref": reference}


def reference_path(song):
    """The original drums to review against: the separated stem."""
    path = song.path("stems", "drums.wav")
    if not path.is_file():
        raise ProjectError(
            f"{song.slug}: no stems/drums.wav to compare against — run "
            f"`rambass stems {song.slug} --drums-only` first")
    return path


def write_ledger(song, notes, *, version: str = "") -> None:
    """Persist the ledger both ways: YAML source of truth, markdown for eyes."""
    save_review(song.path("qa", "review.yaml"), notes,
                version=version or song.drum_midi_path().name)
    song.path("qa", "review.md").write_text(
        review_markdown(song.title, notes, count_in_bars=song.count_in_bars),
        encoding="utf-8")


def add_note(song, note: Note) -> list[Note]:
    """Append one note to the song's ledger and rewrite both files.

    The CLI and the console both land here, so a note typed over SSH and a
    note clicked in the browser are indistinguishable on disk. Fills in the
    covering section when the caller left it blank.
    """
    if not note.section:
        from .restore import _section_at

        note.section = _section_at(song.sections, note.bar, note.beat)
    version, notes = load_review(song.path("qa", "review.yaml"))
    notes.append(note)
    write_ledger(song, notes, version=version)
    return notes


def rebuild_song(song, *, project_root, dry_run: bool = False,
                 force: str | None = None, step: str | None = None) -> dict:
    """The rebuild, as one code path for the CLI command and the console.

    Runs the auto side of :func:`rebuild_selection` in pipeline order via the
    real ``rambass`` CLI; reports the held side untouched. *force* names one
    held artifact to rebuild anyway — its file is copied to ``.bak`` first,
    because a one-click rebuild must not make overwriting a hand edit easier
    than the terminal already does.
    """
    import shutil

    from .provenance import stale_report

    auto, held = rebuild_selection(stale_report(song))
    if step:
        auto = [e for e in auto if e.step == step]
        # The held side is filtered too. A button that asked for one step and
        # got back the song's whole held list reads as "this is what your click
        # left alone" -- and the one artifact it is *about* is buried in it.
        held = [e for e in held if e.step == step]

    forced = []
    backup_name = ""
    if force:
        match = [e for e in held if e.artifact == force]
        if not match:
            held_names = ", ".join(e.artifact for e in held) or "none"
            raise ProjectError(
                f"--force {force}: not a held (edited/unknown) artifact of "
                f"{song.slug}. Held right now: {held_names}. A stale artifact "
                f"rebuilds without --force.")
        path = song.path(*force.split("/"))
        # Not on a dry run: asking what a force *would* do must not write
        # anything, and the copy used to happen before the run/no-run fork.
        if path.exists() and not dry_run:
            backup = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, backup)
            backup_name = backup.name
        forced = match

    todo = auto + forced
    result: dict = {
        "song": song.slug,
        "dry_run": dry_run,
        "commands": [entry.command for entry in todo],
        "ran": 0,
        "failed": "",
        "outputs": {},
        "backup": backup_name,
        "held": [{"artifact": entry.artifact, "state": entry.state,
                  "reasons": entry.reasons}
                 for entry in held if entry not in forced],
    }
    if todo and not dry_run:
        report = run_rebuild(todo, runner=subprocess_runner(project_root))
        result["ran"] = report["ran"]
        result["failed"] = report["failed"]
        result["outputs"] = report["outputs"]
    return result


def runnable_commands(song, report) -> set[str]:
    """Every command this song's own stage screens put a button on.

    The whitelist for :func:`run_step_command`, and it is the screens
    themselves rather than a second list -- so the browser can ask for exactly
    what the console offered it and nothing else, and the two cannot drift.
    """
    return {step["command"]
            for screen in song_screen(song, report)["screens"]
            for step in screen["steps"]
            if step["kind"] == "run" and step["command"]}


def run_step_command(song, command: str, *, project_root, report) -> dict:
    """Run one screen row's command, for the rows staleness does not track.

    `rambass analyze --write`, `lyrics transcribe`, `lyrics check`,
    `video card` and `countin` produce nothing in :data:`~rambass.provenance.
    PIPELINE`, so there is no step for :func:`rebuild_song` to filter on --
    and a button that posts an empty rebuild instead means *the whole chain*,
    which is how clicking "Find the tempo" started a demucs separation.

    There is nothing to gate here (no provenance, so no hand edit to protect);
    the gate is the whitelist, and the report shape matches
    :func:`rebuild_song` so one log renders both.
    """
    if command not in runnable_commands(song, report):
        raise ProjectError(
            f"{command!r} is not a step {song.slug} offers. The console can "
            f"only run what it put a button on.")
    code, output = subprocess_runner(project_root)(command)
    return {"song": song.slug, "dry_run": False, "commands": [command],
            "ran": 0 if code else 1, "failed": command if code else "",
            "outputs": {command: output}, "backup": "", "held": []}


# ── what the console's screens are made of ───────────────────────────────────

#: Which dashboard column each pipeline step reports into. The drum chain's
#: later variants all land on `quantize` — to a viewer they are one fact,
#: "the cleaned MIDI is out of date". The practice pair reports nowhere:
#: practice must never gate the gig, and a stale warp ringing a show column
#: would do exactly that.
STAGE_OF_STEP = {
    "stems": "stems",
    "drums transcribe": "drums_midi",
    "drums clean": "quantize",
    "drums consolidate": "quantize",
    "drums restore": "quantize",
    "click": "render",
    "gx100 midi": "gx100",
    "video ass": "video",
    "video render": "video",
}


def dashboard_row(song, report) -> dict:
    """One dashboard row: the manifest's own status, plus provenance flags.

    Two separate facts per cell on purpose — "is this stage considered
    finished" (`song.status`, what `rambass mark` sets) and "is what's on disk
    provably out of date" (`rambass stale`). Collapsing them into one colour
    would make the dashboard lie in one direction or the other.
    """
    from .manifest import STAGES

    flags: dict = {}
    for entry in report:
        stage = STAGE_OF_STEP.get(entry.step)
        if not stage:
            continue
        cell = flags.setdefault(stage, {"stale": False, "edited": False})
        if entry.state == "stale":
            cell["stale"] = True
        if entry.state == "edited":
            cell["edited"] = True
    done, total = song.progress()
    return {
        "slug": song.slug,
        "album": song.album,
        "title": song.title,
        "origin": song.drums_origin,
        "done": done,
        "total": total,
        "cells": {
            stage: {"status": song.status.get(stage, "todo"),
                    **flags.get(stage, {"stale": False, "edited": False})}
            for stage in STAGES
        },
    }


def song_screen(song, report) -> dict:
    """Everything one song's stage screens need, in display order.

    The drum cluster is one screen (see :data:`DRUM_CLUSTER`); every other
    stage stands alone. Steps that produce a provenance artifact carry its
    current state, so a Run button can read "done, but stale".
    """
    from .manifest import STAGES

    by_artifact = {entry.artifact: entry for entry in report}
    screens = []
    seen_cluster = False
    for stage in STAGES:
        if stage in DRUM_CLUSTER:
            if seen_cluster:
                continue
            seen_cluster = True
            key, title = "drums", "Drums (transcribe, clean, kit)"
        else:
            key, title = stage, stage
        steps = []
        for row in steps_for(song, key):
            artifact = row.artifact.format(slug=song.slug)
            entry = by_artifact.get(artifact)
            # A row can name an artifact that no PIPELINE step produces --
            # `render/sticks.wav` is the one -- so it never gets an entry and
            # used to read as the grey n/a dot for a file sitting on disk next
            # to the click track. Existence is the only fact available for it.
            # Only as a fallback: a tracked artifact's verdict always wins, or
            # a present-but-unprovenanced click would report a confident `ok`.
            state = row.state
            if entry is None and not state and artifact:
                state = "ok" if song.path(*artifact.split("/")).exists() else "missing"
            steps.append({
                "label": row.label, "command": row.command, "kind": row.kind,
                "note": row.note, "artifact": artifact,
                # The provenance step name, which is the key `rebuild_song`
                # filters on. Without it a Run button can only say "rebuild",
                # meaning the whole chain -- which is how clicking Run on the
                # click track started a demucs separation.
                "step": entry.step if entry else "",
                "state": entry.state if entry else state,
                "reasons": entry.reasons if entry else [],
            })
        status = (song.status.get(stage, "todo") if key != "drums" else
                  song.status.get("drums_midi", "todo"))
        screens.append({"stage": key, "title": title, "status": status,
                        "steps": steps})

    counts = {"stale": 0, "edited": 0, "missing": 0}
    for entry in report:
        if entry.state in counts:
            counts[entry.state] += 1
    _, notes = load_review(song.path("qa", "review.yaml"))
    return {
        "slug": song.slug,
        "title": song.title,
        "album": song.album,
        "origin": song.drums_origin,
        "screens": screens,
        "counts": counts,
        "open_notes": sum(1 for n in notes if n.status == "open"),
    }


def grid_rows(performance, span: ClipSpan) -> list[dict]:
    """The instrument grid for one span: a row per canonical name with hits.

    This is the toms-and-cymbals visibility Reaper's default drum view does
    not give — one row per *instrument name*, in kit order, straight off the
    MIDI. It shows what the MIDI says, mislabels included: the grid surfaces
    problems, it does not independently verify instrument identity.
    """
    from .drummap import CANONICAL

    timeline = performance.timeline
    lo = span.candidate_start - 1e-9
    hi = span.candidate_start + span.duration - 1e-9
    by_instrument: dict[str, list[dict]] = {}
    for hit in performance.sorted_hits():
        if not lo <= hit.time < hi:
            continue
        bar, beat = timeline.seconds_to_bar_beat(hit.time)
        by_instrument.setdefault(hit.instrument, []).append(
            {"bar": bar, "beat": round(beat, 3), "velocity": hit.velocity})
    order = {name: index for index, name in enumerate(CANONICAL)}
    return [{"instrument": name, "ticks": ticks}
            for name, ticks in sorted(by_instrument.items(),
                                      key=lambda kv: order.get(kv[0], 999))]


def section_performance(performance, span: ClipSpan):
    """The hits inside *span*, shifted so the span starts at zero.

    EZdrummer's browser plays a groove from its own zero; a slice keeping
    absolute song time would import with the whole preceding song as silence.
    """
    from .midiio import DrumPerformance

    lo = span.candidate_start - 1e-9
    hi = span.candidate_start + span.duration - 1e-9
    hits = [hit.moved_to(hit.time - span.candidate_start)
            for hit in performance.hits if lo <= hit.time < hi]
    return DrumPerformance(hits, performance.timeline,
                           f"{performance.name} - {span.name}")
