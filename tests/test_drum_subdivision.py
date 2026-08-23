"""The quantise grid: how it is chosen, and that it reaches the whole kit.

Manlio is a shuffle -- its hi-hat sits on 0, 1/3 and 2/3 of the beat, and 85%
of the kit lands within 60 ms of a triplet-8th against 5% on the 16th-only
positions. Quantising it to 16ths drags every triplet 83.3 ms onto the wrong
note, and the tolerance gate is 87.5 ms, so which way a hit goes is decided by
under 5 ms of human variation. These tests pin down the plumbing that lets a
song say "triplets" and have it actually happen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rambass.cli import main
from rambass.manifest import load_song, save_song
from rambass.midiio import DrumPerformance, Hit, read_drum_midi, write_drum_midi
from rambass.quantize import (
    CYMBALS,
    DEFAULT_SUBDIVISIONS,
    QuantizeSettings,
    quantize,
)
from rambass.timeline import Timeline

TRIPLET = 1.0 / 3.0


# ── the trap in QuantizeSettings, stated so it cannot be "cleaned up" ────────


def test_the_default_table_is_absolute_and_wins_over_subdivision():
    """DEFAULT_SUBDIVISIONS lists everything but the toms, so it beats
    `subdivision` for the whole kit. Callers must pass per_instrument."""
    settings = QuantizeSettings(subdivision=3)
    assert settings.subdivision_for("kick") == 4
    assert settings.subdivision_for("snare") == 4
    assert settings.subdivision_for("hihat_closed") == 4
    assert settings.subdivision_for("crash") == 2
    # The toms are the only thing `subdivision` alone can move.
    assert settings.subdivision_for("tom_mid") == 3


def test_per_instrument_is_how_an_explicit_grid_reaches_the_kit():
    settings = QuantizeSettings(
        subdivision=3, per_instrument={k: 3 for k in DEFAULT_SUBDIVISIONS}
    )
    for instrument in ("kick", "snare", "hihat_closed", "crash"):
        assert settings.subdivision_for(instrument) == 3


def test_cymbals_is_the_crash_family_and_nothing_else():
    assert CYMBALS == {"crash", "crash_2", "china", "splash"}
    assert CYMBALS <= set(DEFAULT_SUBDIVISIONS)


def test_a_triplet_kick_snaps_to_the_triplet_not_the_sixteenth():
    """Manlio bar 1: a kick played at beat 2.709. The 16th at 2.750 is 40.6 ms
    away and the triplet at 2.667 is 42.7 ms away -- the wrong answer wins by
    2 ms on a 16th grid, which is the whole bug."""
    timeline = Timeline(bpm=60.0)
    performance = DrumPerformance([Hit("kick", 1.7094, 64)], timeline)

    on_sixteenths, _ = quantize(performance, QuantizeSettings(subdivision=4))
    assert on_sixteenths.hits[0].time == pytest.approx(1.75, abs=1e-6)

    on_triplets, _ = quantize(
        performance, QuantizeSettings(subdivision=3, per_instrument={"kick": 3})
    )
    assert on_triplets.hits[0].time == pytest.approx(2 * TRIPLET + 1.0, abs=1e-6)


def test_the_tolerance_gate_and_the_triplet_error_nearly_coincide():
    """Why a 16th grid produces incoherence rather than a uniform mistake."""
    step_seconds = 60.0 / 60.0 / 4          # 16ths at 60 BPM
    gate = QuantizeSettings().tolerance_steps * step_seconds
    triplet_error = abs(TRIPLET - 0.25)
    assert gate == pytest.approx(0.0875)
    assert triplet_error == pytest.approx(0.08333, abs=1e-4)
    assert 0 < gate - triplet_error < 0.005


# ── the manifest carries the grid ────────────────────────────────────────────


def test_a_song_defaults_to_sixteenths(song):
    assert song.drum_subdivision == 4
    assert song.drum_cymbal_subdivision == 2


def test_the_grid_round_trips_through_song_yaml(song, project):
    song.drum_subdivision = 3
    song.drum_cymbal_subdivision = 3
    directory = save_song(song, song.dir).parent
    reloaded = load_song(directory)
    assert reloaded.drum_subdivision == 3
    assert reloaded.drum_cymbal_subdivision == 3


def test_the_grid_is_written_under_drums_not_at_the_top_level(song):
    song.drum_subdivision = 3
    assert song.to_dict()["drums"]["subdivision"] == 3


def test_an_absurd_subdivision_is_a_problem(song):
    song.drum_subdivision = 0
    assert any("subdivision" in p for p in song.problems())
    song.drum_subdivision = 7
    assert any("subdivision" in p for p in song.problems())


def test_a_triplet_song_with_cymbals_on_eighths_is_advised_not_rejected(song):
    """3 and 2 do not share a grid, so the crashes would be dragged 83 ms.

    Advice and not a problem: `problems()` is fatal through `validate()`, and
    this combination is a thing you probably did not mean, not an unloadable
    file. Pinned because putting it back in `problems()` makes song.yaml
    refuse to load, which is how this was found.
    """
    song.drum_subdivision = 3
    song.drum_cymbal_subdivision = 2
    assert "cymbal_subdivision" in song.grid_advice()
    assert not [p for p in song.problems() if "cymbal" in p]
    song.validate()          # must not raise


def test_matching_grids_have_nothing_to_advise(song):
    for kit, cymbal in ((4, 2), (3, 3), (4, 4), (6, 3)):
        song.drum_subdivision = kit
        song.drum_cymbal_subdivision = cymbal
        assert song.grid_advice() == ""


# ── end to end through the CLI ───────────────────────────────────────────────


@pytest.fixture
def cwd(project, monkeypatch):
    monkeypatch.chdir(project.root)
    return project


def _write_raw(song, hits) -> Path:
    target = song.drum_midi_path("raw")
    write_drum_midi(target, DrumPerformance(hits, song.timeline()))
    return target


def test_defaults_reproduce_the_default_subdivision_table(cwd, song):
    """The regression this replaces was verified once by md5 and never again."""
    song.bpm = 60.0
    save_song(song, song.dir)
    reloaded = load_song(song.dir)
    hits = [Hit("kick", 1.7094, 64), Hit("crash", 3.6833, 80)]
    _write_raw(reloaded, hits)

    assert main(["drums", "clean", reloaded.slug, "--output", "viaflags"]) == 0
    through_cli = read_drum_midi(reloaded.drum_midi_path("viaflags"))

    direct, _ = quantize(
        DrumPerformance(hits, reloaded.timeline()), QuantizeSettings(subdivision=4)
    )
    # Compared to the tick, not to the float: a hit the tolerance gate left
    # off-grid round-trips through 960 ticks per beat, which is 1.04 ms at
    # 60 BPM. Anything tighter fails on the file format, not on the logic.
    got = through_cli.sorted_hits()
    want = direct.sorted_hits()
    assert [h.instrument for h in got] == [h.instrument for h in want]
    for mine, theirs in zip(got, want, strict=True):
        assert mine.time == pytest.approx(theirs.time, abs=1.1e-3)
        assert mine.velocity == theirs.velocity


def test_the_subdivision_flag_moves_the_kick(cwd, song):
    song.bpm = 60.0
    save_song(song, song.dir)
    reloaded = load_song(song.dir)
    _write_raw(reloaded, [Hit("kick", 1.7094, 64)])

    assert main(["drums", "clean", reloaded.slug, "--subdivision", "3",
                 "--cymbal-subdivision", "3", "--output", "trip"]) == 0
    got = read_drum_midi(reloaded.drum_midi_path("trip"))
    assert got.sorted_hits()[0].time == pytest.approx(1.0 + 2 * TRIPLET, abs=1e-3)


def test_the_manifest_grid_is_used_when_no_flag_is_given(cwd, song):
    song.bpm = 60.0
    song.drum_subdivision = 3
    song.drum_cymbal_subdivision = 3
    save_song(song, song.dir)
    reloaded = load_song(song.dir)
    _write_raw(reloaded, [Hit("kick", 1.7094, 64)])

    assert main(["drums", "clean", reloaded.slug, "--output", "frommanifest"]) == 0
    got = read_drum_midi(reloaded.drum_midi_path("frommanifest"))
    assert got.sorted_hits()[0].time == pytest.approx(1.0 + 2 * TRIPLET, abs=1e-3)


def test_an_explicit_flag_beats_the_manifest(cwd, song):
    song.bpm = 60.0
    song.drum_subdivision = 3
    save_song(song, song.dir)
    reloaded = load_song(song.dir)
    _write_raw(reloaded, [Hit("kick", 1.7094, 64)])

    assert main(["drums", "clean", reloaded.slug, "--subdivision", "4",
                 "--output", "forced"]) == 0
    got = read_drum_midi(reloaded.drum_midi_path("forced"))
    assert got.sorted_hits()[0].time == pytest.approx(1.75, abs=1e-3)
