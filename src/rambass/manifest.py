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
    "render",       # backing track bounced, count-in in place, loudness matched
    "lyrics",       # timed cue file exists and passes `rambass lyrics check`
    "video",        # lyric video rendered against the final backing track
    "gx100",        # pedalboard patch changes programmed
    "rehearsed",    # played through with the band on the gig rig
)

STATUS_VALUES = ("todo", "wip", "done", "n/a")

#: Drum MIDI variants, least to most finished. `raw` is absent on purpose: it is
#: the unquantised take and is never a backing track.
DRUM_VARIANT_ORDER = ("quantized", "consolidated", "restored")

#: What a section may declare its backbeat to be, when no detector can settle it.
#:
#: Deliberately short. This is not a general re-voicing facility — it names the
#: one thing that recurs across an album and that measurement cannot decide: a
#: verse played with the stick across the rim and a chorus played on the head.
#: See :func:`~rambass.restore.voice_backbeats` for why Manlio's verse-2 forced
#: it. Lives here rather than in ``restore.py`` because it is manifest schema,
#: and ``manifest.py`` must not import anything that pulls in mido.
BACKBEAT_ARTICULATIONS = ("", "sidestick", "snare")

#: The keywords ``sections[].hats`` accepts besides an explicit mask.
HAT_PATTERNS = ("run", "shuffle")


def hat_slots(
    declaration: str,
    *,
    subdivision: int,
    beats_per_bar: int,
    where: str = "",
) -> tuple[bool, ...] | None:
    """Which slots of a bar a section's ``hats`` declaration plays.

    ``None`` for an empty declaration, which means "do nothing" — today's
    behaviour, and the default for every section that has not been listened to.

    * ``run`` — every slot of ``drums.subdivision``.
    * ``shuffle`` — the first and last slot of each beat, which needs a triplet
      grid to mean anything.
    * an explicit mask of ``x`` and ``.``, one character per slot in a bar.
      Whitespace and ``|`` are ignored so a reader can group it by beat:
      ``"x.x | x.x | x.x | x.x"``.

    Anything else raises :class:`~rambass.project.ProjectError`, because a typo
    here fills nothing and reads exactly like a no-op — the worst way for a
    declaration to fail, and the same argument :meth:`Song.problems` makes for
    checking both ends of a ``voicing`` map.
    """
    from .project import ProjectError

    declaration = (declaration or "").strip()
    if not declaration:
        return None
    slots = max(subdivision, 1) * max(beats_per_bar, 1)
    named = f"{where} " if where else ""
    if declaration == "run":
        return (True,) * slots
    if declaration == "shuffle":
        if subdivision != 3:
            raise ProjectError(
                f"{named}declares hats: shuffle, which is the first and last "
                f"slot of each beat and needs a triplet grid — this song's "
                f"drums.subdivision is {subdivision}. Write the mask out if "
                f"that is really what you mean")
        return tuple(index % subdivision in (0, subdivision - 1)
                     for index in range(slots))
    mask = declaration.replace(" ", "").replace("\t", "").replace("|", "")
    if set(mask) - {"x", "."}:
        unexpected = "".join(sorted(set(mask) - {"x", "."}))
        raise ProjectError(
            f"{named}declares hats: {declaration!r}; expected one of "
            f"{', '.join(HAT_PATTERNS)}, or a mask of 'x' and '.' — "
            f"{unexpected!r} is neither")
    if len(mask) != slots:
        raise ProjectError(
            f"{named}declares a hats mask {len(mask)} slots long; a bar of "
            f"{beats_per_bar} beats at drums.subdivision {subdivision} is "
            f"{slots} slots")
    return tuple(character == "x" for character in mask)


#: Where this song's accompaniment comes from.
#:
#: * ``a-cappella`` — nowhere. The band sings it unaccompanied, so there is no
#:   backing track, no click, no count-in and nothing to play back. Everything
#:   except getting a reference recording and rehearsing it is ``n/a``.
#: * ``backing-track`` — a finished base already exists with the drums mixed
#:   into it; nothing to separate, transcribe or quantise. Most of Diversamente
#:   Giovani, plus I Pooh.
#: * ``recorded`` / ``extracted`` / ``programmed`` — the drums still have to be
#:   turned into MIDI, from real tracks, from the stereo mix, or from scratch.
DRUM_ORIGINS = (
    "a-cappella", "backing-track", "recorded", "extracted", "programmed",
)

