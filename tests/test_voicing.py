"""Declared articulation: a section says how its backbeat is played.

Paolo, after hearing verse-2: *"verse-2 also uses cross-stick/side-stick
(examples are 22.4, 23.2, 23.4, 24.2 etc etc). some sound different than others
but they are all the same concept: side stick rather than standard snares."*

The acoustic detector gets verse-1 and verse-3 and misses most of verse-2, and
the measurement says why. Across all 941 hi-hat and cymbal stem detections, the
hi-hat stem's 5-11 kHz share is 0.17 at a verse backbeat against 0.85-0.96
everywhere else — except in verse-2, where the clicks read 0.84-1.00, which is
a hi-hat's own spectrum. Reaper 23.2, one of Paolo's examples, is 0.84. He can
hear a cross-stick in context; the spectrum there cannot, and no threshold
recovers it without eating the real hi-hat part.

So this is **declared, not detected**, and that is not a per-song threshold
override of the kind CLAUDE.md warns about. It is arrangement structure — the
same category as ``sections`` themselves or ``drums.subdivision: 3``: settled by
ear once, written in ``song.yaml``, and never re-derived from audio.
"""

from __future__ import annotations

import pytest

from rambass.manifest import Section
from rambass.midiio import DrumPerformance, Hit
from rambass.restore import voice_backbeats
from rambass.timeline import Timeline


def _verse(bars=4, *, start_bar=1, start_beat=1.0, backbeat="hihat_open",
           velocity=85):
    """A shuffle verse: kick on 1 and 3, triplet hats, something on 2 and 4."""
    hits: list[Hit] = []
    for bar in range(start_bar - 1, start_bar - 1 + bars):
        base = bar * 4.0
        hits.append(Hit("kick", base + 0.0, 100))
        hits.append(Hit("kick", base + 2.0, 100))
        for beat in range(4):
            for third in range(3):
                at = base + beat + third / 3.0
                if third == 0 and beat in (1, 3):
                    hits.append(Hit(backbeat, at, velocity))
                else:
                    hits.append(Hit("hihat_closed", at, 70))
    return DrumPerformance(sorted(hits, key=lambda h: (h.time, h.instrument)),
                           Timeline(bpm=60.0))


def _sections(**kwargs):
    return [Section(name="verse-2", bar=1, **kwargs)]


# ── the declaration does what Paolo said ─────────────────────────────────────


def test_a_declared_backbeat_renames_whatever_is_on_two_and_four():
    """verse-2's clicks arrive as loud open hi-hats, because that is the stem
    the click's energy landed in. The declaration does not care which."""
    performance = _verse(4)
    out, report = voice_backbeats(
        performance, _sections(backbeat="sidestick"), end_bar=5)
    assert report["renamed"] == 8                       # 4 bars x 2 backbeats
    sidesticks = [h for h in out.hits if h.instrument == "sidestick"]
    assert len(sidesticks) == 8
    assert sorted(round(h.time % 4.0, 3) for h in sidesticks) == sorted([1.0, 3.0] * 4)


def test_a_side_stick_already_named_by_the_detector_is_left_where_it_is():
    """verse-1 and verse-3 the detector already gets. Declaring it must be a
    no-op there, not a second pass that moves anything."""
    performance = _verse(4, backbeat="sidestick", velocity=47)
    out, report = voice_backbeats(
        performance, _sections(backbeat="sidestick"), end_bar=5)
    assert report["renamed"] == 0
    assert out.hits == performance.hits


def test_the_hat_under_the_click_goes_with_it():
    """Same reasoning as drop_hats_on_sidesticks: what the detector reported at
    that instant is the click, and the alternative is not a hole but a loud
    accented hat on every backbeat of the section."""
    performance = _verse(2)
    extra = list(performance.hits) + [Hit("hihat_closed", 1.004, 122),
                                      Hit("hihat_closed", 3.004, 120)]
    performance = DrumPerformance(sorted(extra, key=lambda h: (h.time, h.instrument)),
                                  performance.timeline)
    out, report = voice_backbeats(
        performance, _sections(backbeat="sidestick"), end_bar=3)
    assert report["dropped"] == 2
    on_one = [h for h in out.hits if abs(h.time - 1.0) < 0.05]
    assert [h.instrument for h in on_one] == ["sidestick"]


