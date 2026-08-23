"""The hit-list passes: playability, collision resolution, cross-stem bleed.

All pure functions over ``list[Hit]``, so none of this needs librosa or audio.
Constants that came out of a measurement on Tutti in Fila are pinned here on
purpose — see the Testing section of CLAUDE.md.
"""

from __future__ import annotations

import pytest

from rambass.midiio import Hit
from rambass.timeline import Timeline
from rambass.transcribe import (
    clean_merged_hits,
    enforce_playability,
    resolve_collisions,
    suppress_cross_stem_bleed,
)


# ── enforce_playability ──────────────────────────────────────────────────────
#
# A drummer has two hands and two feet, and the feet are not interchangeable
# with the hands. Three cymbals/drums struck at the same instant is not a
# performance detail, it is a transcription error.


def test_three_hand_instruments_at_once_needs_a_third_hand():
    hits = [
        Hit("snare", 1.0, 110),
        Hit("hihat_closed", 1.005, 60),
        Hit("crash", 1.010, 95),
    ]
    kept, dropped = enforce_playability(hits)
    assert dropped == 1
    # The quietest of the three goes; the other two are playable with two hands.
    assert sorted(h.instrument for h in kept) == ["crash", "snare"]


def test_kick_is_a_foot_so_kick_plus_snare_plus_hat_survives():
    """The commonest event in rock drumming must not be touched."""
    hits = [
        Hit("kick", 2.0, 100),
        Hit("snare", 2.002, 105),
        Hit("hihat_closed", 2.004, 70),
    ]
    kept, dropped = enforce_playability(hits)
    assert dropped == 0
    assert len(kept) == 3


def test_kick_and_hihat_pedal_are_two_different_feet():
    hits = [
        Hit("kick", 0.5, 100),
        Hit("hihat_pedal", 0.5, 55),
        Hit("snare", 0.5, 100),
        Hit("ride", 0.5, 80),
    ]
    kept, dropped = enforce_playability(hits)
    assert dropped == 0, "two feet and two hands is exactly a drummer"


def test_a_fifth_limb_is_refused():
    hits = [
        Hit("kick", 0.5, 100),
        Hit("hihat_pedal", 0.5, 55),
        Hit("snare", 0.5, 100),
        Hit("ride", 0.5, 80),
        Hit("crash", 0.5, 40),
    ]
    kept, dropped = enforce_playability(hits)
    assert dropped == 1
    assert "crash" not in {h.instrument for h in kept}


def test_hits_outside_the_window_are_left_alone():
    hits = [
        Hit("snare", 1.0, 110),
        Hit("hihat_closed", 1.2, 60),
        Hit("crash", 1.4, 95),
    ]
    kept, dropped = enforce_playability(hits, window=0.030)
    assert dropped == 0
    assert len(kept) == 3


def test_repeated_strokes_of_one_instrument_are_deflams_job_not_ours():
    """Two snares 8 ms apart is one limb doing a flam. Not our problem."""
    hits = [Hit("snare", 1.0, 100), Hit("snare", 1.008, 90), Hit("kick", 1.0, 100)]
    kept, dropped = enforce_playability(hits)
    assert dropped == 0
    assert len(kept) == 3


def test_a_velocity_tie_breaks_deterministically_toward_the_drums():
    hits = [
        Hit("crash", 3.0, 80),
        Hit("hihat_closed", 3.0, 80),
        Hit("snare", 3.0, 80),
    ]
    first, _ = enforce_playability(hits)
    second, _ = enforce_playability(list(reversed(hits)))
    assert {h.instrument for h in first} == {h.instrument for h in second}
    assert "snare" in {h.instrument for h in first}, "the drum outranks the cymbals"


def test_empty_input_is_not_a_crash():
    assert enforce_playability([]) == ([], 0)


# ── resolve_collisions ───────────────────────────────────────────────────────
#
# Paolo: "kick+snare on the same beat are fairly uncommon ... based on the
# patterns of the song, if you have kick and snare on beat 1, it is more likely
# to be a kick". Measured on Manlio at triplet-8ths: beat 1 has 49 kicks alone
# against 4 snares alone, and 25 collisions. Beat 2 is the mirror image, 59
# snares to 4 kicks. So the song states its own answer.