#: Pipeline stages that make no sense once a finished backing track exists.
BACKING_TRACK_NA = ("stems", "drums_midi", "quantize", "kit")

#: An unaccompanied song needs none of the production pipeline at all.
A_CAPPELLA_APPLICABLE = ("source", "rehearsed")

#: How the audience relates to a song. Drives where it can safely sit in a set.
#:
#: ``signature`` is the one song people came for, and it is a tier of its own
#: rather than a loud ``hit``: it is a card you play once, so it belongs in the
#: closing run. Metallica's M72 tour ran 99 shows with no encore at all and put
#: Enter Sandman at 17 and Master of Puppets at 18 — of 19. The biggest songs go
#: near the end of the main set, not first and not held back for an encore.
#: Unfamiliar material goes in the middle, where the songs either side carry it.
STANDINGS = ("signature", "hit", "known", "deep", "new")

#: What a song is *for* in a set, where that is fixed rather than a choice.
ROLES = ("", "opener", "closer", "interlude", "detour", "linking")

#: 1 = ballad, 5 = flat out. Deliberately separate from heaviness: Il Phurgone is
#: high energy and not remotely heavy; Mandami un Faxe is neither.
ENERGY_RANGE = (1, 5)
HEAVINESS_RANGE = (1, 5)


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


