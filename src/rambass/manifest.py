"""The ``song.yaml`` manifest — the single source of truth for one song.

Everything else (Reaper project, click, drum MIDI, lyric video, GX-100 patch
changes, setlist running order) is derived from this file, so it is the only
thing that must be edited by hand and the only thing that really needs to be
in git.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .project import SONG_SUBDIRS, ProjectError, slugify
from .timeline import TempoChange, Timeline, parse_time_signature

MANIFEST_NAME = "song.yaml"

#: Pipeline stages tracked in ``status:``. Order is the order of work.
STAGES = (
    "source",       # original mix / recorded stems are in place
    "analyze",      # tempo + downbeat established
    "stems",        # drums separated out (extracted songs only)
    "drums_midi",   # drum performance exists as MIDI
    "quantize",     # MIDI cleaned up and on the grid
    "kit",          # drum VST kit chosen and sounding right
    "render",       # backing track bounced
    "video",        # lyric / visual video built
    "gx100",        # pedalboard patch changes programmed
    "rehearsed",    # played through with the band
)

STATUS_VALUES = ("todo", "wip", "done", "n/a")

#: How the drums for this song come into existence.
#:
#: ``backing-track`` means a finished backing track already exists with the
#: drums mixed into it — nothing to separate, transcribe or quantise. That is the
#: case for most of Diversamente Giovani, where the band mixed live bases
#: straight out of the album sessions, and it makes the whole drum pipeline
#: ``n/a`` for those songs.
DRUM_ORIGINS = ("backing-track", "recorded", "extracted", "programmed")

#: Pipeline stages that make no sense once a finished backing track exists.
BACKING_TRACK_NA = ("stems", "drums_midi", "quantize", "kit")


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


@dataclass
class Section:
    """A named part of the arrangement, anchored to a bar."""

    name: str
    bar: int
    note: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> Section:
        return cls(name=str(data["name"]), bar=int(data["bar"]), note=str(data.get("note", "")))

    def to_dict(self) -> dict:
        out: dict = {"name": self.name, "bar": self.bar}
        if self.note:
            out["note"] = self.note
        return out


@dataclass
class PatchChange:
    """One GX-100 memory change, anchored to a bar.

    ``memory`` is written the way it is printed on the pedal (``U01-1``,
    ``U12-3``) so what is in the manifest matches what the guitarist sees.
    """

    bar: int
    memory: str
    name: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> PatchChange:
        return cls(
            bar=int(data["bar"]),
            memory=str(data["memory"]).upper(),
            name=str(data.get("name", "")),
        )

    def to_dict(self) -> dict:
        out: dict = {"bar": self.bar, "memory": self.memory}
        if self.name:
            out["name"] = self.name
        return out


@dataclass
class Song:
    """A parsed ``song.yaml`` plus the directory it came from."""

    slug: str
    title: str
    album: str = ""
    track: int = 0
    directory: Path | None = None

    # musical
    bpm: float = 120.0
    time_signature: tuple[int, int] = (4, 4)
    tempo_changes: list[TempoChange] = field(default_factory=list)
    count_in_bars: int = 2
    bars: int = 0                     # length of the song proper, 0 = unknown
    key: str = ""

    # content
    sections: list[Section] = field(default_factory=list)
    drums_origin: str = "extracted"
    drum_kit: str = ""
    drum_map: str = "general-midi"
    source_audio: str = ""
    source_url: str = ""      # where the original came from (Drive link, etc.)
    backing_track: str = ""   # a finished base, relative to render/
    stems: list[str] = field(default_factory=lambda: ["drums", "bass", "other", "vocals"])
    click: dict = field(default_factory=lambda: {"enabled": True, "accent_downbeat": True})

    # live
    gx100_channel: int = 1
    patch_changes: list[PatchChange] = field(default_factory=list)
    video_style: str = "lyrics"
    lyrics_file: str = "lyrics.md"

    status: dict = field(default_factory=dict)
    notes: str = ""
    extra: dict = field(default_factory=dict)

    # ── (de)serialisation ────────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: dict, directory: Path | None = None) -> Song:
        known = {
            "slug", "title", "album", "track", "tempo", "count_in", "bars", "key",
            "sections", "drums", "source", "stems", "click", "gx100", "video",
            "status", "notes",
        }
        tempo = data.get("tempo") or {}
        drums = data.get("drums") or {}
        source = data.get("source") or {}
        gx100 = data.get("gx100") or {}
        video = data.get("video") or {}
        count_in = data.get("count_in") or {}

        origin = str(drums.get("origin", "extracted"))
        if origin not in DRUM_ORIGINS:
            raise ProjectError(
                f"{data.get('slug', '?')}: drums.origin must be one of "
                f"{', '.join(DRUM_ORIGINS)} (got {origin!r})"
            )

        song = cls(
            slug=str(data.get("slug") or slugify(str(data.get("title", "untitled")))),
            title=str(data.get("title", "Untitled")),
            album=str(data.get("album", "")),
            track=int(data.get("track", 0) or 0),
            directory=Path(directory) if directory else None,
            bpm=float(tempo.get("bpm", 120.0)),
            time_signature=parse_time_signature(tempo.get("time_signature", "4/4")),
            tempo_changes=[TempoChange.from_dict(c) for c in _as_list(tempo.get("changes"))],
            count_in_bars=int(count_in.get("bars", 2)),
            bars=int(data.get("bars", 0) or 0),
            key=str(data.get("key", "")),
            sections=[Section.from_dict(s) for s in _as_list(data.get("sections"))],
            drums_origin=origin,
            drum_kit=str(drums.get("kit", "")),
            drum_map=str(drums.get("map", "general-midi")),
            source_audio=str(source.get("audio", "")),
            source_url=str(source.get("url", "")),
            backing_track=str(source.get("backing_track", "")),
            stems=[str(s) for s in _as_list(data.get("stems"))] or
                  ["drums", "bass", "other", "vocals"],
            click={**{"enabled": True, "accent_downbeat": True}, **(data.get("click") or {})},
            gx100_channel=int(gx100.get("channel", 1)),
            patch_changes=[PatchChange.from_dict(p) for p in _as_list(gx100.get("changes"))],
            video_style=str(video.get("style", "lyrics")),
            lyrics_file=str(video.get("lyrics", "lyrics.md")),
            status={k: str(v) for k, v in (data.get("status") or {}).items()},
            notes=str(data.get("notes", "")),
            extra={k: v for k, v in data.items() if k not in known},
        )
        song.validate()
        return song

    def to_dict(self) -> dict:
        out: dict = {
            "slug": self.slug,
            "title": self.title,
            "album": self.album,
            "track": self.track,
            "key": self.key,
            "bars": self.bars,
            "tempo": {
                "bpm": self.bpm,
                "time_signature": f"{self.time_signature[0]}/{self.time_signature[1]}",
            },
            "count_in": {"bars": self.count_in_bars},
            "click": dict(self.click),
            "drums": {
                "origin": self.drums_origin,
                "kit": self.drum_kit,
                "map": self.drum_map,
            },
            "source": {
                "audio": self.source_audio,
                "url": self.source_url,
                "backing_track": self.backing_track,
            },
            "stems": list(self.stems),
            "sections": [s.to_dict() for s in self.sections],
            "gx100": {
                "channel": self.gx100_channel,
                "changes": [p.to_dict() for p in self.patch_changes],
            },
            "video": {"style": self.video_style, "lyrics": self.lyrics_file},
            "status": {stage: self.status.get(stage, "todo") for stage in STAGES},
            "notes": self.notes,
        }
        if self.tempo_changes:
            out["tempo"]["changes"] = [c.to_dict() for c in self.tempo_changes]
        out.update(self.extra)
        return out

    # ── validation ───────────────────────────────────────────────────────
    def validate(self) -> None:
        problems = self.problems()
        if problems:
            raise ProjectError(f"{self.slug}: " + "; ".join(problems))

    def problems(self) -> list[str]:
        """Non-fatal-ish consistency checks, reported by ``rambass check``."""
        out: list[str] = []
        if self.bpm <= 20 or self.bpm > 300:
            out.append(f"implausible bpm {self.bpm}")
        if self.count_in_bars < 0:
            out.append("count_in.bars cannot be negative")
        seen_bars: dict[int, str] = {}
        for section in self.sections:
            if section.bar < 1:
                out.append(f"section {section.name!r} is at bar {section.bar} (<1)")
            if section.bar in seen_bars:
                out.append(
                    f"sections {seen_bars[section.bar]!r} and {section.name!r} "
                    f"are both at bar {section.bar}"
                )
            seen_bars[section.bar] = section.name
        if self.bars and any(s.bar > self.bars for s in self.sections):
            out.append("a section starts after the last bar of the song")
        if not 1 <= self.gx100_channel <= 16:
            out.append(f"gx100.channel must be 1-16, got {self.gx100_channel}")
        for change in self.patch_changes:
            if not _valid_memory(change.memory):
                out.append(
                    f"gx100 memory {change.memory!r} is not a GX-100 memory name "
                    "(expected U01-1 .. U50-4 or P01-1 .. P25-4)"
                )
        for stage, value in self.status.items():
            if stage not in STAGES:
                out.append(f"unknown status stage {stage!r}")
            if value not in STATUS_VALUES:
                out.append(f"status.{stage} must be one of {', '.join(STATUS_VALUES)}")
        return out

    # ── derived ──────────────────────────────────────────────────────────
    def timeline(self) -> Timeline:
        return Timeline(
            bpm=self.bpm,
            time_signature=self.time_signature,
            changes=list(self.tempo_changes),
            count_in_bars=self.count_in_bars,
        )

    @property
    def dir(self) -> Path:
        if self.directory is None:
            raise ProjectError(f"{self.slug} was not loaded from disk")
        return self.directory

    def path(self, *parts: str) -> Path:
        return self.dir.joinpath(*parts)

    def source_path(self) -> Path | None:
        """The original mix, if we can find it."""
        if self.source_audio:
            explicit = self.path("source", self.source_audio)
            return explicit if explicit.exists() else None
        candidates = sorted(
            p for p in self.path("source").glob("*")
            if p.suffix.lower() in {".mp3", ".wav", ".flac", ".m4a", ".aif", ".aiff"}
        )
        return candidates[0] if candidates else None

    def stem_path(self, stem: str) -> Path | None:
        for ext in (".wav", ".flac", ".mp3"):
            candidate = self.path("stems", f"{stem}{ext}")
            if candidate.exists():
                return candidate
        return None

    #: Cue files we know how to read, best first.
    LYRICS_CANDIDATES = ("lyrics.srt", "lyrics.vtt", "lyrics.lrc", "lyrics.md")

    def lyrics_path(self) -> Path | None:
        """The cue file to use for this song, or None if there is not one yet.

        Preference order is SRT, VTT, LRC, then the bar-cue markdown, because a
        hand-timed subtitle file is always the better source when both exist —
        and `rambass new` leaves a markdown template behind, so "both exist" is
        the normal case rather than the exception.

        ``video.lyrics`` only overrides that when it names something outside the
        standard set, which is how a song keeps a file under its own name.
        """
        if self.lyrics_file and self.lyrics_file not in self.LYRICS_CANDIDATES:
            named = self.path(self.lyrics_file)
            if named.is_file():
                return named
        for candidate in self.LYRICS_CANDIDATES:
            path = self.path(candidate)
            if path.is_file():
                return path
        return None

    def backing_track_path(self) -> Path | None:
        """The finished backing track, if there is one on disk.

        ``source.backing_track`` names it; failing that we look for the
        conventional render name. Either way it lives in ``render/``, because it
        is a rendered artefact rather than source material.
        """
        if self.backing_track:
            named = self.path("render", self.backing_track)
            if named.exists():
                return named
        for candidate in (f"{self.slug}.wav", "base.wav"):
            path = self.path("render", candidate)
            if path.exists():
                return path
        return None

    def default_status(self) -> dict:
        """The status a freshly-scaffolded song of this kind should start with."""
        status = dict.fromkeys(STAGES, "todo")
        if self.drums_origin == "backing-track":
            for stage in BACKING_TRACK_NA:
                status[stage] = "n/a"
        elif self.drums_origin != "extracted":
            status["stems"] = "n/a"
        return status

    def drum_midi_path(self, variant: str = "quantized") -> Path:
        return self.path("midi", f"drums-{variant}.mid")

    def total_bars(self) -> int:
        """Best guess at song length in bars."""
        if self.bars:
            return self.bars
        if self.sections:
            return max(s.bar for s in self.sections) + 8
        return 64

    def section_at(self, bar: int) -> Section | None:
        current = None
        for section in sorted(self.sections, key=lambda s: s.bar):
            if section.bar <= bar:
                current = section
        return current

    def progress(self) -> tuple[int, int]:
        """``(done, applicable)`` across the pipeline stages."""
        applicable = [s for s in STAGES if self.status.get(s, "todo") != "n/a"]
        done = [s for s in applicable if self.status.get(s) == "done"]
        return len(done), len(applicable)


def _valid_memory(memory: str) -> bool:
    """GX-100 memory names: ``U01-1``..``U50-4`` and ``P01-1``..``P25-4``."""
    text = memory.strip().upper()
    if len(text) < 5 or text[0] not in "UP" or "-" not in text:
        return False
    bank, _, slot = text[1:].partition("-")
    if not bank.isdigit() or not slot.isdigit():
        return False
    bank_n, slot_n = int(bank), int(slot)
    if not 1 <= slot_n <= 4:
        return False
    return 1 <= bank_n <= (50 if text[0] == "U" else 25)


# ── file IO ──────────────────────────────────────────────────────────────
def load_song(song_dir: Path) -> Song:
    path = Path(song_dir) / MANIFEST_NAME
    if not path.is_file():
        raise ProjectError(f"no {MANIFEST_NAME} in {song_dir}")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ProjectError(f"{path} does not contain a YAML mapping")
    if not data.get("album"):
        data["album"] = Path(song_dir).parent.name
    return Song.from_dict(data, directory=Path(song_dir))


def save_song(song: Song, song_dir: Path | None = None) -> Path:
    target = Path(song_dir) if song_dir else song.dir
    target.mkdir(parents=True, exist_ok=True)
    for sub in SONG_SUBDIRS:
        (target / sub).mkdir(exist_ok=True)
    path = target / MANIFEST_NAME
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            song.to_dict(),
            handle,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
    return path
