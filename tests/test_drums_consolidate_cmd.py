"""`rambass drums consolidate` — Stage 6 as its own opt-in command.

Separate from `drums clean` and writing its own variant, because of what its
docstring promises: "This deliberately removes the fills." On Manlio that is the
three breaks, the closing fill and every tom run, all to be put back by hand at
Stage 7. That is a decision, not a side effect of a command you already run, and
keeping the unconsolidated part on disk is what makes it A/B-able.
"""

from __future__ import annotations

import pytest

from rambass.cli import main
from rambass.manifest import Section, load_song, save_song
from rambass.midiio import DrumPerformance, Hit, read_drum_midi, write_drum_midi


@pytest.fixture
def cwd(project, monkeypatch):
    monkeypatch.chdir(project.root)
    return project


def _backbeat(timeline, first_bar, bars):
    out = []
    for b in range(first_bar, first_bar + bars):
        for beat, name in ((1.0, "kick"), (2.0, "snare"), (3.0, "kick"), (4.0, "snare")):
            out.append(Hit(name, timeline.bar_beat_to_seconds(b, beat), 100))
    return out


def _seed(song, hits, variant="quantized"):
    write_drum_midi(song.drum_midi_path(variant),
                    DrumPerformance(hits, song.timeline()))


def test_it_writes_its_own_variant_and_leaves_the_input_alone(cwd, song):
    song.bars = 32
    save_song(song, song.dir)
    reloaded = load_song(song.dir)
    hits = _backbeat(reloaded.timeline(), 1, 32)
    hits.append(Hit("crash", reloaded.timeline().bar_beat_to_seconds(3, 1.0), 90))
    _seed(reloaded, hits)
    before = reloaded.drum_midi_path("quantized").read_bytes()

    assert main(["drums", "consolidate", reloaded.slug]) == 0
    assert reloaded.drum_midi_path("quantized").read_bytes() == before
    out = reloaded.drum_midi_path("consolidated")
    assert out.is_file()
    assert not [h for h in read_drum_midi(out).hits if h.instrument == "crash"], \
        "a crash in one bar of thirty-two is exactly what Stage 6 removes"


def test_it_reports_each_section(cwd, song, capsys):
    song.bars = 32
    save_song(song, song.dir)
    reloaded = load_song(song.dir)
    _seed(reloaded, _backbeat(reloaded.timeline(), 1, 32))
    main(["drums", "consolidate", reloaded.slug])
    out = capsys.readouterr().out
    for name in ("intro", "verse", "chorus"):
        assert name in out
    assert "removes the fills" in out, "the warning has to be on screen"


def test_a_dry_run_writes_nothing(cwd, song, capsys):
    song.bars = 32
    save_song(song, song.dir)
    reloaded = load_song(song.dir)
    _seed(reloaded, _backbeat(reloaded.timeline(), 1, 32))
    assert main(["drums", "consolidate", reloaded.slug, "--dry-run"]) == 0
    assert not reloaded.drum_midi_path("consolidated").exists()
    assert "intro" in capsys.readouterr().out


def test_it_uses_the_manifest_sections_including_a_mid_bar_one(cwd, song):
    """The section list is the song's, beats and all -- not a rounded copy."""
    song.bars = 32
    song.sections = [Section("a", 1), Section("b", 9, beat=3.0)]
    save_song(song, song.dir)
    reloaded = load_song(song.dir)
    timeline = reloaded.timeline()
    hits = _backbeat(timeline, 1, 32)
    hits.append(Hit("crash", timeline.bar_beat_to_seconds(9, 3.0), 90))
    _seed(reloaded, hits)
    assert main(["drums", "consolidate", reloaded.slug]) == 0
    got = read_drum_midi(reloaded.drum_midi_path("consolidated"))
    assert not [h for h in got.hits if h.instrument == "crash"]


def test_sections_sharing_a_name_are_pooled(cwd, song, capsys):
    """Paolo's rule: the same name is the same part, so they vote together."""
    song.bars = 32
    song.sections = [Section("chorus", 1), Section("verse", 9),
                     Section("chorus", 17), Section("verse", 25)]
    save_song(song, song.dir)
    reloaded = load_song(song.dir)
    _seed(reloaded, _backbeat(reloaded.timeline(), 1, 32))
    main(["drums", "consolidate", reloaded.slug])
    out = capsys.readouterr().out
    assert "chorus" in out
    # 2 spans of 8 bars each, pooled, is 16 one-bar repetitions
    assert "16" in out


def test_it_refuses_when_the_song_has_no_sections(cwd, song, capsys):
    song.bars = 32
    song.sections = []
    save_song(song, song.dir)
    reloaded = load_song(song.dir)
    _seed(reloaded, _backbeat(reloaded.timeline(), 1, 32))
    assert main(["drums", "consolidate", reloaded.slug]) == 0
    assert not reloaded.drum_midi_path("consolidated").exists()
    assert "no sections" in capsys.readouterr().out


def test_a_missing_input_is_a_clear_message_not_a_traceback(cwd, song, capsys):
    song.bars = 32
    save_song(song, song.dir)
    assert main(["drums", "consolidate", load_song(song.dir).slug]) == 0
    assert "transcribe" in capsys.readouterr().out


def test_the_threshold_is_adjustable(cwd, song):
    """A hit in half the bars survives at 0.5 and not at 0.55.

    Note the tom is in the *first* half rather than on alternate bars: an
    every-other-bar tom is a genuine two-bar pattern, `consolidate` picks the
    two-bar unit and finds it in 16 of 16 repetitions, and it survives any
    threshold. Which is correct, and was this test's first mistake.
    """
    song.bars = 32
    song.sections = [Section("verse", 1)]
    save_song(song, song.dir)
    reloaded = load_song(song.dir)
    timeline = reloaded.timeline()
    hits = _backbeat(timeline, 1, 32)
    for b in range(1, 17):
        hits.append(Hit("tom_mid", timeline.bar_beat_to_seconds(b, 4.0) + 0.25, 90))
    _seed(reloaded, hits)

    main(["drums", "consolidate", reloaded.slug, "--output", "strict"])
    strict = read_drum_midi(reloaded.drum_midi_path("strict"))
    main(["drums", "consolidate", reloaded.slug, "--output", "loose",
          "--threshold", "0.5"])
    loose = read_drum_midi(reloaded.drum_midi_path("loose"))

    assert not [h for h in strict.hits if h.instrument == "tom_mid"]
    assert [h for h in loose.hits if h.instrument == "tom_mid"]