#: Bleed does not leak on every single bar, and that matters: the census is
#: built from the bars where only one instrument fired, so a phantom present in
#: *every* bar leaves no evidence to arbitrate with and the function correctly
#: refuses. Manlio's real ratio on beat 1 is 49 clean bars to 25 collisions.
COLLIDE_EVERY = 3


def _shuffle_bars(bars: int, *, collide_on_one: bool = False) -> list[Hit]:
    """Manlio's actual pattern: kick on 1 and 3, snare on 2 and 4, at 60 BPM."""
    hits: list[Hit] = []
    for bar in range(bars):
        base = bar * 4.0
        hits.append(Hit("kick", base + 0.0, 100))
        hits.append(Hit("snare", base + 1.0, 110))
        hits.append(Hit("kick", base + 2.0, 100))
        hits.append(Hit("snare", base + 3.0, 110))
        if collide_on_one and bar % COLLIDE_EVERY == 0:
            hits.append(Hit("snare", base + 0.004, 70))
    return sorted(hits, key=lambda h: (h.time, h.instrument))


def test_the_songs_own_pattern_drops_a_phantom_snare_on_the_kick_beat():
    timeline = Timeline(bpm=60.0)
    hits = _shuffle_bars(24, collide_on_one=True)
    kept, report = resolve_collisions(hits, timeline, subdivision=3)
    assert report["dropped"] == 24 // COLLIDE_EVERY
    on_one = [h for h in kept if abs(h.time % 4.0) < 0.05]
    assert {h.instrument for h in on_one} == {"kick"}


def test_a_phantom_on_every_single_bar_leaves_no_evidence_and_is_refused():
    """Stated so nobody 'fixes' the refusal: with no clean bar the census is
    empty, and guessing from an empty census is how a part gets invented."""
    timeline = Timeline(bpm=60.0)
    hits = _shuffle_bars(24, collide_on_one=True)
    hits += [Hit("snare", bar * 4.0 + 0.004, 70)
             for bar in range(24) if bar % COLLIDE_EVERY]
    hits.sort(key=lambda h: (h.time, h.instrument))
    _, report = resolve_collisions(hits, timeline, subdivision=3)
    assert report["dropped"] == 0


def test_the_surviving_hits_are_otherwise_untouched():
    timeline = Timeline(bpm=60.0)
    hits = _shuffle_bars(24, collide_on_one=True)
    kept, _ = resolve_collisions(hits, timeline, subdivision=3)
    clean = _shuffle_bars(24)
    assert [(h.instrument, round(h.time, 6)) for h in kept] == [
        (h.instrument, round(h.time, 6)) for h in clean
    ]


def test_a_slot_with_no_preference_keeps_both():
    """A real kick+snare unison must survive where the song is ambiguous."""
    timeline = Timeline(bpm=60.0)
    hits: list[Hit] = []
    for bar in range(12):
        base = bar * 4.0
        # beat 1 alternates between kick-only and snare-only: no prior at all.
        hits.append(Hit("kick" if bar % 2 else "snare", base, 100))
        hits.append(Hit("snare", base + 1.0, 110))
    hits += [Hit("kick", 48.0, 100), Hit("snare", 48.004, 105)]
    kept, report = resolve_collisions(hits, timeline, subdivision=3)
    assert report["dropped"] == 0
    assert len([h for h in kept if abs(h.time - 48.0) < 0.05]) == 2


def test_too_little_evidence_refuses_to_act():
    timeline = Timeline(bpm=60.0)
    hits = [Hit("kick", 0.0, 100), Hit("snare", 0.004, 70)]
    kept, report = resolve_collisions(hits, timeline, subdivision=3, min_evidence=4)
    assert report["dropped"] == 0
    assert len(kept) == 2


def test_instruments_outside_the_pair_are_never_dropped():
    timeline = Timeline(bpm=60.0)
    hits = _shuffle_bars(24, collide_on_one=True)
    hits += [Hit("hihat_closed", bar * 4.0 + 0.002, 60) for bar in range(24)]
    hits.sort(key=lambda h: (h.time, h.instrument))
    kept, report = resolve_collisions(hits, timeline, subdivision=3)
    assert sum(1 for h in kept if h.instrument == "hihat_closed") == 24
    assert report["dropped"] == 24 // COLLIDE_EVERY


