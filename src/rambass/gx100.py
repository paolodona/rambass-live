"""BOSS GX-100 pedalboard control over MIDI.

Facts this module is built on, from the official *GX-100 MIDI Implementation*
(Roland, ver 1.10, 2022-03-03):

* The unit acts on channel voice messages whose channel matches
  ``MENU:MIDI:MIDI SETTING:RX CHANNEL``.
* **Bank Select** is CC#0 (MSB) with values ``0-2`` only, followed by CC#32
  (LSB) which must be ``0``. After power-up the unit behaves as if bank 0 were
  selected until it receives a bank select.
* **Program Change** carries a program number ``0-127`` and switches to whatever
  memory the pedal's own ``MENU:MIDI:PROGRAM MAP`` has assigned to that
  (bank, program) slot. There are three mappable banks of 128 slots, and each
  slot can point at any memory ``0-299`` — that is ``U01-1``..``U50-4`` followed
  by the presets ``P01-1``..``P25-4``.
* CC#1-31, #33-63 and #64-95 are recognised as **assign sources**, so a CC lane
  in Reaper can ride an expression parameter — wah, delay level, whammy.
* The unit locks to **MIDI timing clock** when
  ``MENU:MIDI:MIDI SETTING:SYNC CLOCK`` is not ``INTERNAL``, which means
  tempo-synced delays and tremolo follow the backing track for free.

The important consequence: a program change alone is *not* enough information to
know which memory the pedal will land on — that depends on the PROGRAM MAP
stored in the unit. So this module keeps an explicit map in
``config/gx100.yaml`` mirroring what is programmed into the pedal, and
``rambass gx100 map`` prints the table to type in (or to check against).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import mido
import yaml

from .manifest import Song
from .midiio import TICKS_PER_BEAT
from .project import Project, ProjectError

#: Total addressable memories: 200 user (U01-1..U50-4) + 100 preset (P01-1..P25-4).
USER_MEMORIES = 200
PRESET_MEMORIES = 100
TOTAL_MEMORIES = USER_MEMORIES + PRESET_MEMORIES

MAX_BANK = 2          # bank select MSB accepts 0-2
SLOTS_PER_BANK = 128  # program change 0-127

CONFIG_NAME = "gx100.yaml"


def memory_to_index(memory: str) -> int:
    """``"U01-1"`` -> 0, ``"U50-4"`` -> 199, ``"P01-1"`` -> 200."""
    text = memory.strip().upper()
    if len(text) < 4 or text[0] not in "UP" or "-" not in text:
        raise ProjectError(f"not a GX-100 memory name: {memory!r} (expected e.g. U03-2)")
    bank_text, _, slot_text = text[1:].partition("-")
    if not bank_text.isdigit() or not slot_text.isdigit():
        raise ProjectError(f"not a GX-100 memory name: {memory!r}")
    bank, slot = int(bank_text), int(slot_text)
    limit = 50 if text[0] == "U" else 25
    if not 1 <= bank <= limit or not 1 <= slot <= 4:
        raise ProjectError(
            f"{memory!r} is out of range "
            f"({'U01-1..U50-4' if text[0] == 'U' else 'P01-1..P25-4'})"
        )
    base = 0 if text[0] == "U" else USER_MEMORIES
    return base + (bank - 1) * 4 + (slot - 1)


def index_to_memory(index: int) -> str:
    """Inverse of :func:`memory_to_index`."""
    if not 0 <= index < TOTAL_MEMORIES:
        raise ProjectError(f"memory index {index} outside 0-{TOTAL_MEMORIES - 1}")
    prefix = "U" if index < USER_MEMORIES else "P"
    local = index if index < USER_MEMORIES else index - USER_MEMORIES
    return f"{prefix}{local // 4 + 1:02d}-{local % 4 + 1}"


@dataclass
class ProgramMap:
    """Which (bank, program change) slot selects which memory.

    ``assignments`` maps ``(bank, program)`` to a memory name. Anything not
    listed falls back to the sequential default — bank 0 slot 0 is ``U01-1``,
    slot 1 is ``U01-2``, and so on — which is the layout ``rambass gx100 map``
    prints for you to enter into the pedal.
    """

    channel: int = 1
    assignments: dict[tuple[int, int], str] = field(default_factory=dict)
    sequential_default: bool = True

    def slot_for(self, memory: str) -> tuple[int, int]:
        """Memory name -> ``(bank, program_change)``."""
        wanted = memory.strip().upper()
        for slot, assigned in sorted(self.assignments.items()):
            if assigned.upper() == wanted:
                return slot
        if not self.sequential_default:
            raise ProjectError(
                f"memory {memory!r} is not in the GX-100 program map and "
                "sequential_default is off — add it to config/gx100.yaml"
            )
        index = memory_to_index(wanted)
        bank, program = divmod(index, SLOTS_PER_BANK)
        if bank > MAX_BANK:
            raise ProjectError(
                f"{memory!r} maps to bank {bank}, but the GX-100 only accepts "
                f"bank select 0-{MAX_BANK}"
            )
        return bank, program

    def memory_for(self, bank: int, program: int) -> str:
        if (bank, program) in self.assignments:
            return self.assignments[(bank, program)]
        index = bank * SLOTS_PER_BANK + program
        if index >= TOTAL_MEMORIES:
            return "(unassigned)"
        return index_to_memory(index)

    def table(self, limit: int | None = None) -> list[tuple[int, int, str]]:
        """``(bank, program, memory)`` rows, for printing or checking."""
        rows: list[tuple[int, int, str]] = []
        for bank in range(MAX_BANK + 1):
            for program in range(SLOTS_PER_BANK):
                if bank * SLOTS_PER_BANK + program >= TOTAL_MEMORIES:
                    break
                rows.append((bank, program, self.memory_for(bank, program)))
        return rows[:limit] if limit else rows


def load_program_map(project: Project | None = None) -> ProgramMap:
    project = project or Project.discover()
    path = project.config_dir / CONFIG_NAME
    if not path.is_file():
        return ProgramMap()
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    assignments: dict[tuple[int, int], str] = {}
    for entry in data.get("assignments") or []:
        bank = int(entry["bank"])
        program = int(entry["pc"])
        if not 0 <= bank <= MAX_BANK:
            raise ProjectError(f"{path}: bank must be 0-{MAX_BANK}, got {bank}")
        if not 0 <= program <= 127:
            raise ProjectError(f"{path}: pc must be 0-127, got {program}")
        assignments[(bank, program)] = str(entry["memory"]).upper()
    return ProgramMap(
        channel=int(data.get("channel", 1)),
        assignments=assignments,
        sequential_default=bool(data.get("sequential_default", True)),
    )


@dataclass(frozen=True)
class PatchEvent:
    """A resolved patch change ready to be written as MIDI."""

    seconds: float
    bar: int
    memory: str
    bank: int
    program: int
    name: str = ""


def resolve_patch_changes(song: Song, program_map: ProgramMap) -> list[PatchEvent]:
    """Turn the manifest's patch changes into concrete bank/PC pairs."""
    timeline = song.timeline()
    events: list[PatchEvent] = []
    for change in sorted(song.patch_changes, key=lambda c: c.bar):
        bank, program = program_map.slot_for(change.memory)
        events.append(
            PatchEvent(
                seconds=timeline.bar_beat_to_seconds(change.bar, 1.0),
                bar=change.bar,
                memory=change.memory,
                bank=bank,
                program=program,
                name=change.name,
            )
        )
    return events


