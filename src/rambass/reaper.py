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

from .manifest import Song
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