def test_a_loud_collision_is_kept_even_where_the_prior_is_strong():
    """A crash-accented backbeat is played *with* the kick, hard. Not bleed."""
    timeline = Timeline(bpm=60.0)
    hits = _shuffle_bars(12)
    hits.append(Hit("snare", 0.004, 125))
    hits.sort(key=lambda h: (h.time, h.instrument))
    kept, report = resolve_collisions(hits, timeline, subdivision=3)
    assert report["dropped"] == 0
    assert sum(1 for h in kept if abs(h.time) < 0.05) == 2


def test_empty_and_single_instrument_inputs_are_safe():
    timeline = Timeline(bpm=60.0)
    assert resolve_collisions([], timeline)[0] == []
    only = [Hit("kick", float(i), 100) for i in range(8)]
    kept, report = resolve_collisions(only, timeline, subdivision=3)
    assert kept == only and report["dropped"] == 0


# ── suppress_cross_stem_bleed ────────────────────────────────────────────────
#
# Measured on Manlio bar 1: the song opens on a single kick, and the snare part
# stem carries that kick at a tenth the level with the same decay envelope. The
# separator leaks; the onset detector believes it.


def test_a_quiet_hit_coinciding_with_a_much_louder_one_is_bleed():
    hits = [Hit("kick", float(i), 105) for i in range(10)]
    hits += [Hit("snare", i + 0.5, 108) for i in range(10)]
    hits.append(Hit("snare", 0.006, 60))     # the phantom on the one
    hits.sort(key=lambda h: (h.time, h.instrument))
    kept, dropped = suppress_cross_stem_bleed(hits)
    assert dropped == 1
    assert not [h for h in kept if h.instrument == "snare" and h.time < 0.4]


def test_a_loud_hit_under_a_loud_hit_is_an_accent_not_bleed():
    hits = [Hit("kick", float(i), 105) for i in range(10)]
    hits += [Hit("snare", i + 0.5, 108) for i in range(10)]
    hits.append(Hit("snare", 0.006, 112))
    hits.sort(key=lambda h: (h.time, h.instrument))
    _, dropped = suppress_cross_stem_bleed(hits)
    assert dropped == 0


def test_hats_are_excluded_because_they_play_under_everything():
    hits = [Hit("kick", float(i), 110) for i in range(10)]
    hits += [Hit("hihat_closed", float(i) + 0.004, 50) for i in range(10)]
    hits.sort(key=lambda h: (h.time, h.instrument))
    _, dropped = suppress_cross_stem_bleed(hits)
    assert dropped == 0, "gating hats here would delete the whole hat part"


def test_a_quiet_hit_on_its_own_is_a_ghost_note_and_survives():
    hits = [Hit("snare", float(i), 108) for i in range(10)]
    hits.append(Hit("snare", 5.5, 55))       # a ghost note, nothing beside it
    hits.sort(key=lambda h: (h.time, h.instrument))
    _, dropped = suppress_cross_stem_bleed(hits)
    assert dropped == 0


def test_too_few_hits_to_have_a_median_does_nothing():
    hits = [Hit("kick", 0.0, 110), Hit("snare", 0.004, 50)]
    _, dropped = suppress_cross_stem_bleed(hits)
    assert dropped == 0


@pytest.mark.parametrize("window", [0.005, 0.025])
def test_the_window_bounds_what_counts_as_coincident(window):
    hits = [Hit("kick", float(i), 105) for i in range(10)]
    hits += [Hit("snare", i + 0.5, 108) for i in range(10)]
    hits.append(Hit("snare", 0.015, 60))
    hits.sort(key=lambda h: (h.time, h.instrument))
    _, dropped = suppress_cross_stem_bleed(hits, window=window)
    assert dropped == (0 if window < 0.015 else 1)


# ── quiet by nature cuts both ways ───────────────────────────────────────────
#
# Measured on Manlio: this pass dropped 136 hits and **99 of them were drums
# deleted because a hi-hat beside them read louder** -- 44 kicks and 45 snares.
# A closed hat cannot out-shout a kick, so when the hat stem is the louder of
# the two the causality runs the other way: it is the drum leaking into the hat
# stem, not the hat leaking into the drum's. The instruments the pass already
# refuses to *delete* for being quiet by nature are exactly the ones it must
# refuse to *believe*, and leaving that asymmetry in place is what deleted the
# verse backbeat Paolo heard missing.


