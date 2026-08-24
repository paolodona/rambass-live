"""The headless-render spike: drum MIDI through a VST3 instrument.

docs/review-ui.md Phase 4, Gate R2. The A/B review tool compares a *candidate*
wav against the separated stem, and until now that candidate was a hand bounce
through the kit in a DAW -- once per review pass, which is the tax that stops
people doing a second pass. This module renders it straight from
``midi/drums-quantized.mid`` instead.

**The spike's question is answered.** Measured on Paolo's machine on
2026-08-24: ``EZdrummer 3.vst3`` instantiates headlessly through `pedalboard`
in about 12 seconds, with no licence dialog and no window, and a *fresh*
instance already has a kit loaded -- four General MIDI notes rendered at peak
0.70. Neither half of that was safe to assume; the plugin's activation path
blocking a headless instantiation was the whole reason this was scoped as a
spike rather than as a feature.

Two things it deliberately does not do:

* **No remapping.** Whatever note numbers are in the file go to the plugin
  unchanged. ``config/drum-maps/ezdrummer3.yaml`` does not exist yet and
  CLAUDE.md is explicit that invented numbers are worse than none -- so the
  render is as good as the song's declared map and no better, which is the
  honest answer and also the one that makes a wrong map *audible* here.
* **No mixing.** Stereo out of the plugin's master, not the multi-out rig
  docs/drums-rebuild.md wants for the gig. This is a QA render for ears
  checking whether a hit is there, not the Stage 9 base.

The clock is the thing to get right. ``write_drum_midi`` puts tick 0 at
musical bar 1 beat 1, and ``review.clip_spans`` cuts candidate clips with
``Timeline.bar_beat_to_seconds`` -- also from the musical zero. So the render
must contain **no count-in**: adding one looks perfectly fine on its own and
is wrong against every reference clip by exactly ``count_in.bars``.

`pedalboard` is imported only inside this module via ``audio.require_module``,
the same rule ``analyze.py``/``transcribe.py``/``stems.py`` follow.
"""

from __future__ import annotations

import os
from pathlib import Path

from .audio import AudioError, require_module

#: Point this at the VST3 when it is somewhere non-standard -- either the
#: bundle itself or a directory holding it. Same contract as
#: :data:`~rambass.audio.FFMPEG_ENV`, including the part that matters: a
#: set-but-wrong value is an error rather than a fall-through to discovery.
PLUGIN_ENV = "RAMBASS_VST3"

#: Where hosts install VST3s. Searched only when the override is unset.
VST3_DIRS = (
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    / "Common Files" / "VST3",
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    / "Common Files" / "VST3" / "Toontrack",
    Path("/Library/Audio/Plug-Ins/VST3"),
    Path.home() / "Library" / "Audio" / "Plug-Ins" / "VST3",
    Path.home() / ".vst3",
    Path("/usr/lib/vst3"),
)

#: A note_on with no note_off holds a voice. A drum sample is a one-shot so the
#: length barely matters musically, but a five-minute part is a few thousand
#: hits and a polyphony ceiling is a real thing to walk into.
GATE_SECONDS = 0.05

#: How long to keep rendering after the last event. A crash on the last beat
#: decays for seconds; stopping at the last note_on truncates it, and the final
#: section's clip is exactly the one that then sounds wrong.
TAIL_SECONDS = 4.0


def _matches(path: Path, hint: str) -> bool:
    return path.suffix.lower() == ".vst3" and hint.lower() in path.stem.lower()


def locate_plugin(hint: str = "ezdrummer", override: str | None = None) -> Path:
    """Find the instrument's ``.vst3``, override first.

    The order is :func:`~rambass.audio.locate_tool`'s and for the same reason:
    a typo in the variable must say *the variable is wrong*, not "EZdrummer is
    not installed", which sends the reader to check the one thing that was
    fine.
    """
    setting = (override or os.environ.get(PLUGIN_ENV, "")).strip().strip('"')
    if setting:
        base = Path(setting)
        if _matches(base, "") and base.exists():
            return base
        if base.is_dir():
            for candidate in sorted(base.iterdir()):
                if _matches(candidate, hint):
                    return candidate
        raise AudioError(
            f"{PLUGIN_ENV} is set to {setting!r} but there is no {hint} VST3 "
            f"there.\n"
            f"  it should be the .vst3 bundle, or the directory holding it")
    for directory in VST3_DIRS:
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.iterdir()):
            if _matches(candidate, hint):
                return candidate
    raise AudioError(
        f"no {hint} VST3 found in the usual plug-in folders.\n"
        f"  installed elsewhere? set {PLUGIN_ENV} to the .vst3 bundle "
        f"(or its folder)\n"
        f"  searched: " + ", ".join(str(d) for d in VST3_DIRS if d.is_dir()))