def test_the_loudest_hand_hit_wins_when_several_coincide():
    performance = DrumPerformance(
        [Hit("kick", 1.0, 100), Hit("hihat_closed", 1.002, 70),
         Hit("hihat_open", 1.004, 95), Hit("crash", 1.006, 60)],
        Timeline(bpm=60.0))
    out, report = voice_backbeats(
        performance, _sections(backbeat="sidestick"), end_bar=2)
    assert report["renamed"] == 1
    named = [h for h in out.hits if h.instrument == "sidestick"]
    assert named[0].velocity == 95, "the open hat was the loudest hand hit"


def test_a_kick_on_the_backbeat_is_a_foot_and_is_never_renamed():
    performance = DrumPerformance([Hit("kick", 1.0, 110)], Timeline(bpm=60.0))
    out, report = voice_backbeats(
        performance, _sections(backbeat="sidestick"), end_bar=2)
    assert report["renamed"] == 0
    assert out.hits[0].instrument == "kick"


def test_a_slot_with_nothing_in_it_is_left_for_the_vote_to_fill():
    """consolidate stamps the section's agreed pattern across it, so a missing
    backbeat comes back from the bars that have one. Inventing a hit here would
    put it in before there is any evidence about where in the beat it sits."""
    performance = DrumPerformance(
        [Hit("kick", 0.0, 100), Hit("hihat_open", 1.0, 85)], Timeline(bpm=60.0))
    out, report = voice_backbeats(
        performance, _sections(backbeat="sidestick"), end_bar=2)
    assert report["renamed"] == 1
    assert report["empty_slots"] == 1                   # beat 4 had nothing
    assert len(out.hits) == 2


# ── it stays inside the section ──────────────────────────────────────────────


def test_nothing_outside_the_section_is_touched():
    performance = _verse(8)
    out, _ = voice_backbeats(
        performance, [Section(name="verse-2", bar=3, backbeat="sidestick")],
        end_bar=5)
    named = sorted(h.time for h in out.hits if h.instrument == "sidestick")
    assert named == [9.0, 11.0, 13.0, 15.0]             # bars 3-4 only


def test_a_section_starting_mid_bar_starts_mid_bar():
    """Manlio's verse-2 runs from bar 20 beat 3 to bar 28 beat 3, so its first
    repetition is half a bar and beat 2 of bar 20 is NOT in it."""
    performance = _verse(4)
    out, _ = voice_backbeats(
        performance,
        [Section(name="verse-2", bar=1, beat=3.0, backbeat="sidestick")],
        end_bar=3)
    named = sorted(h.time for h in out.hits if h.instrument == "sidestick")
    assert named == [3.0, 5.0, 7.0], "beat 2 of bar 1 is before the section"


def test_a_section_with_no_declaration_is_a_no_op():
    performance = _verse(4)
    out, report = voice_backbeats(performance, _sections(), end_bar=5)
    assert report["renamed"] == 0
    assert out.hits == performance.hits


def test_no_sections_at_all_is_not_a_crash():
    performance = _verse(2)
    out, report = voice_backbeats(performance, [], end_bar=3)
    assert out.hits == performance.hits and report["renamed"] == 0


# ── the manifest round-trip ──────────────────────────────────────────────────


def test_a_section_carries_its_backbeat_through_yaml(project):
    from rambass.manifest import Song, load_song, save_song

    item = Song(slug="x", title="X", album="tutti-in-fila", bpm=60.0, bars=8,
                directory=project.songs_dir / "tutti-in-fila" / "09-x",
                sections=[Section(name="verse-2", bar=3, beat=3.0,
                                  backbeat="sidestick"),
                          Section(name="chorus", bar=7)])
    save_song(item)
    again = load_song(item.directory)
    assert again.sections[0].backbeat == "sidestick"
    assert again.sections[1].backbeat == ""


def test_a_section_with_no_backbeat_gains_no_key(project):
    section = Section(name="chorus", bar=7)
    assert "backbeat" not in section.to_dict()