def test_a_hat_is_never_the_louder_partner_that_deletes_a_snare():
    """The Manlio verse backbeat: a rim click reads as a v122 closed hat, and
    that phantom accent was deleting the real stroke underneath it."""
    hits = [Hit("kick", float(i), 105) for i in range(10)]
    hits += [Hit("snare", i + 0.5, 108) for i in range(10)]
    hits.append(Hit("snare", 2.504, 60))            # the real, quiet stroke
    hits.append(Hit("hihat_closed", 2.506, 122))    # its own leak in the hat stem
    hits.sort(key=lambda h: (h.time, h.instrument))
    kept, dropped = suppress_cross_stem_bleed(hits)
    assert dropped == 0
    assert Hit("snare", 2.504, 60) in kept


def test_a_hat_is_never_the_louder_partner_that_deletes_a_kick():
    hits = [Hit("kick", float(i), 105) for i in range(10)]
    hits += [Hit("snare", i + 0.5, 108) for i in range(10)]
    hits.append(Hit("kick", 3.252, 55))
    hits.append(Hit("hihat_closed", 3.254, 120))
    hits.sort(key=lambda h: (h.time, h.instrument))
    _, dropped = suppress_cross_stem_bleed(hits)
    assert dropped == 0


def test_a_ride_is_not_loudness_evidence_either():
    hits = [Hit("kick", float(i), 105) for i in range(10)]
    hits += [Hit("snare", i + 0.5, 108) for i in range(10)]
    hits.append(Hit("snare", 4.504, 60))
    hits.append(Hit("ride", 4.506, 118))
    hits.sort(key=lambda h: (h.time, h.instrument))
    _, dropped = suppress_cross_stem_bleed(hits)
    assert dropped == 0


def test_a_real_drum_is_still_loudness_evidence():
    """The pass has to keep working: a loud kick next to a whisper-quiet snare
    in the snare stem is still Manlio bar 1, and still bleed."""
    hits = [Hit("kick", float(i), 105) for i in range(10)]
    hits += [Hit("snare", i + 0.5, 108) for i in range(10)]
    hits.append(Hit("snare", 0.006, 60))
    hits.sort(key=lambda h: (h.time, h.instrument))
    _, dropped = suppress_cross_stem_bleed(hits)
    assert dropped == 1


def test_a_sidestick_is_quiet_by_nature_and_is_never_deleted_as_bleed():
    hits = [Hit("kick", float(i), 105) for i in range(10)]
    hits += [Hit("sidestick", i + 0.5, 60) for i in range(10)]
    hits.append(Hit("sidestick", 5.004, 30))    # quiet even for a rim click
    hits.append(Hit("snare", 5.006, 115))
    hits.sort(key=lambda h: (h.time, h.instrument))
    _, dropped = suppress_cross_stem_bleed(hits)
    assert dropped == 0, "a rim click is 20-30 dB below a snare by construction"


# ── clean_merged_hits: the whole post-merge pipeline, in order ────────────────
#
# Extracted from transcribe_parts precisely so it can be tested here: the
# per-stem detection needs librosa and audio, this does not. Order matters and
# is asserted, because each pass assumes the previous one has run.


def test_the_pipeline_runs_every_pass_and_reports_each_one():
    timeline = Timeline(bpm=60.0)
    hits = _shuffle_bars(24, collide_on_one=True)
    kept, notes = clean_merged_hits(hits, timeline, subdivision=3)
    assert set(notes) >= {"cymbal_runs", "hat_pairs", "crash_bleed",
                          "cross_stem_bleed", "collisions", "playability"}
    # Every phantom goes, whichever pass gets to it first.
    assert len(kept) == len(_shuffle_bars(24))
    assert {h.instrument for h in kept if abs(h.time % 4.0) < 0.05} == {"kick"}


def test_a_quiet_phantom_is_caught_by_the_level_gate_not_the_pattern():
    """Ordering: the cheap level-based gate runs first and should take it."""
    timeline = Timeline(bpm=60.0)
    hits = _shuffle_bars(24, collide_on_one=True)      # phantoms at v70
    _, notes = clean_merged_hits(hits, timeline, subdivision=3)
    assert notes["cross_stem_bleed"] == 24 // COLLIDE_EVERY
    assert notes["collisions"]["dropped"] == 0


