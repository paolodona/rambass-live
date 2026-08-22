"""Canonical drum-instrument names and the note maps that realise them.

Transcription and quantising work with *names* (``kick``, ``hihat_open``) and
only turn into note numbers at write time. That way a MIDI file can be
retargeted from General MIDI to whatever kit ends up in the Reaper project
without re-running any analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .project import Project, ProjectError

#: The instruments the transcriber can produce, coarsest first.
CANONICAL = (
    "kick", "kick_alt",
    "snare", "snare_electric", "sidestick", "clap",
    "hihat_closed", "hihat_pedal", "hihat_open",
    "tom_low", "tom_low_mid", "tom_mid", "tom_high_mid", "tom_high", "tom_highest",
    "crash", "crash_2", "crash_choke", "ride", "ride_2", "ride_bell",
    "china", "china_choke", "splash",
    "tambourine", "cowbell",
)

#: General MIDI fallback, so a partial custom map still produces valid MIDI.
GM_NOTES: dict[str, int] = {
    "kick": 36, "kick_alt": 35,
    "snare": 38, "snare_electric": 40, "sidestick": 37, "clap": 39,
    "hihat_closed": 42, "hihat_pedal": 44, "hihat_open": 46,
    "tom_low": 41, "tom_low_mid": 43, "tom_mid": 45,
    "tom_high_mid": 47, "tom_high": 48, "tom_highest": 50,
    "crash": 49, "crash_2": 57, "ride": 51, "ride_2": 59, "ride_bell": 53,
    "china": 52, "splash": 55, "tambourine": 54, "cowbell": 56,
}

#: Canonical names General MIDI cannot express, and what they degrade to.
#:
#: A choked cymbal is a stab; a ringing one is a wash, and in a metal
#: arrangement that is the difference the listener actually hears. Toontrack
#: gives chokes their own note numbers, so the names have to exist for a real kit
#: map to point at (docs/drums-rebuild.md) — but GM has no choke articulation at
#: all. Rather than duplicate a GM note (which would break the reverse lookup) or
#: invent one (which the repo does not do), a choke played through a GM kit
#: degrades to its ringing sibling, and says so here rather than silently.
GM_DEGRADES_TO: dict[str, str] = {
    "crash_choke": "crash",
    "china_choke": "china",
}


@dataclass(frozen=True)
class DrumMap:
    """A named mapping from canonical instrument to MIDI note number."""

    name: str
    notes: dict[str, int]
    channel: int = 10
    description: str = ""

    @property
    def midi_channel_index(self) -> int:
        """Zero-based channel, which is what mido wants."""
        return self.channel - 1

    def note_for(self, instrument: str) -> int:
        if instrument in self.notes:
            return self.notes[instrument]
        if instrument in GM_NOTES:
            return GM_NOTES[instrument]
        fallback = GM_DEGRADES_TO.get(instrument)
        if fallback:
            return self.notes.get(fallback, GM_NOTES[fallback])
        raise ProjectError(
            f"unknown drum instrument {instrument!r}; "
            f"expected one of {', '.join(CANONICAL)}"
        )

    def instrument_for(self, note: int) -> str:
        """Reverse lookup, used when importing someone else's MIDI."""
        for name, number in self.notes.items():
            if number == note:
                return name
        for name, number in GM_NOTES.items():
            if number == note:
                return name
        return f"note{note}"


GENERAL_MIDI = DrumMap(
    name="general-midi",
    notes=dict(GM_NOTES),
    channel=10,
    description="General MIDI level 1 percussion key map",
)


def load_drum_map(name: str, project: Project | None = None) -> DrumMap:
    """Load ``config/drum-maps/<name>.yaml``, falling back to built-in GM."""
    if name in ("", "general-midi", "gm"):
        return GENERAL_MIDI
    project = project or Project.discover()
    path = project.drum_maps_dir / f"{name}.yaml"
    if not path.is_file():
        available = ", ".join(sorted(p.stem for p in project.drum_maps_dir.glob("*.yaml")))
        raise ProjectError(f"no drum map {name!r} in {project.drum_maps_dir} (have: {available})")
    return load_drum_map_file(path)


def load_drum_map_file(path: Path) -> DrumMap:
    with Path(path).open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    raw_notes = data.get("notes") or {}
    notes: dict[str, int] = {}
    for instrument, value in raw_notes.items():
        if value is None:          # placeholder in the template — fall back to GM
            continue
        note = int(value)
        if not 0 <= note <= 127:
            raise ProjectError(f"{path}: note for {instrument!r} is {note}, outside 0-127")
        notes[str(instrument)] = note
    channel = int(data.get("channel", 10))
    if not 1 <= channel <= 16:
        raise ProjectError(f"{path}: channel must be 1-16, got {channel}")
    return DrumMap(
        name=str(data.get("name", Path(path).stem)),
        notes=notes,
        channel=channel,
        description=str(data.get("description", "")),
    )