def test_an_unknown_backbeat_articulation_is_refused():
    from rambass.manifest import Song
    from rambass.project import ProjectError

    with pytest.raises(ProjectError) as caught:
        Song.from_dict({
            "slug": "x", "title": "X", "tempo": {"bpm": 60.0},
            "sections": [{"name": "v", "bar": 1, "backbeat": "tambourina"}],
        })
    assert "tambourina" in str(caught.value)


def test_rambass_check_reports_a_bad_articulation_without_raising():
    from rambass.manifest import Song

    item = Song(slug="x", title="X", bpm=60.0,
                sections=[Section(name="v", bar=1, backbeat="tambourina")])
    assert any("tambourina" in problem for problem in item.problems())


# ── velocity: the declared backbeat is even, because the numbers are broken ───
#
# Measured on Manlio after the declaration first ran: verse-2's side-sticks came
# out [45, 45, 45, 60, 118, 118, 121, 122 x9]. The spread is not dynamics, it is
# two incompatible scales in one section. scale_velocities works per instrument
# against that instrument's own median, so a hit detected in the hi-hat stem
# carries a number meaning "loud for a hi-hat" — and the moment it is renamed to
# a rim click that number means nothing at all.
#
# The population measured on the right instrument is the one the detector named
# from the snare stem: 34 hits, median 45, range 45-69. 45 is the velocity floor,
# and it is *correct* on this scale — a rim click on this kit really is 25-30 dB
# below the same drummer's snare, which at the 30 dB span scale_velocities uses
# is more than the whole velocity range. So the reference median is the honest
# number, evenness is the honest contour for a groove backbeat, and how loud it
# actually sounds is a Stage 8 decision about the kit's rim-click samples.


def test_the_declared_backbeat_comes_out_even():
    """Not an average of two scales — the one measured on the right instrument."""
    hits = [Hit("kick", 0.0, 100),
            Hit("sidestick", 1.0, 47),        # detector-named: right scale
            Hit("hihat_open", 3.0, 122),      # renamed: hi-hat scale, meaningless
            Hit("sidestick", 5.0, 51),
            Hit("hihat_closed", 7.0, 118)]
    performance = DrumPerformance(sorted(hits, key=lambda h: h.time),
                                 Timeline(bpm=60.0))
    out, report = voice_backbeats(
        performance, _sections(backbeat="sidestick"), end_bar=3)
    assert report["reference_velocity"] == 49        # median of 47 and 51
    assert [h.velocity for h in out.hits if h.instrument == "sidestick"] == [49] * 4


def test_an_explicit_velocity_wins_over_the_measured_one():
    """`drums clean --backbeat-velocity` exists because the floor is right on
    the part's own scale and may still be inaudible through a given kit."""
    performance = _verse(2)
    out, report = voice_backbeats(
        performance, _sections(backbeat="sidestick"), end_bar=3, velocity=88)
    assert report["reference_velocity"] == 88
    assert {h.velocity for h in out.hits if h.instrument == "sidestick"} == {88}


def test_with_nothing_measured_on_the_right_scale_the_velocities_are_left_alone():
    """Refusing beats guessing: with no reference population there is no honest
    number, so say so rather than invent one."""
    performance = _verse(2)                          # all hihat_open at 85
    out, report = voice_backbeats(
        performance, _sections(backbeat="sidestick"), end_bar=3)
    assert report["reference_velocity"] is None
    assert {h.velocity for h in out.hits if h.instrument == "sidestick"} == {85}


def test_a_section_that_declares_nothing_keeps_its_dynamics():
    performance = _verse(4, backbeat="snare", velocity=110)
    hits = list(performance.hits)
    hits[3] = hits[3].with_velocity(60)
    performance = DrumPerformance(hits, performance.timeline)
    out, _ = voice_backbeats(performance, _sections(), end_bar=5)
    assert out.hits == performance.hits

# ── how loud the click is belongs in song.yaml ────────────────────────────────
#
# Paolo, listening to Manlio's verses: *"the cross-stick/side-stick are too low
# in volume (eg: 24.4) and are barely audible."* He is right and the reason is in
# the block above: v45 is the honest median of the population measured on the
# right instrument, and on this kit's rim-click samples it disappears under
# hi-hats sitting at 75-98 (all 50 of Manlio's declared clicks came out 45,
# against a hat median of 81 and a snare median of 109).
#
# So the level is a decision, and a decision lives in the manifest. Same argument
# as `drums.subdivision`: `drums clean --backbeat-velocity 96` is whoever last
# typed a command, and the next re-run silently drops back to the floor —
# `drums.backbeat_velocity: 96` is in git, is reapplied every time, and shows up
# in `rambass stale` when it changes.