def test_a_phantom_that_is_normal_for_its_own_stem_needs_the_pattern():
    """The real Manlio case, and why resolve_collisions has to exist.

    That song's snare stem is mostly leakage and ghost strokes, so its *median*
    detection sits 20 dB below its real backbeats and bar 1's phantom reads as
    above average for its own stem. No level rule can see it. The song's
    pattern can: beat 1 is 49 kicks to 4 snares.
    """
    timeline = Timeline(bpm=60.0)
    hits: list[Hit] = []
    for bar in range(24):
        base = bar * 4.0
        hits.append(Hit("kick", base + 0.0, 100))
        hits.append(Hit("snare", base + 1.0, 100))
        hits.append(Hit("kick", base + 2.0, 100))
        hits.append(Hit("snare", base + 3.0, 100))
        if bar % COLLIDE_EVERY == 0:
            hits.append(Hit("snare", base + 0.004, 100))   # not quiet at all
    hits.sort(key=lambda h: (h.time, h.instrument))
    kept, notes = clean_merged_hits(hits, timeline, subdivision=3)
    assert notes["cross_stem_bleed"] == 0, "no level difference to work from"
    assert notes["collisions"]["dropped"] == 24 // COLLIDE_EVERY
    assert {h.instrument for h in kept if abs(h.time % 4.0) < 0.05} == {"kick"}


def test_the_pipeline_is_a_no_op_on_an_already_clean_part():
    timeline = Timeline(bpm=60.0)
    hits = _shuffle_bars(24)
    kept, notes = clean_merged_hits(hits, timeline, subdivision=3)
    assert kept == hits
    assert notes["collisions"]["dropped"] == 0
    assert notes["playability"] == 0


def test_the_pipeline_never_leaves_an_unplayable_cluster():
    """Whatever the earlier passes do, the output must be playable."""
    timeline = Timeline(bpm=60.0)
    hits = _shuffle_bars(24)
    for bar in range(24):                       # four hands on every downbeat
        base = bar * 4.0
        hits += [Hit("hihat_closed", base + 0.002, 70),
                 Hit("crash", base + 0.004, 65),
                 Hit("tom_mid", base + 0.006, 60),
                 Hit("ride", base + 0.008, 55)]
    hits.sort(key=lambda h: (h.time, h.instrument))
    kept, notes = clean_merged_hits(hits, timeline, subdivision=3)
    assert notes["playability"] > 0
    leftover, again = enforce_playability(kept)
    assert again == 0, "the pipeline's output must already be playable"
    assert leftover == kept


def test_the_pipeline_is_deterministic():
    timeline = Timeline(bpm=60.0)
    hits = _shuffle_bars(24, collide_on_one=True)
    first, notes_a = clean_merged_hits(hits, timeline, subdivision=3)
    second, notes_b = clean_merged_hits(list(hits), timeline, subdivision=3)
    assert first == second
    assert notes_a["collisions"]["dropped"] == notes_b["collisions"]["dropped"]


def test_an_empty_part_is_not_a_crash():
    kept, notes = clean_merged_hits([], Timeline(bpm=60.0), subdivision=3)
    assert kept == []
    assert notes["playability"] == 0


# ── hat sensitivity ──────────────────────────────────────────────────────────
#
# Measured on Manlio: 182 empty triplet slots carry hi-hat-band energy at or
# above the weakest tenth of the hits that WERE transcribed, so the subtle hat
# really is being missed. Lowering the picker's delta recovers it, but the
# marginal hits land on the grid at ~50% against 81% for the shipping set, and
# below 0.07 the additions are indistinguishable from chance. So this is a knob
# with a real trade-off behind it, not a constant waiting to be lowered.


def test_hat_delta_overrides_only_the_hat_band():
    from rambass.transcribe import PART_BANDS, bands_with_hat_delta

    tuned = bands_with_hat_delta(0.08)
    assert tuned["hihat"].delta == 0.08
    assert tuned["hihat"].low_hz == PART_BANDS["hihat"].low_hz
    assert tuned["hihat"].min_gap == PART_BANDS["hihat"].min_gap
    for name in ("kick", "snare", "toms", "cymbals"):
        assert tuned[name] == PART_BANDS[name]


def test_hat_delta_of_none_changes_nothing():
    from rambass.transcribe import PART_BANDS, bands_with_hat_delta

    assert bands_with_hat_delta(None) == PART_BANDS


def test_hat_delta_does_not_mutate_the_shipping_table():
    from rambass.transcribe import PART_BANDS, bands_with_hat_delta

    before = PART_BANDS["hihat"].delta
    bands_with_hat_delta(0.02)
    assert PART_BANDS["hihat"].delta == before
