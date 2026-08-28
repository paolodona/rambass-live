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
                      "velocity_from_median": 0, "stale_removals": [],
                      "contradicted": []}


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


# ── proposing additions, so the checklist is not 35 lines of typing ──────────
#
# `drums missing` reports 35 things on Manlio and 20 of them are crashes with a
# bar, a beat and a velocity already attached. Making somebody retype that into
# song.yaml is the kind of friction that gets a good tool abandoned. So it can
# propose them, and the review happens where review belongs -- in the diff.


def test_confident_candidates_become_additions():
    from rambass.restore import MissingHit, propose_additions

    items = [
        MissingHit(bar=32, beat=3.0, instrument="crash", velocity=111,
                   section="chorus-1", reason="section start: a v111 open hi-hat"),
        MissingHit(bar=14, beat=4.0, instrument="tom_mid", velocity=79,
                   section="verse-1", reason="removed by consolidate"),
    ]
    proposed, skipped = propose_additions(items)
    assert [(a.bar, a.beat, a.instrument, a.velocity) for a in proposed] == [
        (32, 3.0, "crash", 111)]
    assert skipped == 1


def test_a_bare_position_is_not_proposed():
    """velocity 0 means nothing was measured there. A crash the drummer chose not
    to play is exactly what a section start with no cymbal looks like, so that
    one stays a listening decision."""
    from rambass.restore import MissingHit, propose_additions

    items = [MissingHit(bar=18, beat=1.0, instrument="crash", velocity=0,
                        section="break-1", reason="no cymbal here at all")]
    proposed, skipped = propose_additions(items)
    assert proposed == [] and skipped == 1


def test_a_fill_is_not_proposed():
    """A fill is a phrase, not a hit. Proposing its eight toms one at a time
    would put back exactly the incoherent bar Stage 6 removed."""
    from rambass.restore import MissingHit, propose_additions

    items = [MissingHit(bar=75, beat=1.0 + i / 3.0, instrument="tom_mid",
                        velocity=90, section="theme-finale",
                        reason="removed by consolidate")
             for i in range(7)]
    proposed, skipped = propose_additions(items)
    assert proposed == []
    assert skipped == 7


def test_the_note_says_where_it_came_from():
    from rambass.restore import MissingHit, propose_additions

    items = [MissingHit(bar=32, beat=3.0, instrument="crash", velocity=111,
                        section="chorus-1", reason="section start: a v111 open hi-hat")]
    proposed, _ = propose_additions(items)
    assert "chorus-1" in proposed[0].note
    assert "proposed" in proposed[0].note


def test_something_already_in_the_manifest_is_not_proposed_twice():
    from rambass.restore import Addition, MissingHit, propose_additions

    items = [MissingHit(bar=32, beat=3.0, instrument="crash", velocity=111,
                        section="chorus-1", reason="section start")]
    proposed, skipped = propose_additions(
        items, existing=[Addition(bar=32, beat=3.0, instrument="crash")])
    assert proposed == [] and skipped == 1


# ── `drums restore` applies a section's declared voicing ─────────────────────
#
# Stage 7 and not `drums clean`, unlike the declared *backbeat*. The backbeat has
# to be voiced before quantise so the right stroke gets snapped and before
# consolidate so the section's vote sees a consistent one. A re-voicing moves no
# hit and changes no vote -- it renames -- so forcing a re-quantise and a
# re-consolidate for it would spend minutes to reach the same positions. It is a
# hand decision about a section, which is what this stage is.


def _cwd(project, monkeypatch):
    monkeypatch.chdir(project.root)
    return project


def test_drums_restore_applies_a_section_voicing(project, monkeypatch, capsys):
    from rambass.cli import main
    from rambass.manifest import load_song
    from rambass.midiio import DrumPerformance, Hit, read_drum_midi, write_drum_midi

    monkeypatch.chdir(project.root)
    song = load_song(_seeded(project))
    timeline = song.timeline()
    write_drum_midi(
        song.drum_midi_path("consolidated"),
        DrumPerformance([
            Hit("hihat_open", timeline.bar_beat_to_seconds(3, 1.0), 111),
            Hit("hihat_open", timeline.bar_beat_to_seconds(3, 3.0), 82),
            Hit("hihat_open", timeline.bar_beat_to_seconds(1, 1.0), 90),
            Hit("kick", timeline.bar_beat_to_seconds(3, 2.0), 100),
        ], timeline))

    assert main(["drums", "restore", song.slug]) == 0
    out = capsys.readouterr().out
    assert "finale" in out and "ride_bell" in out, out

    again = read_drum_midi(song.drum_midi_path("restored"),
                           _map(project, song))
    at = {round(hit.time, 4): hit.instrument for hit in again.hits}
    assert at[round(timeline.bar_beat_to_seconds(3, 1.0), 4)] == "ride_bell"
    assert at[round(timeline.bar_beat_to_seconds(3, 3.0), 4)] == "ride_bell"
    # Before the section, untouched.
    assert at[round(timeline.bar_beat_to_seconds(1, 1.0), 4)] == "hihat_open"
    # The velocities of the figure survive the rename.
    assert sorted(hit.velocity for hit in again.hits
                  if hit.instrument == "ride_bell") == [82, 111]