def test_the_backbeat_velocity_round_trips_through_song_yaml(project):
    from rambass.manifest import load_song, save_song
    from rambass.manifest import Song

    directory = project.songs_dir / "a" / "01-a"
    item = Song(slug="a", title="A", bpm=60.0, directory=directory,
                drum_backbeat_velocity=96)
    save_song(item, directory)
    assert load_song(directory).drum_backbeat_velocity == 96


def test_no_backbeat_velocity_means_use_the_measured_median(project):
    from rambass.manifest import Song, load_song, save_song

    directory = project.songs_dir / "a" / "01-a"
    save_song(Song(slug="a", title="A", bpm=60.0, directory=directory), directory)
    assert load_song(directory).drum_backbeat_velocity == 0


@pytest.mark.parametrize("value", [-1, 128, 200])
def test_a_backbeat_velocity_outside_the_midi_range_is_refused(value):
    from rambass.manifest import Song

    item = Song(slug="x", title="X", bpm=60.0, drum_backbeat_velocity=value)
    assert any("backbeat_velocity" in problem for problem in item.problems())


def test_a_backbeat_velocity_inside_the_range_is_fine():
    from rambass.manifest import Song

    for value in (0, 1, 96, 127):
        item = Song(slug="x", title="X", bpm=60.0, drum_backbeat_velocity=value)
        assert not [p for p in item.problems() if "backbeat_velocity" in p]


# ── end to end through `drums clean` ─────────────────────────────────────────


def _shuffle_verse_project(song, velocity):
    """A song whose verse declares a side-stick backbeat, with raw MIDI on disk."""
    from rambass.manifest import save_song
    from rambass.midiio import write_drum_midi

    song.bpm = 60.0
    song.bars = 4
    song.drum_subdivision = 3
    song.drum_cymbal_subdivision = 3
    song.sections = [Section("verse-2", 1, backbeat="sidestick")]
    song.drum_backbeat_velocity = velocity
    save_song(song, song.dir)
    from rambass.manifest import load_song

    reloaded = load_song(song.dir)
    performance = _verse(4)                     # hats at 70, open hats on 2 and 4
    write_drum_midi(reloaded.drum_midi_path("raw"),
                    DrumPerformance(performance.hits, reloaded.timeline()))
    return reloaded


def test_the_manifest_velocity_is_used_when_no_flag_is_given(cwd_song):
    from rambass.cli import main
    from rambass.midiio import read_drum_midi

    song = _shuffle_verse_project(cwd_song, 96)
    assert main(["drums", "clean", song.slug, "--output", "frommanifest"]) == 0
    out = read_drum_midi(song.drum_midi_path("frommanifest"))
    clicks = [h.velocity for h in out.hits if h.instrument == "sidestick"]
    assert clicks and set(clicks) == {96}


def test_an_explicit_flag_beats_the_manifest_velocity(cwd_song):
    from rambass.cli import main
    from rambass.midiio import read_drum_midi

    song = _shuffle_verse_project(cwd_song, 96)
    assert main(["drums", "clean", song.slug, "--backbeat-velocity", "70",
                 "--output", "forced"]) == 0
    out = read_drum_midi(song.drum_midi_path("forced"))
    clicks = [h.velocity for h in out.hits if h.instrument == "sidestick"]
    assert clicks and set(clicks) == {70}


def test_with_no_manifest_velocity_the_measured_median_still_wins(cwd_song):
    """0 means "not decided", which is not the same as velocity 0."""
    from rambass.cli import main
    from rambass.midiio import read_drum_midi

    song = _shuffle_verse_project(cwd_song, 0)
    assert main(["drums", "clean", song.slug, "--output", "measured"]) == 0
    out = read_drum_midi(song.drum_midi_path("measured"))
    clicks = [h.velocity for h in out.hits if h.instrument == "sidestick"]
    # nothing was named sidestick by the detector here, so there is no reference
    # population and the renamed hits keep the open hat's own velocity
    assert clicks and set(clicks) == {85}
