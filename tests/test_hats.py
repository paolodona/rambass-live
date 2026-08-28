"""``sections[].hats`` — a declared hi-hat pattern, and the pass that fills it.

The largest single cause of Manlio's review notes: 66 of them are holes in a
continuous hi-hat run and 89 in total are a hat pattern the pipeline got wrong.
It is **not recoverable from the audio** — the threshold sweep and the hat-stem
flux probe both fail, and the occupancy count cannot even tell ``chorus-1`` (a
continuous triplet run reading 6 of 12 slots) from ``chorus-2`` (a genuine
shuffle reading 9). So it is declared, in the register ``voicing:`` and
``backbeat:`` already established.
"""

from __future__ import annotations

import pytest

from rambass.manifest import Section, hat_slots, load_song, save_song
from rambass.midiio import DrumPerformance, Hit
from rambass.project import ProjectError
from rambass.restore import fill_hat_runs
from rambass.timeline import Timeline

#: 60 BPM in 4/4 with a triplet grid, as Manlio is: a beat is 1 s, a bar 4 s,
#: a slot 1/3 s and there are twelve slots to the bar.
TIMELINE = Timeline(bpm=60.0)


def at(bar, beat):
    return TIMELINE.bar_beat_to_seconds(bar, beat)


def section(name, bar, *, beat=1.0, hats="", voicing=None):
    return Section(name=name, bar=bar, beat=beat, hats=hats,
                   voicing=dict(voicing or {}))


# ── the mask ─────────────────────────────────────────────────────────────

def test_run_is_every_slot():
    assert hat_slots("run", subdivision=3, beats_per_bar=4) == (True,) * 12


def test_shuffle_is_the_first_and_last_slot_of_each_beat():
    assert hat_slots("shuffle", subdivision=3, beats_per_bar=4) == (
        True, False, True, True, False, True,
        True, False, True, True, False, True)


def test_an_explicit_mask_reads_one_character_per_slot():
    assert hat_slots("x.xx.xx.xx.x", subdivision=3, beats_per_bar=4) == (
        True, False, True, True, False, True,
        True, False, True, True, False, True)


def test_whitespace_and_bars_are_ignored_so_a_reader_can_group_it():
    grouped = hat_slots("x.x | x.x | x.x | x.x", subdivision=3, beats_per_bar=4)
    assert grouped == hat_slots("shuffle", subdivision=3, beats_per_bar=4)


def test_nothing_declared_is_nothing_to_do():
    assert hat_slots("", subdivision=3, beats_per_bar=4) is None


def test_an_unknown_keyword_is_an_error_not_a_no_op():
    """A typo fills nothing and looks exactly like a no-op — the worst failure."""
    with pytest.raises(ProjectError, match="hats"):
        hat_slots("straight", subdivision=3, beats_per_bar=4)


def test_a_wrong_length_mask_is_an_error():
    with pytest.raises(ProjectError, match="12"):
        hat_slots("x.xx.x", subdivision=3, beats_per_bar=4)


def test_shuffle_needs_a_triplet_grid():
    with pytest.raises(ProjectError, match="subdivision"):
        hat_slots("shuffle", subdivision=4, beats_per_bar=4)


# ── the manifest field ───────────────────────────────────────────────────

def test_it_round_trips_through_the_manifest(song):
    song.sections = [section("chorus", 1, beat=3.0, hats="run"),
                     section("verse", 9, hats="x.xx.xx.xx.x"),
                     section("break", 17)]
    song.drum_subdivision = 3
    save_song(song, song.dir)
    back = load_song(song.dir)
    assert [s.hats for s in sorted(back.sections, key=lambda s: s.position)] == [
        "run", "x.xx.xx.xx.x", ""]


def test_an_undeclared_section_writes_no_key(song):
    """Nothing gains a redundant `hats: ''`, the same as `beat` and `voicing`."""
    song.sections = [section("verse", 1)]
    save_song(song, song.dir)
    assert "hats" not in song.dir.joinpath("song.yaml").read_text(encoding="utf-8")


def test_a_bad_declaration_is_reported_by_check(song):
    song.drum_subdivision = 3
    song.sections = [section("verse", 1, hats="straight")]
    problems = song.problems()
    assert any("hats" in p and "verse" in p for p in problems), problems


