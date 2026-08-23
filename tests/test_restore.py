"""Stage 7 as a command: hand work that survives the next re-transcription.

Paolo: *"There should be stage where we re-introduce all that has been
removed/not added to the midi by design. I had so many iterations now that Im
lost and I dont know how far I am in the process."*

Both halves of that are the same problem. A crash drawn into the Reaper MIDI item
is gone the next time ``drums transcribe`` runs, so the work has to be redone,
and because it is redone from scratch every time there is never a record of how
much of it is finished. Putting the edits in ``song.yaml`` fixes both: they are
bar-anchored, they are in git, and ``rambass drums missing`` can tick off the ones
that are done.

Bar-anchored, of course, not seconds — CLAUDE.md's one invariant. A restore list
in seconds would silently detach from the music the day the tempo changed.
"""

from __future__ import annotations

import pytest

from rambass.midiio import DrumPerformance, Hit
from rambass.restore import Addition, Removal, apply_edits
from rambass.timeline import Timeline


def _perf(hits, bpm=60.0):
    return DrumPerformance(sorted(hits, key=lambda h: (h.time, h.instrument)),
                           Timeline(bpm=bpm))


# ── additions ────────────────────────────────────────────────────────────────


def test_an_addition_lands_on_its_bar_and_beat():
    out, report = apply_edits(
        _perf([Hit("kick", 0.0, 100)]),
        additions=[Addition(bar=3, beat=1.0, instrument="crash", velocity=105)])
    assert report["added"] == 1
    crash = [h for h in out.hits if h.instrument == "crash"]
    assert (crash[0].time, crash[0].velocity) == (8.0, 105)


def test_an_addition_on_a_fractional_beat_works():
    """A 12/8 shuffle has somewhere to be that a whole beat cannot name."""
    out, _ = apply_edits(_perf([]), additions=[
        Addition(bar=2, beat=3.5, instrument="tom_mid", velocity=90)])
    assert out.hits[0].time == pytest.approx(6.5)


def test_an_addition_with_no_velocity_gets_the_instruments_own_median():
    """Better than a constant: a crash added beside the ones the transcriber
    found should sit where they sit, not at some default the author picked."""
    out, report = apply_edits(
        _perf([Hit("crash", 0.0, 100), Hit("crash", 4.0, 110)]),
        additions=[Addition(bar=3, instrument="crash")])
    added = [h for h in out.hits if h.time == 8.0]
    assert added[0].velocity == 105
    assert report["velocity_from_median"] == 1


def test_an_addition_of_an_instrument_the_part_does_not_have_yet():
    out, _ = apply_edits(_perf([Hit("kick", 0.0, 100)]),
                         additions=[Addition(bar=1, instrument="ride")])
    ride = [h for h in out.hits if h.instrument == "ride"]
    assert ride and 1 <= ride[0].velocity <= 127


def test_an_addition_that_is_already_there_is_not_doubled():
    """Running restore twice must not build a flam. Idempotent by construction,
    because the edits are declarative and the MIDI is rebuilt from them."""
    performance = _perf([Hit("crash", 8.0, 105)])
    out, report = apply_edits(
        performance, additions=[Addition(bar=3, instrument="crash", velocity=105)])
    assert report["added"] == 0
    assert report["already_there"] == 1
    assert len(out.hits) == 1


def test_an_unknown_instrument_is_refused_by_name():
    from rambass.project import ProjectError

    with pytest.raises(ProjectError) as caught:
        apply_edits(_perf([]), additions=[Addition(bar=1, instrument="bongo")])
    assert "bongo" in str(caught.value)


# ── removals ─────────────────────────────────────────────────────────────────


def test_a_removal_takes_out_the_hit_at_that_position():
    out, report = apply_edits(
        _perf([Hit("kick", 0.0, 100), Hit("sidestick", 1.0, 45)]),
        removals=[Removal(bar=1, beat=2.0, instrument="sidestick")])
    assert report["removed"] == 1
    assert [h.instrument for h in out.hits] == ["kick"]


def test_a_removal_with_no_instrument_clears_the_position():
    """For the case that motivated it: consolidate stamped a pattern across a
    section and one slot of it is simply wrong."""
    out, report = apply_edits(
        _perf([Hit("kick", 4.0, 100), Hit("hihat_closed", 4.0, 70),
               Hit("snare", 5.0, 110)]),
        removals=[Removal(bar=2, beat=1.0)])
    assert report["removed"] == 2
    assert [h.instrument for h in out.hits] == ["snare"]