def test_a_song_with_only_a_voicing_is_not_skipped(project, monkeypatch, capsys):
    """The guard used to be "no additions and no removals, nothing to do". A
    section voicing is a third kind of declared edit and has to keep the command
    from bailing before it applies one."""
    from rambass.cli import main
    from rambass.manifest import load_song
    from rambass.midiio import DrumPerformance, Hit, write_drum_midi

    monkeypatch.chdir(project.root)
    song = load_song(_seeded(project))
    assert not song.drum_additions and not song.drum_removals
    timeline = song.timeline()
    write_drum_midi(
        song.drum_midi_path("consolidated"),
        DrumPerformance(
            [Hit("hihat_open", timeline.bar_beat_to_seconds(3, 1.0), 111)],
            timeline))

    assert main(["drums", "restore", song.slug]) == 0
    assert song.drum_midi_path("restored").exists(), (
        "restore bailed on a song whose only Stage 7 edit is a voicing")
    assert "no drums.additions" not in capsys.readouterr().out


def test_a_declared_addition_wins_over_the_sections_voicing(project, monkeypatch):
    """The escape hatch, and the repo's usual precedence: a declaration is the
    last word. `drums.additions` is applied *after* the re-voicing, so a hi-hat
    deliberately declared inside a re-voiced section stays a hi-hat."""
    from rambass.cli import main
    from rambass.manifest import Addition, load_song, save_song
    from rambass.midiio import DrumPerformance, Hit, read_drum_midi, write_drum_midi

    monkeypatch.chdir(project.root)
    song = load_song(_seeded(project))
    song.drum_additions.append(
        Addition(bar=3, beat=4.0, instrument="hihat_open", velocity=70))
    save_song(song, song.dir)
    song = load_song(song.dir)
    timeline = song.timeline()
    write_drum_midi(
        song.drum_midi_path("consolidated"),
        DrumPerformance(
            [Hit("hihat_open", timeline.bar_beat_to_seconds(3, 1.0), 111)],
            timeline))

    assert main(["drums", "restore", song.slug]) == 0
    again = read_drum_midi(song.drum_midi_path("restored"), _map(project, song))
    at = {round(hit.time, 4): hit.instrument for hit in again.hits}
    assert at[round(timeline.bar_beat_to_seconds(3, 1.0), 4)] == "ride_bell"
    assert at[round(timeline.bar_beat_to_seconds(3, 4.0), 4)] == "hihat_open"


def test_editing_a_sections_voicing_makes_the_restored_midi_stale(project):
    """`drums restore` reads the section list now, so it has to watch it. Before
    this the restore step's provenance fields were tempo, bars, drums/map,
    drums/additions and drums/removals -- a voicing change moved none of them,
    so `rambass stale` reported ok and the console said the candidate was
    current. Exactly the hole `midi_behind_manifest` is about."""
    from rambass.manifest import load_song, save_song
    from rambass.provenance import stale_report, stamp

    directory = _seeded(project)
    song = load_song(directory)
    consolidated = song.drum_midi_path("consolidated")
    consolidated.parent.mkdir(parents=True, exist_ok=True)
    consolidated.write_bytes(b"MThd-consolidated")
    restored = song.drum_midi_path("restored")
    restored.write_bytes(b"MThd-restored")
    stamp(song, restored, step="drums restore", inputs=[consolidated])

    def verdict(one):
        return next(e for e in stale_report(one)
                    if e.artifact.endswith("drums-restored.mid"))

    assert verdict(load_song(directory)).state == "ok"

    song.sections[1].voicing = {"hihat_open": "ride"}
    save_song(song, directory)
    entry = verdict(load_song(directory))
    assert entry.state == "stale"
    assert any("sections" in reason for reason in entry.reasons), entry.reasons


def _seeded(project):
    """A two-section song whose second section re-voices its open hats."""
    from rambass.manifest import STAGES, Section, Song, save_song

    directory = project.songs_dir / "tutti-in-fila" / "09-manlio"
    save_song(Song(
        slug="manlio", title="Manlio", album="tutti-in-fila", track=9,
        directory=directory, bpm=60.0, count_in_bars=2, bars=8,
        drums_origin="extracted", drum_map="general-midi",
        sections=[Section("intro", 1),
                  Section("finale", 3, voicing={"hihat_open": "ride_bell"})],
        status={stage: "todo" for stage in STAGES}), directory)
    return directory


def _map(project, song):
    from rambass.drummap import load_drum_map

    return load_drum_map(song.drum_map, project)