def test_a_wrong_length_mask_is_reported_by_check(song):
    song.drum_subdivision = 3
    song.sections = [section("verse", 1, hats="x.x")]
    assert any("verse" in p and "12" in p for p in song.problems())


def test_shuffle_on_a_straight_grid_is_reported_by_check(song):
    """And the message names the song, since the grid is the song's."""
    song.drum_subdivision = 4
    song.sections = [section("verse", 1, hats="shuffle")]
    assert any("shuffle" in p and "subdivision" in p for p in song.problems())


def test_a_good_declaration_is_not_reported(song):
    song.drum_subdivision = 3
    song.sections = [section("verse", 1, hats="run"),
                     section("chorus", 9, hats="shuffle"),
                     section("outro", 17, hats="xxx.........")]
    assert not [p for p in song.problems() if "hats" in p]


# ── the pass ─────────────────────────────────────────────────────────────

def hats_in(performance, bar):
    return sorted(round((h.time - at(bar, 1.0)) / (1 / 3), 3)
                  for h in performance.hits
                  if h.instrument.startswith("hihat") or h.instrument.startswith("ride")
                  if at(bar, 1.0) - 1e-9 <= h.time < at(bar + 1, 1.0) - 1e-9)


def test_run_fills_every_slot_of_every_bar():
    # Bar 1 plays right up to its last slot, so it has not stopped; bar 2 is
    # empty, which is not a stop either. See the stop-rule tests below.
    hits = [Hit("hihat_closed", at(1, 1.0), 90), Hit("kick", at(1, 4.667), 100)]
    out, report = fill_hat_runs(
        DrumPerformance(hits, TIMELINE), [section("verse", 1, hats="run")],
        end_bar=3, subdivision=3)
    assert hats_in(out, 1) == [float(i) for i in range(12)]
    assert hats_in(out, 2) == [float(i) for i in range(12)]
    assert report["added"] == 23           # 24 slots less the one already there
    assert report["sections"][0]["name"] == "verse"


def test_a_bar_holding_one_lonely_detection_counts_as_stopped():
    """A consequence of sharing the stop helper, and it is the right one.

    One hit on beat 1 and nothing else *is* a bar that stopped after beat 1 —
    Manlio's bar 32 is exactly that, and consolidate withholds its stamp there
    for the same reason. It does mean one detection is treated more
    conservatively than none, which looks odd until you say it out loud: an
    empty bar carries no evidence about where the playing stopped, and a bar
    with one hit at the front carries some.
    """
    hits = [Hit("hihat_closed", at(1, 1.0), 90)]
    out, report = fill_hat_runs(
        DrumPerformance(hits, TIMELINE), [section("verse", 1, hats="run")],
        end_bar=2, subdivision=3)
    assert hats_in(out, 1) == [0.0]
    assert report["sections"][0]["stopped"] == [{"bar": 1, "beat": 1.0}]


def test_a_shuffle_fills_only_its_own_slots():
    out, _ = fill_hat_runs(
        DrumPerformance([], TIMELINE), [section("verse", 1, hats="shuffle")],
        end_bar=2, subdivision=3)
    assert hats_in(out, 1) == [0.0, 2.0, 3.0, 5.0, 6.0, 8.0, 9.0, 11.0]


def test_an_explicit_mask_fills_only_its_own_slots():
    out, _ = fill_hat_runs(
        DrumPerformance([], TIMELINE), [section("verse", 1, hats="xxx.........")],
        end_bar=2, subdivision=3)
    assert hats_in(out, 1) == [0.0, 1.0, 2.0]


def test_an_undeclared_section_is_untouched():
    hits = [Hit("hihat_closed", at(1, 1.0), 90)]
    out, report = fill_hat_runs(
        DrumPerformance(hits, TIMELINE), [section("verse", 1)],
        end_bar=3, subdivision=3)
    assert len(out.hits) == 1
    assert report["added"] == 0


