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

    *runner* takes the command string and returns its exit code. The CLI and
    the console inject a subprocess runner (the real ``rambass`` CLI, so this
    can never produce a result a human typing the same command would not);
    tests inject a recorder. Trusting the input is deliberate — the selection
    above is the only gate, and callers pass its *auto* side.
    """
    report: dict = {"ran": 0, "failed": "", "log": []}
    for entry in entries:
        report["log"].append(entry.command)
        if runner(entry.command) != 0:
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
    """
    import shlex
    import subprocess
    import sys

    def run(command: str) -> int:
        words = shlex.split(command)
        if words and words[0] == "rambass":
            words = [sys.executable, "-m", "rambass"] + words[1:]
        return subprocess.run(words, cwd=cwd).returncode

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
    """

    label: str
    command: str = ""
    kind: str = "run"
    artifact: str = ""
    note: str = ""


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
            return [StepRow("Reference recording in source/", kind="manual",
                            note="optional — for rehearsal only")]
        if stage == "rehearsed":
            return [StepRow("Play it through with the band", kind="manual")]
        return []

    if stage == "source":
        return [StepRow("Original mix in source/", kind="manual",
                        note="the WAV, not the MP3 — separation quality sets "
                             "the ceiling (drums-rebuild.md Stage 0)")]
    if stage == "analyze":
        return [StepRow("Find the tempo", f"rambass analyze {slug} --write",
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