def write_patch_midi(
    path: str | Path,
    song: Song,
    program_map: ProgramMap,
    *,
    lead_ms: float = 120.0,
    ticks_per_beat: int = TICKS_PER_BEAT,
) -> tuple[Path, list[PatchEvent]]:
    """Write a MIDI file of bank-select + program-change pairs for one song.

    *lead_ms* pulls every change slightly earlier. Switching a memory is not
    instantaneous and a patch that lands exactly on the downbeat arrives late —
    a hair early is inaudible, a hair late is a missed entry. The first change of
    the song is never pulled before the start of the file.
    """
    events = resolve_patch_changes(song, program_map)
    timeline = song.timeline()
    channel = max(0, min(15, song.gx100_channel - 1))

    midi = mido.MidiFile(type=1, ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.name = f"GX-100 ch{song.gx100_channel}"
    midi.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(song.bpm), time=0))

    cursor = 0
    for event in events:
        seconds = max(0.0, event.seconds - lead_ms / 1000.0)
        tick = max(0, int(round(timeline.seconds_to_quarters(seconds) * ticks_per_beat)))
        track.append(
            mido.Message("control_change", channel=channel, control=0,
                         value=event.bank, time=tick - cursor)
        )
        track.append(
            mido.Message("control_change", channel=channel, control=32, value=0, time=0)
        )
        track.append(
            mido.Message("program_change", channel=channel, program=event.program, time=0)
        )
        cursor = tick

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    midi.save(str(path))
    return path, events


def patch_sheet(songs: list[Song], program_map: ProgramMap) -> str:
    """A printable cheat sheet — the page that gets taped to the pedalboard."""
    lines = [
        "# GX-100 patch sheet — Ramba S.S.",
        "",
        "One row per patch change, in running order. `bank`/`PC` are what the",
        "backing track sends; `memory` is what should light up on the pedal.",
        "",
    ]
    for index, song in enumerate(songs, start=1):
        events = resolve_patch_changes(song, program_map)
        lines.append(f"## {index:02d}. {song.title}  —  {song.bpm:g} BPM, "
                     f"MIDI ch {song.gx100_channel}")
        if not events:
            lines += ["", "_no patch changes programmed yet_", ""]
            continue
        lines += [
            "",
            "| bar | time | memory | bank | PC | what |",
            "|----:|-----:|:-------|-----:|---:|:-----|",
        ]
        timeline = song.timeline()
        for event in events:
            audio = timeline.audio_time(event.bar, 1.0)
            minutes, seconds = divmod(audio, 60)
            lines.append(
                f"| {event.bar} | {int(minutes)}:{seconds:04.1f} | `{event.memory}` | "
                f"{event.bank} | {event.program} | {event.name or ''} |"
            )
        lines.append("")
    return "\n".join(lines)
