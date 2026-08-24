"""Which derived files are stale, and which of the three reasons it is.

Paolo, after finding out the hard way: *"In general, how do I avoid working on
stale files? should we have a staleness check or staleness alert so that old
files are regenerated automatically?"*

An artifact here can go stale three ways, and only the first is the one ``make``
would catch:

1. **an input changed** — a re-separated stem, a new source mix;
2. **``song.yaml`` changed** — a new tempo, a moved section, a declared backbeat,
   a new entry in ``drums.additions``;
3. **the code that produced it changed.** This is the one that actually bit us.
   ``midi/drums-quantized.mid`` was two commits old with a ``hihat_closed v122``
   at Reaper 19.3 where the current transcriber puts an open hi-hat. Its inputs
   were untouched, its manifest was untouched, its mtime was newer than
   everything it was built from — and it was wrong. Nothing on disk said so, and
   the only reason it surfaced is that Paolo listened to it.

So a stamp records all three and :func:`stale_report` says which one moved.

**It never regenerates anything.** A re-transcription is minutes of CPU and it
can change the part under you — which is exactly what happened, and twice in one
session — so the decision belongs to a human who is ready to listen to the
result. The report's job is to make sure nobody is *surprised*, and to print the
command.

Cheap tier on purpose: hashlib, pathlib and pyyaml. A laptop at a venue can run
it, and it does not import mido, librosa or numpy.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import __version__

#: One provenance file per song, next to ``song.yaml``. Not a sidecar per
#: artifact: a directory of ``drums-raw.mid.provenance.yaml`` files is noise in
#: every listing and in every diff, and the whole set is read at once anyway.
PROVENANCE_NAME = "provenance.yaml"

#: Files bigger than this are fingerprinted by sampling rather than in full.
#: A stem is 56 MB and there are five per song; head, middle, tail and the exact
#: length notice a re-separation and cost nothing.
FULL_HASH_LIMIT = 4 << 20
SAMPLE_BYTES = 1 << 20


#: Single values a step reads *out of* a file, rather than the file itself.
#:
#: Second instance of the same lesson as the per-field manifest hashes, so it got
#: a mechanism instead of another special case. `drums transcribe` reads exactly
#: one number from practice/align.yaml -- the bar-1 offset -- but depending on the
#: whole file meant that `align --fit`, which re-fits three hundred anchors and
#: preserves bar 1, declared the entire drum chain stale for a change that cannot
#: move a single hit. A hand correction to bar 1 still must invalidate it, which
#: is the whole reason that number is in that file, so the dependency is on the
#: *value*.
SCALARS = {
    "align/bar1": lambda song: song.align_anchor(),
}


def scalar_hashes(song, names) -> dict:
    """Hash each named scalar. Unknown names are skipped, not raised on."""
    out = {}
    for name in sorted(names):
        resolve = SCALARS.get(name)
        if resolve is None:
            continue
        try:
            value = resolve(song)
        except (OSError, ValueError, KeyError):
            value = None
        out[name] = hashlib.sha256(repr(value).encode()).hexdigest()[:16]
    return out


@dataclass(frozen=True)
class Step:
    """One producing command, and everything that decides whether its output is
    still valid.

    *fields* is the set of top-level ``song.yaml`` keys that matter — per concern
    rather than the whole file, so that editing the lyrics filename does not
    declare the drums stale. Nothing is more corrosive to a staleness report than
    one that cries wolf.
    """

    name: str
    artifact: str
    command: str
    inputs: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    #: Values read out of a file rather than the file itself. See :data:`SCALARS`.
    scalars: tuple[str, ...] = ()
    #: Skip this step entirely for these ``drums.origin`` values.
    skip_origins: tuple[str, ...] = ()

    def artifact_for(self, slug: str) -> str:
        """The artifact path with ``{slug}`` resolved.

        ``video/{slug}.ass`` exists because the video files are named after the
        song while everything in ``midi/`` and ``render/`` has one conventional
        name per song directory. Most artifacts contain no placeholder and come
        back unchanged.
        """
        return self.artifact.format(slug=slug)

    def inputs_for(self, song) -> tuple[str, ...]:
        """The input paths with their placeholders resolved.

        Takes the **song**, not the slug, because ``{source_audio}`` is a
        manifest field and no function of the slug: the real files are
        ``09 Manlio.wav`` and ``04_Bambolina_MST1.wav``. Resolving it against
        the slug alone is what broke `rambass stale`, `rambass stems` and every
        console screen the day both albums had stems on disk -- and it stayed
        hidden until then because the report skips a step whose artifact is
        missing, so nothing ever asked for the name.

        A placeholder with nothing behind it -- a song with no ``source.audio``
        -- drops that input instead of naming ``source/``, which is a directory
        and has no fingerprint.
        """
        values = {"slug": song.slug, "source_audio": song.source_audio}
        blank = {key for key, value in values.items() if not value}
        return tuple(
            name.format(**values) for name in self.inputs
            if not any("{" + key + "}" in name for key in blank)
        )


#: The drum chain and what hangs off it, in order. Both :func:`stale_report` and
#: the ``rambass stale`` command read this and nothing else, so adding a stage
#: means adding a row.
PIPELINE: tuple[Step, ...] = (
    Step(
        name="stems",
        artifact="stems/drums.wav",
        command="rambass stems {slug}",
        inputs=("source/{source_audio}",),
        modules=("stems",),
        skip_origins=("a-cappella", "backing-track"),
    ),
    Step(
        name="drums transcribe",
        artifact="midi/drums-raw.mid",
        command="rambass drums transcribe {slug}",
        inputs=("stems/parts/kick.wav", "stems/parts/snare.wav",
                "stems/parts/hihat.wav", "stems/parts/toms.wav",
                "stems/parts/cymbals.wav", "stems/drums.wav"),
        # Not practice/align.yaml itself: only the bar-1 anchor out of it.
        scalars=("align/bar1",),
        modules=("transcribe", "analyze", "midiio", "drummap"),
        fields=("tempo", "count_in", "bars", "drums/origin", "drums/map",
                "drums/subdivision"),
        skip_origins=("a-cappella", "backing-track"),
    ),
    Step(
        name="drums clean",
        artifact="midi/drums-quantized.mid",
        command="rambass drums clean {slug}",
        inputs=("midi/drums-raw.mid",),
        modules=("quantize", "restore", "midiio"),
        fields=("tempo", "sections", "bars", "drums/map", "drums/subdivision",
                "drums/cymbal_subdivision", "drums/backbeat_velocity",
                "drums/accents", "drums/downbeat_boost"),
        skip_origins=("a-cappella", "backing-track"),
    ),
    Step(
        name="drums consolidate",
        artifact="midi/drums-consolidated.mid",
        command="rambass drums consolidate {slug}",
        inputs=("midi/drums-quantized.mid",),
        modules=("quantize", "midiio"),
        fields=("tempo", "sections", "bars", "drums/map", "drums/subdivision"),
        skip_origins=("a-cappella", "backing-track"),
    ),
    Step(
        name="drums restore",
        artifact="midi/drums-restored.mid",
        command="rambass drums restore {slug}",
        inputs=("midi/drums-consolidated.mid",),
        modules=("restore", "midiio"),
        # Only Stage 7's own edits, plus what places them on the grid.
        fields=("tempo", "bars", "drums/map", "drums/additions",
                "drums/removals"),
        skip_origins=("a-cappella", "backing-track"),
    ),
    # The show chain past the drums. These skip only a-cappella songs: a
    # backing-track song never enters the drum pipeline, but its click, patch
    # changes and video are exactly as real as an extracted song's.
    Step(
        name="click",
        artifact="render/click.wav",
        command="rambass click {slug}",
        modules=("click", "audio"),
        fields=("tempo", "bars", "count_in", "click"),
        skip_origins=("a-cappella",),
    ),
    Step(
        name="gx100 midi",
        artifact="midi/gx100.mid",
        command="rambass gx100 midi {slug}",
        modules=("gx100",),
        # Not "video": the lyrics filename lives under that key, and a patch
        # MIDI that goes stale when somebody renames the lyrics file is the
        # cry-wolf failure the per-field hashes exist to prevent.
        fields=("gx100", "tempo", "count_in"),
        skip_origins=("a-cappella",),
    ),
    Step(
        name="video ass",
        artifact="video/{slug}.ass",
        command="rambass video ass {slug}",
        inputs=("lyrics.srt",),
        modules=("lyrics", "video"),
        fields=("video", "tempo", "count_in"),
        skip_origins=("a-cappella",),
    ),
    Step(
        name="video render",
        artifact="video/{slug}.mp4",
        command="rambass video render {slug}",
        inputs=("video/{slug}.ass",),
        modules=("video", "audio"),
        fields=("video",),
        skip_origins=("a-cappella",),
    ),
    # Practice, and last on purpose. CLAUDE.md: practice must never gate the gig,
    # so nothing in the drum chain above takes an input from `practice/` and this
    # pair takes none from `midi/`. They are here because staleness is a
    # different question from gig-readiness -- found the hard way, when
    # `rambass stems --drums-only` rewrote no_drums.wav, `stale` correctly
    # flagged the whole drum chain, and said nothing at all about the warped
    # reference that had just become a warp of a file that no longer existed.
    Step(
        name="align fit",
        artifact="practice/align.yaml",
        command="rambass align {slug} --fit",
        inputs=("stems/drums.wav",),
        modules=("align", "analyze"),
        fields=("tempo", "bars"),
        skip_origins=("a-cappella", "backing-track"),
    ),
    Step(
        name="align warp",
        artifact="practice/no_drums-aligned.wav",
        command="rambass align {slug} --warp",
        inputs=("practice/align.yaml", "stems/no_drums.wav"),
        modules=("align", "audio"),
        fields=("tempo", "bars"),
        skip_origins=("a-cappella", "backing-track"),
    ),
)


def _relative(song, path) -> str:
    path = Path(path)
    try:
        return path.relative_to(song.directory).as_posix()
    except (ValueError, TypeError):
        return path.as_posix()


def fingerprint(path) -> str:
    """A short content hash. ``""`` for a file that is not there.

    Content and not mtime, because copying stems between machines is normal and a
    timestamp-based check would call every one of them stale. Sampled above
    :data:`FULL_HASH_LIMIT`: head, middle, tail and the exact length is enough to
    notice a re-separation, and reading 280 MB of stems per song to answer "is
    this current" is not.
    """
    path = Path(path)
    if not path.is_file():
        return ""
    size = path.stat().st_size
    digest = hashlib.sha256(str(size).encode())
    with path.open("rb") as handle:
        if size <= FULL_HASH_LIMIT:
            digest.update(handle.read())
        else:
            for offset in (0, max(0, size // 2 - SAMPLE_BYTES // 2),
                           max(0, size - SAMPLE_BYTES)):
                handle.seek(offset)
                digest.update(handle.read(SAMPLE_BYTES))
    return digest.hexdigest()[:16]


def code_hash(modules) -> str:
    """Hash the source of the named ``rambass`` modules.

    This is what catches reason 3. Reading the installed source rather than
    asking git means it works in a checkout with uncommitted edits — which is the
    state a file gets built in most of the time.
    """
    digest = hashlib.sha256()
    for name in sorted(modules):
        path = Path(__file__).with_name(f"{name}.py")
        digest.update(name.encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def manifest_hash(song, fields) -> str:
    """One hash over the ``song.yaml`` keys a step depends on. Kept for tests."""
    return hashlib.sha256(
        yaml.safe_dump(manifest_hashes(song, fields), sort_keys=True).encode()
    ).hexdigest()[:16]


def manifest_hashes(song, fields) -> dict:
    """One hash **per** ``song.yaml`` key a step depends on.

    Per concern, not per file: a report that declares the drums stale because
    somebody renamed the lyrics file is one people learn to ignore, and then it is
    worse than nothing. And per *field* rather than one hash over all of them,
    because "song.yaml changed in bars, drums, sections, tempo" -- which is what
    the first version said -- is the whole watch list and tells you nothing about
    what you did.
    """
    data = song.to_dict()

    def value_at(spec: str):
        """``tempo`` is a whole block; ``drums/subdivision`` is one key of one.

        Sub-keys exist because watching the whole ``drums`` block made adding one
        entry to ``drums.additions`` declare the transcription, the clean and the
        consolidate all stale — which is false, since Stage 7 edits are applied
        after all three. It took about ten seconds of real use to hit, and a
        report that cries wolf is worse than no report: the first thing anybody
        does with one is stop reading it.
        """
        current = data
        for part in spec.split("/"):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    return {
        spec: hashlib.sha256(
            yaml.safe_dump(value_at(spec), sort_keys=True, allow_unicode=True)
            .encode()).hexdigest()[:16]
        for spec in sorted(fields)
    }


def provenance_path(song) -> Path:
    return Path(song.directory) / PROVENANCE_NAME


def read_stamps(song) -> dict:
    path = provenance_path(song)
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def write_stamps(song, stamps: dict) -> None:
    path = provenance_path(song)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# What produced each derived file here, and what it was made from.\n"
        "# `rambass stale` compares this with the files on disk. Written by the\n"
        "# producing commands -- do not hand-edit; delete a block to force a\n"
        "# rebuild.\n"
    )
    path.write_text(
        header + yaml.safe_dump(stamps, sort_keys=True, allow_unicode=True),
        encoding="utf-8")


def step_for(artifact: str, slug: str | None = None) -> Step | None:
    """The pipeline step producing *artifact*, or None.

    Templated artifacts (``video/{slug}.ass``) only match when *slug* is given,
    because without it there is nothing to resolve the placeholder against.
    """
    for step in PIPELINE:
        candidate = step.artifact_for(slug) if slug else step.artifact
        if candidate == artifact:
            return step
    return None


def stamp(song, artifact, *, step: str, inputs=()) -> None:
    """Record what produced *artifact*. Called by the producing command.

    *step* is a :class:`Step` name, which is where the module list and the
    manifest fields come from — so a command cannot record a different set from
    the one the checker uses, and the two cannot drift apart.
    """
    relative = _relative(song, artifact)
    known = step_for(relative, slug=song.slug)
    modules = known.modules if known else ()
    fields = known.fields if known else ()
    declared = known.inputs_for(song) if known else ()
    stamps = read_stamps(song)
    stamps[relative] = {
        "step": step,
        "version": __version__,
        "self": fingerprint(artifact),
        "code": code_hash(modules),
        "manifest": manifest_hashes(song, fields),
        "scalars": scalar_hashes(song, known.scalars if known else ()),
        # Filtered to what the step declares. `stamp` is called with whatever
        # paths the command happens to have to hand, while the cascade reasons
        # from Step.inputs -- and the two drifted apart within the hour:
        # `drums transcribe` went on passing practice/align.yaml after the step
        # had narrowed to the bar-1 anchor, so the whole drum chain reported
        # stale for a refit that could not move a hit. One source of truth.
        "inputs": {
            name: fingerprint(path)
            for name, path in ((_relative(song, p), p) for p in inputs)
            if known is None or name in declared
        },
    }
    write_stamps(song, stamps)


@dataclass
class Staleness:
    """One artifact's verdict.

    ``state`` is one of:

    * ``ok`` — built by this code, from these inputs, against this manifest;
    * ``missing`` — never built;
    * ``unknown`` — there on disk with no provenance. Built before this existed,
      or by hand. It may be perfectly current and nothing can say so;
    * ``stale`` — an input, the manifest or **the code** moved since it was built;
    * ``edited`` — the file itself differs from what the command wrote. Deliberate
      almost always, and the risk here is the opposite one: re-running the step
      throws the edit away.
    """

    artifact: str
    step: str
    command: str
    state: str
    reasons: list[str] = field(default_factory=list)


def stale_report(song) -> list[Staleness]:
    """Every pipeline artifact for this song, in order, with a verdict.

    Staleness flows downstream, and **only staleness does**. If the raw MIDI is
    stale then so is everything made from it, whatever its own stamp says —
    without that you fix one file, see green below it and ship the old part, which
    is the failure this module exists to prevent. But ``missing`` and ``unknown``
    are not propagated, because an artifact's own stamp already answers those
    better: it records what its inputs were when it was built, so a deleted or
    unstamped input shows up as "has gone" or as nothing changed at all. Cascading
    them as well turns a song where nothing has been built yet into a wall of
    "stale", and a report that is mostly noise gets ignored.
    """
    stamps = read_stamps(song)
    out: list[Staleness] = []
    tainted: set[str] = set()

    for step in PIPELINE:
        if song.drums_origin in step.skip_origins:
            continue
        artifact = step.artifact_for(song.slug)
        path = Path(song.directory) / artifact
        entry = Staleness(
            artifact=artifact, step=step.name,
            command=step.command.format(slug=song.slug), state="ok")

        if not path.is_file():
            entry.state = "missing"
            entry.reasons.append("has never been built")
            out.append(entry)
            continue

        recorded = stamps.get(artifact)
        if not recorded:
            entry.state = "unknown"
            entry.reasons.append(
                "no provenance recorded — built before this existed, or by hand")
        else:
            if recorded.get("self") and fingerprint(path) != recorded["self"]:
                # Worth its own state. A hand edit in Reaper is *intentional* and
                # the danger is losing it, which is the opposite of the danger
                # with a stale file -- so it must not read as "re-run this".
                entry.state = "edited"
                entry.reasons.append(
                    "changed since it was built — probably edited by hand. "
                    "Re-running the step will discard that; put the edits in "
                    "drums.additions / drums.removals so they survive")
            if recorded.get("code") != code_hash(step.modules):
                entry.reasons.append(
                    f"the code that makes it changed "
                    f"({', '.join(step.modules)}.py)")
            was = recorded.get("manifest")
            if step.fields and isinstance(was, dict):
                now = manifest_hashes(song, step.fields)
                moved = sorted(key for key, value in now.items()
                               if was.get(key) != value)
                if moved:
                    entry.reasons.append(
                        f"song.yaml changed in {', '.join(moved)}")
            elif step.fields and was != manifest_hash(song, step.fields):
                # Provenance from before per-field hashing. Cannot say which.
                entry.reasons.append("song.yaml changed")
            was_scalars = recorded.get("scalars") or {}
            if step.scalars:
                now = scalar_hashes(song, step.scalars)
                for name, value in now.items():
                    if was_scalars.get(name) != value:
                        entry.reasons.append(
                            "the bar-1 anchor in practice/align.yaml changed"
                            if name == "align/bar1" else f"{name} changed")
            for name, was in (recorded.get("inputs") or {}).items():
                now = fingerprint(Path(song.directory) / name)
                if not now:
                    entry.reasons.append(f"{name} has gone")
                elif now != was:
                    entry.reasons.append(f"{name} changed")
            if entry.reasons and entry.state != "edited":
                entry.state = "stale"

        upstream = sorted(
            name for name in step.inputs_for(song) if name in tainted)
        if upstream:
            # `edited` outranks the cascade. Both mean "rebuild me" to a naive
            # reading, but they demand opposite handling: a stale file is safe
            # to regenerate, an edited one holds hand work that regeneration
            # destroys. Downgrading edited to stale here is how a one-key
            # rebuild would silently flatten a hand edit whenever anything
            # upstream of it moved.
            if entry.state != "edited":
                entry.state = "stale"
            entry.reasons.append(
                f"{', '.join(upstream)} {'is' if len(upstream) == 1 else 'are'} "
                f"stale, so this is too")

        if entry.state in ("stale", "edited"):
            tainted.add(artifact)
        out.append(entry)
    return out
