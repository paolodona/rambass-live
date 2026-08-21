"""Rambass Live — backing-track tooling for Ramba S.S..

The package is deliberately layered so that the cheap, deterministic parts
(manifests, MIDI, Reaper build scripts, subtitles, setlists) have *no* heavy
dependencies, and only the audio-DSP commands need librosa/demucs.
"""

__version__ = "0.1.0"