def test_an_existing_open_hat_at_a_masked_slot_is_left_alone():
    """Not doubled and not renamed.

    The hats the transcriber *did* find carry real velocities, and flattening
    them would delete the part while claiming to complete it — the same argument
    ``revoice_sections`` makes for keeping velocities.
    """
    hits = [Hit("hihat_open", at(1, 2.0), 113)]
    out, _ = fill_hat_runs(
        DrumPerformance(hits, TIMELINE), [section("verse", 1, hats="run")],
        end_bar=2, subdivision=3)
    at_slot = [h for h in out.hits if abs(h.time - at(1, 2.0)) < 1e-9]
    assert len(at_slot) == 1
    assert at_slot[0].instrument == "hihat_open"
    assert at_slot[0].velocity == 113


def test_any_hat_family_instrument_holds_its_slot():
    """A ride or a foot splash is a hat for this purpose — see HAT_FAMILY."""
    for instrument in ("hihat_closed", "hihat_open", "hihat_pedal",
                       "ride", "ride_bell"):
        hits = [Hit(instrument, at(1, 2.0), 80)]
        out, _ = fill_hat_runs(
            DrumPerformance(hits, TIMELINE), [section("v", 1, hats="run")],
            end_bar=2, subdivision=3)
        held = [h for h in out.hits if abs(h.time - at(1, 2.0)) < 1e-9]
        assert [h.instrument for h in held] == [instrument]


def test_a_snare_at_a_masked_slot_does_not_hold_it():
    """The mask is about the hat, and a hat plays over the backbeat."""
    hits = [Hit("snare", at(1, 2.0), 110)]
    out, _ = fill_hat_runs(
        DrumPerformance(hits, TIMELINE), [section("v", 1, hats="run")],
        end_bar=2, subdivision=3)
    at_slot = sorted(h.instrument for h in out.hits
                     if abs(h.time - at(1, 2.0)) < 1e-9)
    assert at_slot == ["hihat_closed", "snare"]


def test_the_velocity_is_the_median_of_the_sections_own_hats():
    hits = [Hit("hihat_closed", at(1, 1.0), 80),
            Hit("hihat_closed", at(1, 2.0), 100),
            Hit("hihat_closed", at(1, 3.0), 90)]
    out, report = fill_hat_runs(
        DrumPerformance(hits, TIMELINE), [section("v", 1, hats="run")],
        end_bar=2, subdivision=3)
    added = [h for h in out.hits if h.time not in {at(1, 1.0), at(1, 2.0), at(1, 3.0)}]
    assert {h.velocity for h in added} == {90}
    assert report["sections"][0]["velocity"] == 90
    assert report["sections"][0]["velocity_from"] == "section"


def test_the_velocity_falls_back_to_the_songs_own_hats():
    """A section with no hats of its own borrows the song's median."""
    hits = [Hit("hihat_closed", at(5, 1.0), 70),
            Hit("hihat_closed", at(5, 2.0), 70)]
    out, report = fill_hat_runs(
        DrumPerformance(hits, TIMELINE),
        [section("v", 1, hats="run"), section("chorus", 5)],
        end_bar=6, subdivision=3)
    added = [h for h in out.hits if h.time < at(2, 1.0)]
    assert {h.velocity for h in added} == {70}
    assert report["sections"][0]["velocity_from"] == "song"


def test_the_velocity_falls_back_to_forty_five_with_nothing_measured():
    out, report = fill_hat_runs(
        DrumPerformance([], TIMELINE), [section("v", 1, hats="run")],
        end_bar=2, subdivision=3)
    assert {h.velocity for h in out.hits} == {45}
    assert report["sections"][0]["velocity_from"] == "floor"


def test_a_section_starting_on_a_fractional_beat_still_tiles_the_bar_grid():
    """``chorus-1`` starts at bar 32 beat 3. The mask repeats every *bar*.

    A one-bar hat figure repeats every bar whichever beat the section began on,
    because a section boundary does not move where beat 1 is — the same
    reasoning as ``quantize._repetitions``. So the mask's slot 0 is beat 1 of
    the bar, not the section's first slot.
    """
    out, _ = fill_hat_runs(
        DrumPerformance([], TIMELINE),
        [section("chorus", 1, beat=3.0, hats="xxx.........")],
        end_bar=3, subdivision=3)
    # Bar 1's slots 0-2 are before the section starts, so only bar 2 is filled.
    assert hats_in(out, 1) == []
    assert hats_in(out, 2) == [0.0, 1.0, 2.0]