@dataclass
class Section:
    """A named part of the arrangement, anchored to a bar and a beat.

    ``beat`` is 1-based and defaults to the downbeat, so every section written
    before it existed keeps working and nothing gains a redundant ``beat: 1.0``.
    It exists because the arrangements really do this: Manlio has a 2.5-bar
    break, which puts the verse after it on beat 3 of a bar. Paolo: "we use odd
    measures and sections do not always fall on the bar."

    Fractional beats are legal — 3.5 is the second eighth of beat 3 — because a
    12/8 shuffle has somewhere to be that a whole beat cannot name.

    Musical bars, never Reaper's. See CLAUDE.md: the ruler number belongs on a
    marker label and nowhere else, because ``count_in.bars`` can change.
    """

    name: str
    bar: int
    beat: float = 1.0
    note: str = ""
    #: How this section's backbeat is played, when no detector can settle it.
    #:
    #: ``sidestick`` is the case that made it exist: Manlio's three verses play
    #: beats 2 and 4 with the stick across the rim and the lifts and choruses
    #: play them on the head, and in verse-2 the two are spectrally identical —
    #: see :func:`~rambass.restore.voice_backbeats` for the measurement. This is
    #: arrangement structure, not a tuning knob: it is settled by ear once, the
    #: same way ``drums.subdivision`` is, and never re-derived from audio.
    backbeat: str = ""
    #: ``{played: should be}`` for this section, applied by
    #: :func:`~rambass.restore.revoice_sections`.
    #:
    #: Paolo, on Manlio's finale: *"in theme finale move all hihat_open to a
    #: ride"* — a ride bell. That is 66 hits, and the mechanism that existed was
    #: a ``swap-hit`` note per hit promoting to 66 removals and 66 additions.
    #: But the decision is one statement *about the section*, the same category
    #: as ``backbeat`` above, and writing it 132 times means a re-transcription
    #: that moves one hit by a triplet leaves stale removals behind and the
    #: section half re-voiced. Named instruments survive that; positions do not.
    #:
    #: Where ``backbeat`` is deliberately a closed list of two articulations,
    #: this is the general facility: any canonical name to any canonical name,
    #: checked against :data:`~rambass.drummap.CANONICAL` in :meth:`problems`
    #: because a typo here re-voices nothing and looks exactly like a no-op.
    voicing: dict = field(default_factory=dict)
    #: This section's hi-hat pattern, applied by
    #: :func:`~rambass.restore.fill_hat_runs`. See :func:`hat_slots` for the
    #: grammar: ``run``, ``shuffle``, or a mask of ``x`` and ``.``.
    #:
    #: The largest single cause of Manlio's review notes — 66 of them are holes
    #: in a continuous hi-hat run and 89 in total are a hat pattern the pipeline
    #: got wrong — and **it is not recoverable from the audio**. The threshold
    #: sweep and the hat-stem flux probe both fail, and the occupancy count
    #: cannot tell ``chorus-1`` (a continuous triplet run reading 6 of 12 slots)
    #: from ``chorus-2`` (a genuine shuffle reading 9). So it is declared, the
    #: same category as ``backbeat`` and ``voicing`` above: arrangement
    #: structure, settled by ear once, never re-derived from audio.
    #:
    #: **One field, not two.** An earlier draft proposed a separate
    #: ``backbeat_hat`` for the hat that
    #: :func:`~rambass.transcribe.drop_hats_on_sidesticks` removes under a
    #: side-stick. It is unnecessary: verse-1's and verse-2's ground truth is a
    #: hat on all twelve triplet slots, so ``hats: run`` covers the side-stick
    #: holes as a side effect.
    hats: str = ""

    @property
    def position(self) -> tuple[int, float]:
        """Sort key. Two sections can share a bar, so the bar alone is not one."""
        return (self.bar, self.beat)

    @classmethod
    def from_dict(cls, data: dict) -> Section:
        return cls(
            name=str(data["name"]),
            bar=int(data["bar"]),
            beat=float(data.get("beat", 1.0)),
            note=str(data.get("note", "")),
            backbeat=str(data.get("backbeat", "") or ""),
            voicing={str(played): str(wanted) for played, wanted
                     in (data.get("voicing") or {}).items()},
            hats=str(data.get("hats", "") or ""),
        )

    def to_dict(self) -> dict:
        out: dict = {"name": self.name, "bar": self.bar}
        if self.beat != 1.0:
            out["beat"] = self.beat
        if self.backbeat:
            out["backbeat"] = self.backbeat
        if self.voicing:
            out["voicing"] = dict(self.voicing)
        if self.hats:
            out["hats"] = self.hats
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
class Character:
    """What a song *is*, musically — the input to ordering a set.

    This is the band's own read of each song, not anything derived from the
    audio. It exists so a running order can be argued about with numbers instead
    of adjectives, and so `rambass setlist arc` can flag a stretch of the set
    that will sag.
    """

    genre: str = ""
    energy: int = 3
    heaviness: int = 3
    standing: str = "known"
    role: str = ""
    note: str = ""

    @classmethod
    def from_dict(cls, data: dict | None) -> Character:
        data = data or {}
        return cls(
            genre=str(data.get("genre", "")),
            energy=int(data.get("energy", 3)),
            heaviness=int(data.get("heaviness", 3)),
            standing=str(data.get("standing", "known")),
            role=str(data.get("role", "")),
            note=str(data.get("note", "")),
        )

    def to_dict(self) -> dict:
        return {
            "genre": self.genre,
            "energy": self.energy,
            "heaviness": self.heaviness,
            "standing": self.standing,
            "role": self.role,
            "note": self.note,
        }

    def problems(self) -> list[str]:
        out: list[str] = []
        if not ENERGY_RANGE[0] <= self.energy <= ENERGY_RANGE[1]:
            out.append(f"character.energy must be {ENERGY_RANGE[0]}-{ENERGY_RANGE[1]}")
        if not HEAVINESS_RANGE[0] <= self.heaviness <= HEAVINESS_RANGE[1]:
            out.append(
                f"character.heaviness must be {HEAVINESS_RANGE[0]}-{HEAVINESS_RANGE[1]}"
            )
        if self.standing not in STANDINGS:
            out.append(f"character.standing must be one of {', '.join(STANDINGS)}")
        if self.role not in ROLES:
            named = ", ".join(role for role in ROLES if role)
            out.append(f"character.role must be empty or one of {named}")
        return out


@dataclass(frozen=True)
class Addition:
    """A hit put back by hand at Stage 7, bar-anchored so a re-run keeps it.

    Lives here rather than in ``restore.py`` because it is manifest schema, and
    ``manifest.py`` must not import anything that pulls in mido.
    """

    bar: int
    beat: float = 1.0
    instrument: str = "crash"
    velocity: int = 0
    note: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> Addition:
        return cls(
            bar=int(data["bar"]),
            beat=float(data.get("beat", 1.0)),
            instrument=str(data.get("instrument", "crash")),
            velocity=int(data.get("velocity", 0) or 0),
            note=str(data.get("note", "")),
        )

    def to_dict(self) -> dict:
        out: dict = {"bar": self.bar}
        if self.beat != 1.0:
            out["beat"] = self.beat
        out["instrument"] = self.instrument
        if self.velocity:
            out["velocity"] = self.velocity
        if self.note:
            out["note"] = self.note
        return out


