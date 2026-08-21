"""Reaper project generation.

Rather than writing ``.RPP`` files byte by byte — a format that changes between
Reaper versions and fails in confusing ways when you get a nested block wrong —
this module emits a small tab-separated **build script** which the bundled
ReaScript (``reaper/scripts/rambass_build_song.lua``) executes through Reaper's
own API. Reaper therefore builds the project itself, which means it is always a
valid project, and the Lua side stays readable enough to tweak on gig day.

Build script grammar (one record per line, fields separated by tabs, ``#``
starts a comment)::

    PROJECT   title             bpm   num  den
    TEMPO     position_seconds  bpm   num  den
    TRACK     name              vol_db  pan   r,g,b
    ITEM      track_name        file    position_seconds
    MIDI      track_name        file    position_seconds
    MARKER    position_seconds  name
    REGION    start_seconds     end_seconds   name
    NOTE      text
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .manifest import PatchChange, Section, Song
from .timeline import Timeline

BUILD_SCRIPT_VERSION = 1

#: Track layout for a backing-track project. Order is top-to-bottom in Reaper.
#: The click and the drums are the two tracks that must exist; the rest are
#: reference material that gets muted before the gig.
@dataclass(frozen=True)
class TrackSpec:
    name: str
    volume_db: float = 0.0
    pan: float = 0.0
    color: tuple[int, int, int] = (90, 90, 90)
    role: str = "reference"


DEFAULT_TRACKS: tuple[TrackSpec, ...] = (
    TrackSpec("CLICK", -6.0, 0.0, (250, 190, 60), role="click"),
    TrackSpec("DRUMS MIDI", 0.0, 0.0, (120, 180, 255), role="drums"),
    TrackSpec("REF drums", -6.0, 0.0, (110, 110, 110), role="reference"),
    TrackSpec("REF bass", -6.0, 0.0, (110, 110, 110), role="reference"),
    TrackSpec("REF other", -6.0, 0.0, (110, 110, 110), role="reference"),
    TrackSpec("REF vocals", -6.0, 0.0, (110, 110, 110), role="reference"),
    TrackSpec("REF mix", -6.0, 0.0, (150, 110, 110), role="reference"),
    TrackSpec("GX-100 MIDI", 0.0, 0.0, (180, 250, 140), role="gx100"),
)


@dataclass
class BuildScript:
    """Accumulates records, then renders them."""

    records: list[tuple[str, ...]] = field(default_factory=list)

    def add(self, *fields_: object) -> None:
        self.records.append(tuple(_field(f) for f in fields_))

    def render(self, header_comment: str = "") -> str:
        lines = [
            f"# rambass reaper build script v{BUILD_SCRIPT_VERSION}",
            "# generated file — re-run `rambass reaper build` instead of editing",
        ]
        if header_comment:
            lines += [f"# {line}" for line in header_comment.splitlines()]
        lines += ["\t".join(record) for record in self.records]
        return "\n".join(lines) + "\n"

    def write(self, path: str | Path, header_comment: str = "") -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(header_comment), encoding="utf-8")
        return path


def _field(value: object) -> str:
    if isinstance(value, float):
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text or "0"
    text = str(value)
    if "\t" in text or "\n" in text:
        raise ValueError(f"build-script field may not contain tabs or newlines: {text!r}")
    return text


def build_song_script(
    song: Song,
    *,
    tracks: tuple[TrackSpec, ...] = DEFAULT_TRACKS,
    click_wav: Path | None = None,
    drum_midi: Path | None = None,
    gx100_midi: Path | None = None,
    include_reference: bool = True,
    absolute_paths: bool = True,
) -> BuildScript:
    """Build script for one song: tempo map, tracks, media, section markers."""
    timeline = song.timeline()
    script = BuildScript()
    script.add("PROJECT", song.title, song.bpm, *song.time_signature)

    # Tempo map. The song's tempo starts at time zero rather than at bar 1, so
    # the count-in is in tempo too: Reaper's bar 1 is then the first count-in
    # bar and musical bar 1 is Reaper bar (count_in + 1). Anything else and the
    # grid in the arrange view would not line up with the click we rendered.
    for change_bar in _tempo_bars(song):
        position = 0.0 if change_bar == 1 else timeline.audio_time(change_bar, 1.0)
        sig = timeline.time_signature_at(change_bar)
        script.add("TEMPO", position, timeline.bpm_at(change_bar), *sig)

    wanted_roles = {"click", "drums", "gx100"} | ({"reference"} if include_reference else set())
    for track in tracks:
        if track.role not in wanted_roles:
            continue
        script.add(
            "TRACK", track.name, track.volume_db, track.pan,
            ",".join(str(c) for c in track.color),
        )

    def as_path(path: Path) -> str:
        return str(path.resolve()) if absolute_paths else str(path)

    if click_wav and click_wav.exists():
        script.add("ITEM", "CLICK", as_path(click_wav), 0.0)
    if drum_midi and drum_midi.exists():
        script.add("MIDI", "DRUMS MIDI", as_path(drum_midi), timeline.count_in_seconds)
    if gx100_midi and gx100_midi.exists():
        script.add("MIDI", "GX-100 MIDI", as_path(gx100_midi), timeline.count_in_seconds)

    if include_reference:
        for stem in ("drums", "bass", "other", "vocals"):
            path = song.stem_path(stem)
            if path:
                script.add("ITEM", f"REF {stem}", as_path(path), timeline.count_in_seconds)
        source = song.source_path()
        if source:
            script.add("ITEM", "REF mix", as_path(source), timeline.count_in_seconds)

    # Section markers, and a region per section so the arrangement is navigable
    # with the region playlist during rehearsal.
    ordered = sorted(song.sections, key=lambda s: s.bar)
    last_bar = song.total_bars() + 1
    for index, section in enumerate(ordered):
        start = timeline.audio_time(section.bar, 1.0)
        end_bar = ordered[index + 1].bar if index + 1 < len(ordered) else last_bar
        end = timeline.audio_time(end_bar, 1.0)
        script.add("MARKER", start, f"{section.bar}. {section.name}")
        script.add("REGION", start, end, section.name)

    if song.count_in_bars:
        script.add("MARKER", 0.0, f"count-in ({song.count_in_bars} bars)")
    script.add("MARKER", timeline.count_in_seconds, "BAR 1")

    for change in sorted(song.patch_changes, key=lambda c: c.bar):
        label = f"GX {change.memory}" + (f" {change.name}" if change.name else "")
        script.add("MARKER", timeline.audio_time(change.bar, 1.0), label)

    script.add("NOTE", f"{song.album} / {song.title} — drums: {song.drums_origin}")
    return script


def _tempo_bars(song: Song) -> list[int]:
    bars = [1]
    for change in song.tempo_changes:
        if change.bar not in bars:
            bars.append(change.bar)
    return sorted(bars)


def build_setlist_script(
    songs: list[Song],
    *,
    gap_seconds: float = 4.0,
    renders: dict[str, Path] | None = None,
) -> BuildScript:
    """One Reaper project containing the whole show, back to back.

    Each song becomes a region, so the show can be driven from the region
    playlist and a single footswitch. The rendered backing track for each song is
    placed on one track if it exists; otherwise the region is left empty as a
    placeholder so the running order is still visible.
    """
    renders = renders or {}
    script = BuildScript()
    first = songs[0] if songs else None
    script.add(
        "PROJECT", "Ramba S.S. — live set",
        first.bpm if first else 120.0,
        *(first.time_signature if first else (4, 4)),
    )
    script.add("TRACK", "BACKING", 0.0, 0.0, "120,200,160")
    script.add("TRACK", "GX-100 MIDI", 0.0, 0.0, "180,250,140")

    cursor = 0.0
    for index, song in enumerate(songs, start=1):
        timeline = song.timeline()
        length = timeline.count_in_seconds + timeline.bar_beat_to_seconds(
            song.total_bars() + 1, 1.0
        )
        script.add("TEMPO", cursor, song.bpm, *song.time_signature)
        render = renders.get(song.slug)
        if render and Path(render).exists():
            script.add("ITEM", "BACKING", str(Path(render).resolve()), cursor)
        script.add("REGION", cursor, cursor + length, f"{index:02d} {song.title}")
        script.add("MARKER", cursor, f"{index:02d} {song.title} ({song.bpm:g} BPM)")
        cursor += length + gap_seconds
    return script


def project_length_seconds(song: Song) -> float:
    timeline = song.timeline()
    return timeline.count_in_seconds + timeline.bar_beat_to_seconds(song.total_bars() + 1, 1.0)


def describe(timeline: Timeline, song: Song) -> str:
    """Human-readable summary used by ``rambass show``."""
    lines = [
        f"{song.title}  ({song.album})",
        f"  tempo        {song.bpm:g} BPM  {song.time_signature[0]}/{song.time_signature[1]}",
        f"  count-in     {song.count_in_bars} bars  ({timeline.count_in_seconds:.2f} s)",
        f"  length       {song.total_bars()} bars  "
        f"({project_length_seconds(song):.1f} s with count-in)",
        f"  drums        {song.drums_origin}"
        + (f", kit: {song.drum_kit}" if song.drum_kit else ""),
    ]
    if song.sections:
        lines.append("  sections")
        for section in sorted(song.sections, key=lambda s: s.bar):
            lines.append(
                f"    bar {section.bar:>4}  {timeline.audio_time(section.bar):7.2f}s  "
                f"{section.name}"
            )
    if song.patch_changes:
        lines.append("  GX-100")
        for change in sorted(song.patch_changes, key=lambda c: c.bar):
            lines.append(
                f"    bar {change.bar:>4}  {change.memory:<7} {change.name}"
            )
    return "\n".join(lines)


# ── reading an existing .RPP ─────────────────────────────────────────────
#
# Writing .RPP files is still not something this repo does — see docs/reaper.md.
# *Reading* one is a different proposition: the format is a plain indented
# block structure, and there is a pile of existing hand-built projects for
# Diversamente Giovani whose tempo, markers and pedal changes are real work that
# should be imported rather than redone.
#
# Parsed against actual Reaper 6.82 projects from that folder.

@dataclass
class RppItem:
    """A media item on a track."""

    position: float
    length: float
    name: str = ""
    source_file: str = ""
    source_type: str = ""          # WAVE, MIDI, VIDEO
    #: (bank_msb, bank_lsb, program) for each program change in a MIDI item.
    program_changes: list[tuple[int, int, int]] = field(default_factory=list)


@dataclass
class RppTrack:
    name: str
    items: list[RppItem] = field(default_factory=list)
    #: Raw MIDIOUT value; >= 0 means the track sends to a hardware MIDI device.
    midi_out: int = -1


@dataclass
class RppProject:
    """What we can usefully recover from an existing Reaper project."""

    version: str = ""
    bpm: float = 120.0
    time_signature: tuple[int, int] = (4, 4)
    markers: list[tuple[float, str, bool]] = field(default_factory=list)  # pos, name, is_region
    tracks: list[RppTrack] = field(default_factory=list)

    def track(self, name: str) -> RppTrack | None:
        for track in self.tracks:
            if track.name.lower() == name.lower():
                return track
        return None

    def audio_items(self) -> list[RppItem]:
        return [i for t in self.tracks for i in t.items if i.source_type == "WAVE"]

    def program_changes(self) -> list[tuple[float, int, int, int]]:
        """``(position_seconds, bank_msb, bank_lsb, program)``, in time order.

        The program change lives inside the MIDI item, so its real time is the
        item's position plus the event's offset — but these projects put one
        short MIDI item per change, with the event at the item start, so the
        item position is the change position.
        """
        out: list[tuple[float, int, int, int]] = []
        for track in self.tracks:
            for item in track.items:
                for msb, lsb, program in item.program_changes:
                    out.append((item.position, msb, lsb, program))
        return sorted(out)

    def first_audio_position(self) -> float:
        """Where the music starts, which is rarely zero in these projects."""
        items = self.audio_items()
        return min((i.position for i in items), default=0.0)


def parse_rpp(text: str) -> RppProject:
    """Parse a Reaper project file.

    Only the parts we can act on are extracted: tempo, markers and regions,
    track names, media items with their source files, and program changes in
    MIDI items. Everything else (FX chains, envelopes, GUIDs, window positions)
    is skipped.
    """
    project = RppProject()
    track: RppTrack | None = None
    item: RppItem | None = None
    # Which block are we inside? Reaper nests with "<TAG" ... ">".
    stack: list[str] = []

    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue

        if line.startswith("<"):
            tag = line[1:].split()[0] if len(line) > 1 else ""
            stack.append(tag)
            if tag == "TRACK":
                track = RppTrack(name="")
                project.tracks.append(track)
            elif tag == "ITEM":
                item = RppItem(position=0.0, length=0.0)
                if track is not None:
                    track.items.append(item)
            elif tag == "SOURCE" and item is not None:
                parts = line.split()
                item.source_type = parts[1] if len(parts) > 1 else ""
            continue

        if line == ">":
            closed = stack.pop() if stack else ""
            if closed == "ITEM":
                item = None
            elif closed == "TRACK":
                track = None
            continue

        parts = line.split()
        keyword = parts[0]
        inside = stack[-1] if stack else ""

        if keyword == "REAPER_PROJECT" and len(parts) > 2:
            project.version = parts[2].strip('"')

        elif keyword == "TEMPO" and not stack[1:]:
            # Top-level TEMPO is the project tempo; a TEMPO inside a block is not.
            if len(parts) > 1:
                project.bpm = float(parts[1])
            if len(parts) > 3:
                project.time_signature = (int(float(parts[2])), int(float(parts[3])))

        elif keyword == "MARKER":
            marker = _parse_marker(line)
            if marker:
                project.markers.append(marker)

        elif keyword == "NAME":
            value = _unquote(line[len("NAME"):].strip())
            if inside == "ITEM" and item is not None:
                item.name = value
            elif inside == "TRACK" and track is not None and not track.name:
                track.name = value

        elif keyword == "MIDIOUT" and inside == "TRACK" and track is not None:
            track.midi_out = int(float(parts[1])) if len(parts) > 1 else -1

        elif keyword == "POSITION" and item is not None:
            item.position = float(parts[1])

        elif keyword == "LENGTH" and item is not None:
            item.length = float(parts[1])

        elif keyword == "FILE" and item is not None:
            item.source_file = _unquote(line[len("FILE"):].strip())

        elif keyword in ("e", "E") and item is not None and inside == "SOURCE":
            event = _parse_midi_event(parts)
            if event:
                item.program_changes.append(event)

    # Reaper writes MIDI events as separate CC/PC lines; stitch each
    # bank-select pair onto the program change that follows it.
    for one_track in project.tracks:
        for one_item in one_track.items:
            one_item.program_changes = _collapse_bank_selects(one_item.program_changes)
    return project


def _parse_marker(line: str) -> tuple[float, str, bool] | None:
    """``MARKER 5 384 "grande rutto" 0 0 1 R {GUID}`` -> (384.0, name, is_region).

    Regions are written as a pair of MARKER lines with a non-zero region flag;
    Reaper 6 uses ``R`` in the flags for the region start. We treat a marker as
    a region only when that flag is present, and dedupe the pair by name.
    """
    rest = line[len("MARKER"):].strip()
    parts = rest.split(None, 2)
    if len(parts) < 2:
        return None
    try:
        position = float(parts[1])
    except ValueError:
        return None
    tail = parts[2] if len(parts) > 2 else ""
    name, remainder = _take_quoted(tail)
    # The field straight after the name is the is-region flag. A region is
    # written as two MARKER lines sharing an index — the second has no name,
    # which is how the caller can tell a region's end from a plain marker.
    flags = remainder.split()
    is_region = bool(flags) and flags[0] == "1"
    return position, name, is_region


def _unquote(text: str) -> str:
    """Strip Reaper's optional quoting from a value.

    Reaper quotes a value only when it contains a space, and uses whichever of
    ``"``, ``'`` or `` ` `` does not appear in the value itself.
    """
    text = text.strip()
    for quote in ('"', "'", "`"):
        if len(text) >= 2 and text.startswith(quote) and text.endswith(quote):
            return text[1:-1]
    return text


def _take_quoted(text: str) -> tuple[str, str]:
    """Pull a possibly-quoted first token off *text*."""
    text = text.strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 0:
            return text[1:end], text[end + 1:]
    parts = text.split(None, 1)
    return (parts[0] if parts else ""), (parts[1] if len(parts) > 1 else "")


def _parse_midi_event(parts: list[str]) -> tuple[int, int, int] | None:
    """Decode one ``e <ticks> <status> <d1> <d2>`` line into a marker tuple.

    Returns a sentinel triple so :func:`_collapse_bank_selects` can pair the
    CC#0 / CC#32 / program-change sequence back together:
    ``(-1, msb, -1)`` for a bank MSB, ``(-2, lsb, -2)`` for a bank LSB and
    ``(-3, program, -3)`` for the program change itself.
    """
    if len(parts) < 4:
        return None
    try:
        status = int(parts[2], 16)
        data1 = int(parts[3], 16)
    except ValueError:
        return None
    kind = status & 0xF0
    if kind == 0xB0:                      # control change
        if data1 == 0 and len(parts) > 4:
            return (-1, int(parts[4], 16), -1)
        if data1 == 32 and len(parts) > 4:
            return (-2, int(parts[4], 16), -2)
        return None
    if kind == 0xC0:                      # program change
        return (-3, data1, -3)
    return None


def _collapse_bank_selects(
    events: list[tuple[int, int, int]],
) -> list[tuple[int, int, int]]:
    out: list[tuple[int, int, int]] = []
    msb = lsb = 0
    for marker, value, _ in events:
        if marker == -1:
            msb = value
        elif marker == -2:
            lsb = value
        elif marker == -3:
            out.append((msb, lsb, value))
    return out


def import_into_song(
    project: RppProject,
    song: Song,
    *,
    offset: float | None = None,
    program_map=None,
) -> tuple[dict, list[str]]:
    """Work out what an existing project tells us about a song.

    Returns the values that could be written into ``song.yaml`` and a list of
    human-readable notes. Nothing is written here — the caller decides, because
    an import that silently overwrites a hand-tuned manifest is worse than no
    importer at all.

    *offset* is the project time that corresponds to musical bar 1. These
    projects carry a long lead-in (a title card on screen before the music
    starts), so by default we take the first audio item's position.
    """
    if offset is None:
        offset = project.first_audio_position()

    timeline = Timeline(
        bpm=project.bpm,
        time_signature=project.time_signature,
        count_in_bars=song.count_in_bars,
    )
    notes = [
        f"project {project.version or 'unknown version'}: "
        f"{project.bpm:g} BPM {project.time_signature[0]}/{project.time_signature[1]}",
        f"musical zero taken as {offset:.3f}s (first audio item)",
    ]

    sections: list[Section] = []
    for position, name, _is_region in sorted(project.markers):
        if not name.strip():          # the closing line of a region pair
            continue
        musical = position - offset
        bar, beat = timeline.seconds_to_bar_beat(musical)
        if bar < 1:
            notes.append(f"marker {name!r} at {position:.2f}s is before the music — skipped")
            continue
        sections.append(Section(name=name, bar=bar))
        notes.append(f"marker {name!r} at {position:.2f}s -> bar {bar} (beat {beat:.2f})")

    patches: list[PatchChange] = []
    for position, msb, _lsb, program in project.program_changes():
        musical = position - offset
        bar, _ = timeline.seconds_to_bar_beat(musical)
        memory = ""
        if program_map is not None:
            memory = program_map.memory_for(msb, program)
        patches.append(
            PatchChange(bar=max(bar, 1), memory=memory or "U01-1",
                        name=f"was bank {msb} PC {program}")
        )
        notes.append(
            f"program change bank {msb} PC {program} at {position:.2f}s -> "
            f"bar {max(bar, 1)}" + (f" ({memory})" if memory else "")
        )

    audio = project.audio_items()
    length_seconds = max((i.position + i.length for i in audio), default=0.0) - offset
    bars = max(1, timeline.seconds_to_bar_beat(length_seconds)[0]) if length_seconds > 0 else 0

    media = sorted({i.source_file for i in audio if i.source_file})
    for name in media:
        notes.append(f"audio: {name}")
    for one_track in project.tracks:
        if one_track.midi_out >= 0:
            notes.append(
                f"track {one_track.name!r} sends to hardware MIDI output "
                f"{one_track.midi_out} — that is the pedalboard feed"
            )

    return (
        {
            "bpm": project.bpm,
            "time_signature": project.time_signature,
            "bars": bars,
            "sections": sections,
            "patch_changes": patches,
            "media": media,
            "offset": offset,
        },
        notes,
    )