def test_it_does_not_fill_past_the_end_of_the_last_section():
    out, _ = fill_hat_runs(
        DrumPerformance([], TIMELINE), [section("v", 1, hats="run")],
        end_bar=3, subdivision=3)
    assert not [h for h in out.hits if h.time >= at(3, 1.0) - 1e-9]


def test_it_does_not_fill_past_the_start_of_the_next_section():
    out, _ = fill_hat_runs(
        DrumPerformance([], TIMELINE),
        [section("v", 1, hats="run"), section("break", 2, beat=3.0)],
        end_bar=4, subdivision=3)
    assert not [h for h in out.hits if h.time >= at(2, 3.0) - 1e-9]


# ── the interaction that matters ─────────────────────────────────────────

def test_a_mask_does_not_fill_the_tail_of_a_bar_that_stopped():
    """Phase 3 must not silently undo Phase 1.

    Bar 17 of Manlio is the case: the band stops dead at beat 3 and
    ``consolidate`` withholds its stamp there. A declared ``hats: run`` that
    ignored the stop would put twelve hats straight back in — the mask is a
    statement about the *pattern*, and the pattern is not played after the band
    has stopped.
    """
    hits = [Hit("hihat_closed", at(1, b / 3 + 1.0), 90) for b in range(0, 7)]
    hits += [Hit("hihat_closed", at(2, b / 3 + 1.0), 90) for b in range(0, 12)]
    out, report = fill_hat_runs(
        DrumPerformance(hits, TIMELINE), [section("v", 1, hats="run")],
        end_bar=3, subdivision=3)
    # Bar 1 played slots 0-6 and then stopped: nothing is added after slot 6.
    assert hats_in(out, 1) == [float(i) for i in range(7)]
    assert hats_in(out, 2) == [float(i) for i in range(12)]
    assert report["sections"][0]["stopped"] == [{"bar": 1, "beat": 3.0}]


def test_stop_beats_zero_fills_the_tail_of_a_stopped_bar():
    """The same escape hatch as consolidate's, and it must line up with it."""
    hits = [Hit("hihat_closed", at(1, b / 3 + 1.0), 90) for b in range(0, 7)]
    out, _ = fill_hat_runs(
        DrumPerformance(hits, TIMELINE), [section("v", 1, hats="run")],
        end_bar=2, subdivision=3, stop_beats=0.0)
    assert hats_in(out, 1) == [float(i) for i in range(12)]


def test_an_empty_bar_inside_a_declared_section_is_filled():
    """An empty bar is not a stop — the declaration is what fills it."""
    out, _ = fill_hat_runs(
        DrumPerformance([], TIMELINE), [section("v", 1, hats="run")],
        end_bar=3, subdivision=3)
    assert hats_in(out, 1) == [float(i) for i in range(12)]
    assert hats_in(out, 2) == [float(i) for i in range(12)]


def test_it_fills_the_closed_hat_and_voicing_retargets_it():
    """One mechanism per decision.

    ``theme-finale`` already turns hats into a ride bell with ``voicing:``, so
    ``fill_hat_runs`` has no instrument option to argue with it — it fills
    ``hihat_closed`` and ``revoice_sections`` runs afterwards.
    """
    from rambass.restore import revoice_sections

    filled, _ = fill_hat_runs(
        DrumPerformance([], TIMELINE),
        [section("finale", 1, hats="run", voicing={"hihat_closed": "ride_bell"})],
        end_bar=2, subdivision=3)
    assert {h.instrument for h in filled.hits} == {"hihat_closed"}
    voiced, report = revoice_sections(
        filled, [section("finale", 1, hats="run",
                         voicing={"hihat_closed": "ride_bell"})], end_bar=2)
    assert {h.instrument for h in voiced.hits} == {"ride_bell"}
    assert report["renamed"] == 12


# ── provenance ───────────────────────────────────────────────────────────