@dataclass(frozen=True)
class Removal:
    """A hit taken out by hand at Stage 7. An empty instrument clears the slot."""

    bar: int
    beat: float = 1.0
    instrument: str = ""
    note: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> Removal:
        return cls(
            bar=int(data["bar"]),
            beat=float(data.get("beat", 1.0)),
            instrument=str(data.get("instrument", "") or ""),
            note=str(data.get("note", "")),
        )

    def to_dict(self) -> dict:
        out: dict = {"bar": self.bar}
        if self.beat != 1.0:
            out["beat"] = self.beat
        if self.instrument:
            out["instrument"] = self.instrument
        if self.note:
            out["note"] = self.note
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
    #: Quantise grid per beat. 4 = 16ths, 3 = triplet 8ths for a shuffle. This
    #: is a property of the *song*, not of whoever last typed a command: Manlio
    #: is a shuffle whose hi-hat sits on 0, 1/3 and 2/3 of the beat, and a run
    #: of `drums clean` with the default 4 silently drags every triplet 83 ms
    #: onto a 16th. Same argument as tempo living here.
    drum_subdivision: int = 4
    #: Grid per beat for the crash family, which is never snapped as tight as
    #: the rest of the kit -- a crash is heard as an event, not a subdivision.
    drum_cymbal_subdivision: int = 2
    #: How loud a *declared* backbeat articulation is stamped. 0 = not decided,
    #: which is not velocity 0: :func:`~rambass.restore.voice_backbeats` then
    #: uses the median of the population the detector named, which is the only
    #: number measured on the right instrument.
    #:
    #: It needs to live here for the same reason the subdivision does. That
    #: median is honest on the part's own scale and can still be inaudible
    #: through a given kit -- Manlio's 50 declared rim clicks all came out v45,
    #: the floor, under hi-hats at 75-98 and snares at 109, and Paolo could not
    #: hear them. How loud a rim click should be is a decision about the kit,
    #: settled by ear once; a `--backbeat-velocity` flag is whoever last typed a
    #: command, and the next re-run drops silently back to the floor.
    drum_backbeat_velocity: int = 0
    #: Fixed per-instrument velocities and a downbeat lift, same argument as
    #: `backbeat_velocity` above. `quantize.shape_velocities`'s own reasoning is
    #: "accenting musically usually sounds better than trusting the analysis" --
    #: but until this lived here that accent only ever came from
    #: `--accent-kick`/`--accent-snare`/`--downbeat-boost`, which is whoever last
    #: typed the command: the next plain `drums clean` drops silently back to
    #: the measured contour. Empty/zero means "trust `scale_velocities`", not
    #: "silence".
    drum_accents: dict = field(default_factory=dict)
    drum_downbeat_boost: int = 0
    #: Stage 7's hand edits, bar-anchored so they survive a re-transcription.
    #:
    #: A crash drawn into the Reaper MIDI item is gone the next time
    #: ``drums transcribe`` runs. Declared here it is reapplied every time, it is
    #: in git, and ``rambass drums missing`` can tick it off the checklist -- so
    #: there is a record of how far through Stage 7 a song actually is.
    #: :class:`~rambass.restore.Addition` / :class:`~rambass.restore.Removal`.
    drum_additions: list = field(default_factory=list)
    drum_removals: list = field(default_factory=list)
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
    character: Character = field(default_factory=Character)

    status: dict = field(default_factory=dict)
    notes: str = ""
    #: Cut from the show. Kept on disk so the decision is recorded rather than
    #: lost, but excluded from the board and from every progress count.
    excluded: bool = False
    exclude_reason: str = ""
    extra: dict = field(default_factory=dict)

    # ── (de)serialisation ────────────────────────────────────────────────
    @classmethod
    def from_dict(cls, data: dict, directory: Path | None = None) -> Song:
        # Any key not listed here is carried through untouched in `extra`, so a
        # field the tools do not know about survives a round trip. Every field
        # that *is* modelled must be listed, or its stale copy in `extra` will be
        # written back over the modelled value on save.
        known = {
            "slug", "title", "album", "track", "tempo", "count_in", "bars", "key",
            "sections", "drums", "source", "stems", "click", "gx100", "video",
            "status", "notes", "excluded", "reason", "character",
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
            drum_subdivision=int(drums.get("subdivision", 4) or 4),
            drum_cymbal_subdivision=int(drums.get("cymbal_subdivision", 2) or 2),
            drum_backbeat_velocity=int(drums.get("backbeat_velocity", 0) or 0),
            drum_accents={str(k): int(v)
                          for k, v in (drums.get("accents") or {}).items()},
            drum_downbeat_boost=int(drums.get("downbeat_boost", 0) or 0),
            drum_additions=[Addition.from_dict(a)
                            for a in _as_list(drums.get("additions"))],
            drum_removals=[Removal.from_dict(r)
                           for r in _as_list(drums.get("removals"))],
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
            character=Character.from_dict(data.get("character")),
            status={k: str(v) for k, v in (data.get("status") or {}).items()},
            notes=str(data.get("notes", "")),
            excluded=bool((data.get("excluded") or {}).get("from_set", False)
                          if isinstance(data.get("excluded"), dict)
                          else data.get("excluded", False)),
            exclude_reason=str((data.get("excluded") or {}).get("reason", ""))
            if isinstance(data.get("excluded"), dict) else str(data.get("reason", "")),
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
                "subdivision": self.drum_subdivision,
                "cymbal_subdivision": self.drum_cymbal_subdivision,
                **({"backbeat_velocity": self.drum_backbeat_velocity}
                   if self.drum_backbeat_velocity else {}),
                **({"accents": dict(self.drum_accents)} if self.drum_accents else {}),
                **({"downbeat_boost": self.drum_downbeat_boost}
                   if self.drum_downbeat_boost else {}),
                **({"additions": [a.to_dict() for a in self.drum_additions]}
                   if self.drum_additions else {}),
                **({"removals": [r.to_dict() for r in self.drum_removals]}
                   if self.drum_removals else {}),
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
            "character": self.character.to_dict(),
            "status": {stage: self.status.get(stage, "todo") for stage in STAGES},
            "notes": self.notes,
        }
        if self.excluded:
            out["excluded"] = {"from_set": True, "reason": self.exclude_reason}
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
        # Hoisted to the top of the method because it is used in three places
        # now -- a section's voicing map, drums.accents and the Stage 7 edits --
        # and a `from ... import` half way down shadows the name above it.
        from .drummap import CANONICAL

        out: list[str] = []
        if self.bpm <= 20 or self.bpm > 300:
            out.append(f"implausible bpm {self.bpm}")
        if self.count_in_bars < 0:
            out.append("count_in.bars cannot be negative")
        seen: dict[tuple[int, float], str] = {}
        for section in self.sections:
            if section.bar < 1:
                out.append(f"section {section.name!r} is at bar {section.bar} (<1)")
            beats_per_bar = self.time_signature[0]
            for change in self.tempo_changes:
                if change.bar <= section.bar and change.time_signature:
                    beats_per_bar = change.time_signature[0]
            if not 1.0 <= section.beat < beats_per_bar + 1:
                out.append(
                    f"section {section.name!r} is at beat {section.beat:g} of a "
                    f"{beats_per_bar}-beat bar; beats are 1-based, so the last "
                    f"one is under {beats_per_bar + 1}"
                )
            if section.position in seen:
                out.append(
                    f"sections {seen[section.position]!r} and {section.name!r} "
                    f"are both at bar {section.bar} beat {section.beat:g}"
                )
            seen[section.position] = section.name
            if section.backbeat not in BACKBEAT_ARTICULATIONS:
                named = ", ".join(a for a in BACKBEAT_ARTICULATIONS if a)
                out.append(
                    f"section {section.name!r} declares backbeat "
                    f"{section.backbeat!r}; expected empty or one of {named}"
                )
            # Both ends of every mapping, against the one list a hit may be
            # named with. A typo here re-voices nothing and reads as a no-op,
            # which is the worst way for a declaration to fail.
            for played, wanted in (section.voicing or {}).items():
                for name in (played, wanted):
                    if name not in CANONICAL:
                        out.append(
                            f"section {section.name!r} voices {played!r} as "
                            f"{wanted!r}, but {name!r} is not a canonical drum "
                            f"name (see drummap.CANONICAL)"
                        )
            # Same argument as the voicing map above: a bad `hats` fills
            # nothing and reads as a no-op, which is the worst way for a
            # declaration to fail.
            try:
                hat_slots(section.hats, subdivision=self.drum_subdivision,
                          beats_per_bar=beats_per_bar,
                          where=f"section {section.name!r}")
            except ProjectError as bad:
                out.append(str(bad))
        if self.bars and any(s.bar > self.bars for s in self.sections):
            out.append("a section starts after the last bar of the song")
        for name, value in (("subdivision", self.drum_subdivision),
                            ("cymbal_subdivision", self.drum_cymbal_subdivision)):
            if value not in (1, 2, 3, 4, 6, 8, 12, 16):
                out.append(
                    f"drums.{name} is {value}; expected a musical grid "
                    "(1, 2, 3, 4, 6, 8, 12 or 16 per beat)"
                )
        if not 0 <= self.drum_backbeat_velocity <= 127:
            out.append(
                f"drums.backbeat_velocity is {self.drum_backbeat_velocity}; "
                "expected a MIDI velocity 1-127, or 0 for \"use the measured "
                "median\""
            )
        for instrument, velocity in self.drum_accents.items():
            if instrument not in CANONICAL:
                out.append(
                    f"drums.accents names {instrument!r}, which is not a drum "
                    "instrument (see drummap.CANONICAL)"
                )
            if not 0 <= velocity <= 127:
                out.append(
                    f"drums.accents.{instrument} is {velocity}; expected a "
                    "MIDI velocity 0-127"
                )
        # Stage 7's edits. A bad one is worth catching here rather than at the
        # moment `drums restore` runs, because the list is hand-written and the
        # command is run near the end of a long session.
        for kind, edits in (("additions", self.drum_additions),
                            ("removals", self.drum_removals)):
            for edit in edits:
                if edit.bar < 1:
                    out.append(
                        f"drums.{kind} has an entry at bar {edit.bar}; bars are "
                        f"1-based and the count-in is not one of them"
                    )
                if edit.instrument and edit.instrument not in CANONICAL:
                    out.append(
                        f"drums.{kind} names {edit.instrument!r}, which is not a "
                        f"drum instrument (see drummap.CANONICAL)"
                    )
                if kind == "additions" and not edit.instrument:
                    out.append("drums.additions needs an instrument on every entry")
        if not 1 <= self.gx100_channel <= 16:
            out.append(f"gx100.channel must be 1-16, got {self.gx100_channel}")
        for change in self.patch_changes:
            if not _valid_memory(change.memory):
                out.append(
                    f"gx100 memory {change.memory!r} is not a GX-100 memory name "
                    "(expected U01-1 .. U50-4 or P01-1 .. P25-4)"
                )
        out.extend(self.character.problems())
        for stage, value in self.status.items():
            if stage not in STAGES:
                out.append(f"unknown status stage {stage!r}")
            if value not in STATUS_VALUES:
                out.append(f"status.{stage} must be one of {', '.join(STATUS_VALUES)}")
        return out

    def grid_advice(self) -> str:
        """Warning for a cymbal grid that does not share the kit's, or "".

        Deliberately **not** in :meth:`problems`: that list is fatal through
        :meth:`validate`, and a mismatched grid is a thing you probably did not
        mean rather than a file that cannot be loaded. A triplet kit with the
        cymbals on 8ths pulls every crash 83 ms off the beat at 60 BPM.
        """
        triplet = self.drum_subdivision % 3 == 0
        if triplet == (self.drum_cymbal_subdivision % 3 == 0):
            return ""
        return (
            f"drums.subdivision {self.drum_subdivision} and "
            f"drums.cymbal_subdivision {self.drum_cymbal_subdivision} do not share "
            f"a grid, so the crashes land off the beat the rest of the kit is "
            f"snapped to (83 ms at 60 BPM between a triplet and a 16th)"
        )

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

    def align_anchor(self) -> float | None:
        """Bar 1's position in the original recording, from practice/align.yaml.

        The source side of the alignment map (docs/practice-tracks.md), and the
        one number the whole drum rebuild hangs off: get it wrong and every hit
        lands on the wrong grid line while every timing report still reads fine.
        Stored so it is measured once, inspectable, correctable by ear, and used
        by everything after — rather than re-derived differently by each caller.
        """
        path = self.path("practice", "align.yaml")
        if not path.exists():
            return None
        # Defensively: this file is documented as hand-correctable, so a
        # half-finished edit is an ordinary state for it to be in, and a
        # traceback out of an unrelated command is a poor way to find out.
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return None
        for entry in data.get("anchors") or []:
            if not isinstance(entry, dict):
                continue
            try:
                if int(entry.get("bar", 0)) == 1:
                    return float(entry["at"])
            except (TypeError, ValueError, KeyError):
                continue
        return None

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
        if self.excluded:
            return dict.fromkeys(STAGES, "n/a")
        if self.drums_origin == "a-cappella":
            return {
                stage: ("todo" if stage in A_CAPPELLA_APPLICABLE else "n/a")
                for stage in STAGES
            }
        status = dict.fromkeys(STAGES, "todo")
        if self.drums_origin == "backing-track":
            for stage in BACKING_TRACK_NA:
                status[stage] = "n/a"
        elif self.drums_origin != "extracted":
            status["stems"] = "n/a"
        return status

    def drum_midi_path(self, variant: str = "quantized") -> Path:
        return self.path("midi", f"drums-{variant}.mid")

    def best_drum_midi(self) -> Path:
        """The most finished drum variant that exists, for the Reaper project.

        `reaper build` used to default to ``quantized``, which is the part before
        the section vote and before Stage 7 -- so after doing all of that work the
        project still played the version from three stages back, silently, and
        looking exactly like a build that had worked.

        ``raw`` is deliberately not in the order. It is the unquantised take, and
        a project that quietly used it would be off the grid.
        """
        for variant in reversed(DRUM_VARIANT_ORDER):
            candidate = self.drum_midi_path(variant)
            if candidate.exists():
                return candidate
        return self.drum_midi_path("quantized")

    @property
    def length_known(self) -> bool:
        """Whether this song's length is a fact rather than a fallback.

        `bars` is set by `rambass analyze --write`, by `rambass reaper import`,
        or by hand. Until then :meth:`total_bars` is guessing, and anything built
        on it — a set running time, a click length — is guessing too.
        """
        return bool(self.bars)

    def total_bars(self) -> int:
        """Best guess at song length in bars.

        Check :attr:`length_known` before presenting anything derived from this
        as a number the user can rely on.
        """
        if self.bars:
            return self.bars
        if self.sections:
            return max(s.bar for s in self.sections) + 8
        return 64

    def section_at(self, bar: int, beat: float = 1.0) -> Section | None:
        """The section sounding at this position, or None before the first one.

        Takes a *beat* because a section can start mid-bar: on Manlio the
        downbeat of bar 20 is still the break, and the verse only starts on
        beat 3 of it. Defaulting to the downbeat keeps every existing caller
        asking the question it was already asking.
        """
        current = None
        for section in sorted(self.sections, key=lambda s: s.position):
            if section.position <= (bar, beat):
                current = section
        return current

    def consolidation_spans(self) -> list:
        """The section list :func:`~rambass.quantize.consolidate` wants.

        Each section's **exact** extent, from its own bar and beat to the next
        one's, as a :class:`~rambass.quantize.SectionSpan`. Not rounded to bar
        lines: consolidate tiles the bar grid itself but judges each slot
        against the repetitions it could have appeared in, so it consolidates
        within the section wherever the section starts. Rounding here would
        throw away the half-bars at either end *and* leave their hits
        unconsolidated inside a consolidated section, which is the incoherence
        Stage 6 exists to remove.

        Sections sharing a name are pooled by consolidate, so this deliberately
        preserves duplicate names rather than making them unique.
        """
        from .quantize import SectionSpan

        ordered = sorted(self.sections, key=lambda s: s.position)
        out: list = []
        for index, section in enumerate(ordered):
            if index + 1 < len(ordered):
                following = ordered[index + 1]
                end_bar, end_beat = following.bar, following.beat
            else:
                end_bar, end_beat = self.total_bars() + 1, 1.0
            out.append(SectionSpan(section.name, section.bar, end_bar,
                                   start_beat=section.beat, end_beat=end_beat))
        return out

    def progress(self) -> tuple[int, int]:
        """``(done, applicable)`` across the pipeline stages.

        An excluded song contributes nothing to either number — a cut song must
        not make the project look less finished than it is.
        """
        if self.excluded:
            return 0, 0
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
