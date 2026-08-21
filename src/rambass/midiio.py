"""Reading and writing drum MIDI, with a proper tempo map baked in.

A backing-track MIDI file has to carry its own tempo map: it is what makes the
file drop into Reaper at the right length, and it is what a drum VST needs in
order to place the hits on the grid you quantised them to.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import mido

from .drummap import GENERAL_MIDI, DrumMap
from .timeline import TempoChange, Timeline

TICKS_PER_BEAT = 960
#: Drum notes have no meaningful length; a 32nd is long enough for every VST.
HIT_TICKS = TICKS_PER_BEAT // 8


@dataclass(frozen=True)
class Hit:
    """One drum stroke.

    ``time`` is seconds from the *musical* zero (bar 1 beat 1), so negative
    values are legal and mean "in the count-in".
    """

    instrument: str
    time: float
    velocity: int = 100

    def moved_to(self, time: float) -> Hit:
        return replace(self, time=time)

    def with_velocity(self, velocity: int) -> Hit:
        return replace(self, velocity=max(1, min(127, int(velocity))))


@dataclass
class DrumPerformance:
    """A list of hits plus the tempo map they belong to."""

    hits: list[Hit]
    timeline: Timeline
    name: str = "Drums"

    def sorted_hits(self) -> list[Hit]:
        return sorted(self.hits, key=lambda h: (h.time, h.instrument))

    def instruments(self) -> list[str]:
        return sorted({h.instrument for h in self.hits})

    def count(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for hit in self.hits:
            out[hit.instrument] = out.get(hit.instrument, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def duration(self) -> float:
        return max((h.time for h in self.hits), default=0.0)


def write_drum_midi(
    path: str | Path,
    performance: DrumPerformance,
    drum_map: DrumMap = GENERAL_MIDI,
    *,
    ticks_per_beat: int = TICKS_PER_BEAT,
) -> Path:
    """Write a type-1 MIDI file: tempo map on track 0, drums on track 1."""
    midi = mido.MidiFile(type=1, ticks_per_beat=ticks_per_beat)
    timeline = performance.timeline

    tempo_track = mido.MidiTrack()
    tempo_track.name = "Tempo"
    midi.tracks.append(tempo_track)
    last_tick = 0
    for quarters, bpm, sig in timeline.as_midi_tempo_map():
        tick = int(round(float(quarters) * ticks_per_beat))
        tempo_track.append(
            mido.MetaMessage(
                "time_signature",
                numerator=sig[0],
                denominator=sig[1],
                time=tick - last_tick,
            )
        )
        tempo_track.append(
            mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0)
        )
        last_tick = tick

    drum_track = mido.MidiTrack()
    drum_track.name = performance.name
    midi.tracks.append(drum_track)

    channel = drum_map.midi_channel_index
    events: list[tuple[int, int, str, int]] = []  # (tick, note, kind, velocity)
    for hit in performance.sorted_hits():
        quarters = timeline.seconds_to_quarters(hit.time)
        tick = max(0, int(round(quarters * ticks_per_beat)))
        note = drum_map.note_for(hit.instrument)
        events.append((tick, note, "on", hit.velocity))
        events.append((tick + HIT_TICKS, note, "off", 0))

    events.sort(key=lambda e: (e[0], e[2] == "on"))  # note-offs first at a tie
    cursor = 0
    for tick, note, kind, velocity in events:
        delta = tick - cursor
        drum_track.append(
            mido.Message(
                "note_on" if kind == "on" else "note_off",
                note=note,
                velocity=velocity,
                channel=channel,
                time=delta,
            )
        )
        cursor = tick

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    midi.save(str(path))
    return path


def read_drum_midi(
    path: str | Path,
    drum_map: DrumMap = GENERAL_MIDI,
    *,
    channel: int | None = None,
) -> DrumPerformance:
    """Read a MIDI file into a :class:`DrumPerformance`.

    This is the import path for *Diversamente Giovani*, where the drums already
    exist as a recorded performance: read it, keep the feel, then quantise and
    remap only as much as you actually want to.

    *channel* is 1-based; ``None`` accepts every channel, which is what you
    usually want because exports from other DAWs land on channel 1 as often as
    on 10.
    """
    midi = mido.MidiFile(str(path))
    tpb = midi.ticks_per_beat

    # Pass 1: tempo map, so we can convert ticks to seconds ourselves rather
    # than trusting mido's flattened playback times.
    tempo_events: list[tuple[int, float]] = []
    sig_events: list[tuple[int, tuple[int, int]]] = []
    for track in midi.tracks:
        tick = 0
        for message in track:
            tick += message.time
            if message.type == "set_tempo":
                tempo_events.append((tick, mido.tempo2bpm(message.tempo)))
            elif message.type == "time_signature":
                sig_events.append((tick, (message.numerator, message.denominator)))
    tempo_events.sort()
    sig_events.sort()

    initial_bpm = tempo_events[0][1] if tempo_events and tempo_events[0][0] == 0 else (
        tempo_events[0][1] if tempo_events else 120.0
    )
    initial_sig = sig_events[0][1] if sig_events else (4, 4)
    timeline = _timeline_from_events(tempo_events, sig_events, tpb, initial_bpm, initial_sig)

    hits: list[Hit] = []
    name = Path(path).stem
    for track in midi.tracks:
        tick = 0
        for message in track:
            tick += message.time
            if message.type == "track_name" and not message.name.lower().startswith("tempo"):
                name = message.name or name
            if message.type != "note_on" or message.velocity == 0:
                continue
            if channel is not None and message.channel != channel - 1:
                continue
            seconds = timeline.quarters_to_seconds(tick / tpb)
            hits.append(
                Hit(
                    instrument=drum_map.instrument_for(message.note),
                    time=seconds,
                    velocity=message.velocity,
                )
            )
    return DrumPerformance(hits=hits, timeline=timeline, name=name)


def _timeline_from_events(
    tempo_events: list[tuple[int, float]],
    sig_events: list[tuple[int, tuple[int, int]]],
    ticks_per_beat: int,
    initial_bpm: float,
    initial_sig: tuple[int, int],
) -> Timeline:
    """Rebuild a bar-anchored :class:`Timeline` from tick-anchored MIDI events.

    MIDI anchors tempo changes to ticks; we anchor them to bars. Anything that
    does not land cleanly on a bar line is snapped to the nearest bar, and the
    caller is expected to sanity-check the result — a tempo map that drifts
    mid-bar is a sign the source was never played to a click, which is precisely
    what we are here to fix.
    """
    merged: dict[int, dict] = {}
    for tick, bpm in tempo_events:
        merged.setdefault(tick, {})["bpm"] = bpm
    for tick, sig in sig_events:
        merged.setdefault(tick, {})["sig"] = sig

    changes: list[TempoChange] = []
    bar_tick = 0
    bar = 1
    sig = initial_sig
    for tick in sorted(merged):
        while bar_tick + _bar_ticks(sig, ticks_per_beat) <= tick:
            bar_tick += _bar_ticks(sig, ticks_per_beat)
            bar += 1
        event = merged[tick]
        if tick == 0:
            sig = event.get("sig", sig)
            continue
        sig = event.get("sig", sig)
        changes.append(
            TempoChange(bar=bar, bpm=event.get("bpm"), time_signature=event.get("sig"))
        )
    return Timeline(bpm=initial_bpm, time_signature=initial_sig, changes=changes)


def _bar_ticks(sig: tuple[int, int], ticks_per_beat: int) -> int:
    return int(sig[0] * ticks_per_beat * 4 / sig[1])