def test_changing_hats_makes_the_restore_step_stale(project, song, tmp_path):
    """A `hats:` change that `rambass stale` does not notice never gets applied.

    The same hole `voicing` had before it was added to the restore step's
    fields: the declaration changes, the report says ok, and the review screen
    calls a candidate current that was built from the old pattern.
    """
    from rambass.midiio import write_drum_midi
    from rambass.provenance import stale_report, stamp

    song.drum_subdivision = 3
    song.sections = [section("verse", 1, hats="run")]
    save_song(song, song.dir)
    reloaded = load_song(song.dir)

    consolidated = reloaded.drum_midi_path("consolidated")
    restored = reloaded.drum_midi_path("restored")
    for variant, path in (("consolidate", consolidated), ("restore", restored)):
        write_drum_midi(path, DrumPerformance([Hit("hihat_closed", 0.0, 90)],
                                             reloaded.timeline()))
        stamp(reloaded, path, step=f"drums {variant}",
              inputs=[consolidated] if variant == "restore" else [])

    assert not [r for r in stale_report(reloaded)
                if r.step == "drums restore" and r.state == "stale"]

    reloaded.sections[0].hats = "shuffle"
    save_song(reloaded, reloaded.dir)
    after = load_song(reloaded.dir)
    stale = [r for r in stale_report(after)
             if r.step == "drums restore" and r.state == "stale"]
    assert stale, "a hats change must make drums restore stale"
    assert any("sections" in reason for reason in stale[0].reasons), stale[0].reasons


# ── the proposal ─────────────────────────────────────────────────────────

def _slot_hits(song, bars, slots):
    """Hats at the given slot indices of the given bars, on the *song's* grid."""
    timeline = song.timeline()
    return [Hit("hihat_closed", when, 90)
            for bar in bars
            for slot, when in enumerate(
                timeline.grid_seconds(song.drum_subdivision, bar, bar + 1))
            if slot in slots]


def test_a_full_run_is_proposed_as_run(project, song):
    from rambass.sections import propose_hats

    song.drum_subdivision = 3
    song.bars = 4
    song.sections = [section("verse", 1)]
    hits = _slot_hits(song, (1, 2, 3, 4), set(range(12)))
    proposals = propose_hats(song, DrumPerformance(hits, song.timeline()))
    assert len(proposals) == 1
    assert proposals[0].section == "verse"
    assert proposals[0].slots == tuple(range(12))
    assert proposals[0].keyword == "run"
    assert proposals[0].mask == "xxxxxxxxxxxx"


def test_a_shuffle_is_proposed_as_shuffle(project, song):
    from rambass.sections import propose_hats

    song.drum_subdivision = 3
    song.bars = 4
    song.sections = [section("verse", 1)]
    hits = _slot_hits(song, (1, 2, 3, 4), {s for s in range(12) if s % 3 != 1})
    proposals = propose_hats(song, DrumPerformance(hits, song.timeline()))
    assert proposals[0].keyword == "shuffle"
    assert proposals[0].mask == "x.xx.xx.xx.x"


def test_a_slot_played_in_under_half_the_bars_is_not_proposed(project, song):
    """The proposal is occupancy, and occupancy is what cannot tell a run from
    a shuffle — so it is printed for a human, never written."""
    from rambass.sections import propose_hats

    song.drum_subdivision = 3
    song.bars = 4
    song.sections = [section("verse", 1)]
    hits = _slot_hits(song, (1, 2, 3, 4), {0})
    hits += _slot_hits(song, (1,), {3})                    # 1 of 4 bars
    proposals = propose_hats(song, DrumPerformance(hits, song.timeline()))
    assert proposals[0].slots == (0,)
    assert proposals[0].keyword == ""


def test_a_section_already_declaring_hats_is_marked_as_such(project, song):
    from rambass.sections import propose_hats

    song.drum_subdivision = 3
    song.bars = 4
    song.sections = [section("verse", 1, hats="run")]
    hits = _slot_hits(song, (1, 2, 3, 4), {0})
    assert propose_hats(
        song, DrumPerformance(hits, song.timeline()))[0].declared == "run"