def midi_messages(path) -> list:
    """The file's note events, as absolute seconds from **musical bar 1**.

    Passed through unchanged -- no drum map, no transposition -- so a wrong
    map is audible here rather than quietly corrected. ``mido``'s own
    iteration already applies the file's tempo map, and ``write_drum_midi``
    anchors tick 0 at bar 1 beat 1, which is the clock
    :func:`~rambass.review.clip_spans` cuts candidate clips on.
    """
    import mido

    out = []
    held: dict[tuple[int, int], float] = {}
    clock = 0.0
    for message in mido.MidiFile(str(path)):
        clock += message.time
        if message.type not in ("note_on", "note_off"):
            continue
        key = (getattr(message, "channel", 0), message.note)
        if message.type == "note_on" and message.velocity > 0:
            out.append(message.copy(time=clock))
            held[key] = clock
        else:
            # A note_on with velocity 0 is a note_off; normalise so the plugin
            # sees one shape.
            out.append(mido.Message(
                "note_off", note=message.note, velocity=0,
                channel=key[0], time=clock))
            held.pop(key, None)
    for (channel, note), when in held.items():
        # Hand-made MIDI does not always close its notes. write_drum_midi does.
        out.append(mido.Message("note_off", note=note, velocity=0,
                                channel=channel, time=when + GATE_SECONDS))
    out.sort(key=lambda m: (m.time, m.type == "note_on"))
    return out


def render_duration(messages, *, tail: float = TAIL_SECONDS) -> float:
    """Seconds of audio to ask for: the last event, plus the ring."""
    return (max((m.time for m in messages), default=0.0)) + max(tail, 0.0)


def load_instrument(plugin_path, *, preset=None):
    """Instantiate the VST3 headlessly, optionally with a saved preset.

    A fresh EZdrummer 3 instance comes up with its default kit, which is
    enough to hear whether a hit is there but is *not* the song's chosen kit --
    that is a decision docs/drums-rebuild.md keeps for a human, and *preset*
    is how it gets in.
    """
    pedalboard = require_module("pedalboard", "vst")
    plugin_path = Path(plugin_path)
    if not plugin_path.exists():
        raise AudioError(f"no such plug-in: {plugin_path}")
    plugin = pedalboard.load_plugin(str(plugin_path))
    if not getattr(plugin, "is_instrument", True):
        raise AudioError(
            f"{plugin_path.name} is an effect, not an instrument - this "
            f"renders MIDI, so it needs a drum sampler")
    if preset:
        preset = Path(preset)
        if not preset.is_file():
            raise AudioError(f"--preset {preset}: no such file")
        plugin.load_preset(str(preset))
    return plugin


def render_midi(messages, plugin, *, sample_rate: float = 44100.0,
                tail: float = TAIL_SECONDS):
    """Feed *messages* to *plugin* and return the captured audio.

    Shape is ``(channels, samples)`` -- `pedalboard`'s own layout, which is
    what :func:`~rambass.audio.write_wav` wants transposed.
    """
    return plugin(messages, duration=render_duration(messages, tail=tail),
                  sample_rate=sample_rate)


def render_candidate(song, *, plugin_path=None, preset=None, out=None,
                     midi_path=None, sample_rate: float = 44100.0,
                     tail: float = TAIL_SECONDS):
    """Render this song's drum MIDI to its review candidate wav.

    Writes ``qa/candidate.wav`` by default -- deliberately not ``render/``,
    which docs/practice-tracks.md reserves for the gig deliverable, and which
    this must never be mistaken for: a stereo master out of a default kit is a
    QA render, not a base. Returns ``(path, seconds)``.
    """
    import numpy as np

    from .audio import write_wav

    source = Path(midi_path) if midi_path else song.best_drum_midi()
    if not source.exists():
        raise AudioError(
            f"{song.slug}: no drum MIDI to render ({source.name} is missing).\n"
            f"  build it with:  rambass drums clean {song.slug}")
    messages = midi_messages(source)
    if not messages:
        raise AudioError(f"{source.name} has no notes in it")
    plugin = load_instrument(plugin_path or locate_plugin(), preset=preset)
    audio = render_midi(messages, plugin, sample_rate=sample_rate, tail=tail)
    target = Path(out) if out else song.path("qa", "candidate.wav")
    target.parent.mkdir(parents=True, exist_ok=True)
    write_wav(target, np.asarray(audio).T, int(sample_rate))
    return target, render_duration(messages, tail=tail)
