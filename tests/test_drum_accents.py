"""``drums.accents`` / ``drums.downbeat_boost``: a declared kick/snare accent
survives a re-run, same argument as ``drums.backbeat_velocity``.

``shape_velocities`` already says the thing this fixes: "accenting musically
usually sounds better than trusting the analysis." But until now that accent
only ever came from ``--accent-kick``/``--accent-snare``/``--downbeat-boost``,
which is whoever last typed the command -- the next plain ``drums clean``
silently drops back to the measured contour. Declared in ``song.yaml`` it is
in git and is reapplied every time.
"""

from __future__ import annotations

import pytest

from rambass.cli import main
from rambass.manifest import load_song, save_song
from rambass.midiio import DrumPerformance, Hit, read_drum_midi, write_drum_midi


@pytest.fixture
def cwd(project, monkeypatch):
    monkeypatch.chdir(project.root)
    return project


def _write_raw(song, hits):
    target = song.drum_midi_path("raw")
    write_drum_midi(target, DrumPerformance(hits, song.timeline()))
    return target


def test_the_manifest_accent_is_used_when_no_flag_is_given(cwd, song):
    song.bpm = 60.0
    song.drum_accents = {"kick": 100, "snare": 90}
    song.drum_downbeat_boost = 10
    save_song(song, song.dir)
    reloaded = load_song(song.dir)
    _write_raw(reloaded, [Hit("kick", 0.0, 40), Hit("snare", 1.0, 40)])

    assert main(["drums", "clean", reloaded.slug, "--output", "frommanifest"]) == 0
    got = {h.instrument: h.velocity for h in
           read_drum_midi(reloaded.drum_midi_path("frommanifest")).hits}
    assert got["kick"] == 110    # 100 base + 10 downbeat boost, bar 1 beat 1
    assert got["snare"] == 90    # not on a downbeat, no boost


def test_an_explicit_flag_beats_the_manifest(cwd, song):
    song.bpm = 60.0
    song.drum_accents = {"kick": 100}
    save_song(song, song.dir)
    reloaded = load_song(song.dir)
    _write_raw(reloaded, [Hit("kick", 0.0, 40)])

    assert main(["drums", "clean", reloaded.slug, "--accent-kick", "70",
                 "--output", "viaflag"]) == 0
    got = read_drum_midi(reloaded.drum_midi_path("viaflag"))
    assert got.hits[0].velocity == 70


def test_no_declared_accent_means_trust_the_measured_contour(cwd, song):
    song.bpm = 60.0
    save_song(song, song.dir)
    reloaded = load_song(song.dir)
    _write_raw(reloaded, [Hit("kick", 0.0, 40)])

    assert main(["drums", "clean", reloaded.slug, "--output", "plain"]) == 0
    got = read_drum_midi(reloaded.drum_midi_path("plain"))
    assert got.hits[0].velocity == 40    # the measured velocity, untouched