def test_the_proposal_reaches_the_sections_command(cwd_song, capsys):
    from rambass.cli import main
    from rambass.midiio import write_drum_midi

    cwd_song.drum_subdivision = 3
    cwd_song.bars = 8
    cwd_song.sections = [section("verse", 1), section("chorus", 5)]
    save_song(cwd_song, cwd_song.dir)
    reloaded = load_song(cwd_song.dir)
    timeline = reloaded.timeline()
    hits = [Hit("hihat_closed", timeline.bar_beat_to_seconds(b, 1.0 + s / 3), 90)
            for b in range(1, 9) for s in range(12)]
    write_drum_midi(reloaded.drum_midi_path("quantized"),
                    DrumPerformance(hits, timeline))

    assert main(["sections", "tutti-in-fila", "--hats"]) == 0
    out = capsys.readouterr().out
    assert "verse" in out and "run" in out
    assert "xxxxxxxxxxxx" in out
    # It proposes and never writes.
    assert all(not s.hats for s in load_song(reloaded.dir).sections)
    assert "nothing was changed" in out.lower() or "never" in out.lower()


# ── the command ──────────────────────────────────────────────────────────

def test_drums_restore_fills_a_declared_pattern(cwd_song, capsys):
    from rambass.cli import main
    from rambass.midiio import read_drum_midi, write_drum_midi

    cwd_song.drum_subdivision = 3
    cwd_song.bars = 4
    cwd_song.sections = [section("verse", 1, hats="run")]
    save_song(cwd_song, cwd_song.dir)
    reloaded = load_song(cwd_song.dir)
    timeline = reloaded.timeline()
    # One bar's worth of playing right to its last slot, so nothing "stopped".
    hits = [Hit("hihat_closed", when, 90) for bar in (1, 2, 3, 4)
            for slot, when in enumerate(timeline.grid_seconds(3, bar, bar + 1))
            if slot in (0, 11)]
    write_drum_midi(reloaded.drum_midi_path("consolidated"),
                    DrumPerformance(hits, timeline))

    assert main(["drums", "restore", "tutti-in-fila"]) == 0
    out = capsys.readouterr().out
    assert "hats" in out and "verse" in out
    assert "+40 filled" in out, out        # 4 bars x 12 slots less the 8 present

    after = read_drum_midi(reloaded.drum_midi_path("restored"))
    assert len([h for h in after.hits if h.instrument == "hihat_closed"]) == 48


def test_drums_restore_needs_no_additions_when_a_section_declares_hats(cwd_song, capsys):
    """`hats` is the fourth kind of declared Stage 7 edit, so it cannot bail."""
    from rambass.cli import main
    from rambass.midiio import write_drum_midi

    cwd_song.drum_subdivision = 3
    cwd_song.bars = 2
    cwd_song.sections = [section("verse", 1, hats="shuffle")]
    cwd_song.drum_additions = []
    cwd_song.drum_removals = []
    save_song(cwd_song, cwd_song.dir)
    reloaded = load_song(cwd_song.dir)
    write_drum_midi(reloaded.drum_midi_path("consolidated"),
                    DrumPerformance([], reloaded.timeline()))

    assert main(["drums", "restore", "tutti-in-fila"]) == 0
    out = capsys.readouterr().out
    assert "no drums.additions" not in out
    assert reloaded.drum_midi_path("restored").exists()


def test_the_fill_runs_before_the_voicing_so_a_ride_run_is_two_lines(cwd_song, capsys):
    """`hats: run` plus `voicing: {hihat_closed: ride_bell}` is a ride run."""
    from rambass.cli import main
    from rambass.midiio import read_drum_midi, write_drum_midi

    cwd_song.drum_subdivision = 3
    cwd_song.bars = 2
    cwd_song.sections = [section("finale", 1, hats="run",
                                 voicing={"hihat_closed": "ride_bell"})]
    save_song(cwd_song, cwd_song.dir)
    reloaded = load_song(cwd_song.dir)
    write_drum_midi(reloaded.drum_midi_path("consolidated"),
                    DrumPerformance([], reloaded.timeline()))

    assert main(["drums", "restore", "tutti-in-fila"]) == 0
    after = read_drum_midi(reloaded.drum_midi_path("restored"))
    assert {h.instrument for h in after.hits} == {"ride_bell"}
    assert len(after.hits) == 24