def test_a_removal_that_matches_nothing_is_reported_not_silent():
    """A stale removal is a real risk: the hit it named was deleted upstream by
    a later tuning change, and a silent no-op hides that the file drifted."""
    out, report = apply_edits(_perf([Hit("kick", 0.0, 100)]),
                              removals=[Removal(bar=9, beat=1.0)])
    assert report["removed"] == 0
    assert report["stale_removals"] == [(9, 1.0, "")]
    assert len(out.hits) == 1


def test_a_removal_only_reaches_its_own_slot():
    out, report = apply_edits(
        _perf([Hit("snare", 1.0, 110), Hit("snare", 1.4, 110)]),
        removals=[Removal(bar=1, beat=2.0, instrument="snare")])
    assert report["removed"] == 1
    assert out.hits[0].time == pytest.approx(1.4)


# ── both, and the order ──────────────────────────────────────────────────────


def test_removals_run_before_additions():
    """So that replacing a hit is two lines of yaml rather than a puzzle about
    whether the addition survived its own removal."""
    out, report = apply_edits(
        _perf([Hit("hihat_open", 8.0, 122)]),
        additions=[Addition(bar=3, instrument="crash", velocity=105)],
        removals=[Removal(bar=3, beat=1.0, instrument="hihat_open")])
    assert (report["removed"], report["added"]) == (1, 1)
    assert [h.instrument for h in out.hits] == ["crash"]


def test_nothing_declared_is_a_no_op():
    performance = _perf([Hit("kick", 0.0, 100)])
    out, report = apply_edits(performance)
    assert out.hits == performance.hits
    assert report == {"added": 0, "removed": 0, "already_there": 0,
                      "velocity_from_median": 0, "stale_removals": []}


def test_the_output_stays_sorted():
    out, _ = apply_edits(
        _perf([Hit("kick", 8.0, 100)]),
        additions=[Addition(bar=1, instrument="crash"),
                   Addition(bar=2, instrument="tom_mid")])
    assert [h.time for h in out.hits] == sorted(h.time for h in out.hits)


# ── the manifest side ────────────────────────────────────────────────────────


def test_edits_round_trip_through_song_yaml(project):
    from rambass.manifest import Song, load_song, save_song

    item = Song(slug="x", title="X", album="tutti-in-fila", bpm=60.0, bars=8,
                directory=project.songs_dir / "tutti-in-fila" / "09-x",
                drum_additions=[Addition(bar=3, beat=1.0, instrument="crash",
                                         velocity=105, note="into the chorus")],
                drum_removals=[Removal(bar=5, beat=2.0, instrument="sidestick")])
    save_song(item)
    again = load_song(item.directory)
    assert again.drum_additions == item.drum_additions
    assert again.drum_removals == item.drum_removals


def test_a_song_with_no_edits_gains_no_keys(project):
    from rambass.manifest import Song, load_song, save_song

    item = Song(slug="x", title="X", album="tutti-in-fila", bpm=60.0,
                directory=project.songs_dir / "tutti-in-fila" / "09-x")
    save_song(item)
    text = (item.directory / "song.yaml").read_text(encoding="utf-8")
    assert "additions" not in text and "removals" not in text
    assert load_song(item.directory).drum_additions == []


def test_an_edit_at_bar_zero_is_refused():
    from rambass.manifest import Song
    from rambass.project import ProjectError

    with pytest.raises(ProjectError) as caught:
        Song.from_dict({
            "slug": "x", "title": "X", "tempo": {"bpm": 60.0},
            "drums": {"additions": [{"bar": 0, "instrument": "crash"}]},
        })
    assert "bar" in str(caught.value).lower()


def test_an_edit_naming_an_unknown_instrument_is_refused():
    from rambass.manifest import Song
    from rambass.project import ProjectError

    with pytest.raises(ProjectError) as caught:
        Song.from_dict({
            "slug": "x", "title": "X", "tempo": {"bpm": 60.0},
            "drums": {"additions": [{"bar": 1, "instrument": "bongo"}]},
        })
    assert "bongo" in str(caught.value)
