"""The side-stick: detecting a rim click, and deleting the hat that was only it.

Manlio's verses play the backbeat as a **side-stick** — a stick laid across the
rim with the tip on the head — and the transcriber had no name for it, so the
part came out of the pipeline with *zero* snare hits in verse-1, verse-2 and
verse-3 and a ``hihat_closed`` at velocity 122 on beats 2 and 4 instead.

Measured on the album stems at the 202 snare-stem detections, classified against
the section list:

===============  ===  ====================  ======================
class              n  kit 180-500 Hz share  snare stem - hat stem
===============  ===  ====================  ======================
verse backbeat    24                  0.21               -17.8 dB
snare backbeat    69                  0.57               +28.6 dB
===============  ===  ====================  ======================

A rim click puts nothing into the shell, so it has no body; and because all of
its energy is a high-frequency transient it lands **louder in the hi-hat stem
than in the snare stem**, which a real snare never does. Both features together
catch 23 of the 24 verse backbeats and **none** of the 69 real snare backbeats.
That asymmetry is the point: turning a snare into a rim click is the error that
would be heard, and this rule never makes it. Either feature alone does — the
level test alone takes 3 real backbeats, the body test alone takes 5 ghosts.

All pure functions over ``list[Hit]`` or over numpy arrays, so none of this needs
librosa, ffmpeg or an audio file — see the Testing section of CLAUDE.md.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from rambass.midiio import Hit
from rambass.timeline import Timeline
from rambass.transcribe import (
    SIDESTICK_BODY_SHARE,
    SIDESTICK_HAT_MARGIN_DB,
    clean_merged_hits,
    drop_hats_on_sidesticks,
    label_sidesticks,
    sidestick_evidence,
)


def _evidence_for(hits, body, margin):
    """(body share, hat margin) for every hit, the same value throughout."""
    return [(body, margin) for _ in hits]


# ── the measured constants ───────────────────────────────────────────────────


def test_the_constants_are_the_ones_the_measurement_argued_for():
    """Named here so they are not "cleaned up": 0.35 sits between the verse
    backbeats' 0.21 and the real backbeats' 0.57, and 0.0 dB is the sign flip
    between -17.8 and +28.6 — a 46 dB gap, not a tuned threshold."""
    assert SIDESTICK_BODY_SHARE == 0.35
    assert SIDESTICK_HAT_MARGIN_DB == 0.0


# ── label_sidesticks ─────────────────────────────────────────────────────────


def test_a_snare_hit_with_no_body_and_louder_in_the_hat_stem_is_a_sidestick():
    hits = [Hit("snare", float(i), 47) for i in range(4)]
    kept, count = label_sidesticks(hits, _evidence_for(hits, 0.21, -17.8))
    assert count == 4
    assert {h.instrument for h in kept} == {"sidestick"}
    assert [h.time for h in kept] == [h.time for h in hits]
    assert [h.velocity for h in kept] == [h.velocity for h in hits]


def test_a_real_snare_backbeat_is_left_alone():
    hits = [Hit("snare", float(i), 105) for i in range(4)]
    kept, count = label_sidesticks(hits, _evidence_for(hits, 0.57, 28.6))
    assert count == 0
    assert kept == hits


def test_body_alone_is_not_enough():
    """On its own the body test took 5 ghost detections on Manlio."""
    hits = [Hit("snare", float(i), 105) for i in range(4)]
    _, count = label_sidesticks(hits, _evidence_for(hits, 0.05, 12.0))
    assert count == 0


def test_the_hat_margin_alone_is_not_enough():
    """On its own the level test took 3 real snare backbeats — bar 14 beat 2 and
    bar 51 beat 2 both sit at -2 dB with a full 0.6 body share."""
    hits = [Hit("snare", float(i), 105) for i in range(4)]
    _, count = label_sidesticks(hits, _evidence_for(hits, 0.60, -2.2))
    assert count == 0


def test_an_unmeasurable_hit_is_left_as_a_snare():
    hits = [Hit("snare", float(i), 47) for i in range(4)]
    _, count = label_sidesticks(hits, [(math.nan, math.nan)] * 4)
    assert count == 0


def test_only_snares_are_candidates():
    hits = [Hit("hihat_closed", 0.0, 122), Hit("kick", 1.0, 100),
            Hit("tom_mid", 2.0, 90), Hit("sidestick", 3.0, 47)]
    kept, count = label_sidesticks(hits, _evidence_for(hits, 0.05, -20.0))
    assert count == 0
    assert kept == hits


def test_evidence_of_the_wrong_length_is_refused_rather_than_guessed():
    hits = [Hit("snare", float(i), 47) for i in range(4)]
    kept, count = label_sidesticks(hits, [(0.1, -20.0)])
    assert count == 0
    assert kept == hits


def test_no_hits_is_not_a_crash():
    assert label_sidesticks([], []) == ([], 0)


# ── drop_hats_on_sidesticks ──────────────────────────────────────────────────
#
# The click's transient is all high frequency, so the hat stem's own detector
# fires on it. Measured on Manlio's verses, per triplet slot: the hi-hat stem is
# 13-14 dB louder at beats 2 and 4 than at the surrounding triplets, and its
# 6-16 kHz share collapses from 0.83-0.97 (a closed hat is nearly all top end)
# to 0.30-0.43 (a stick on a rim is 1-5 kHz). What the detector reported there is
# the click. A hi-hat note whose whole evidence is a rim click does not belong in
# the part; if a hat is wanted under the click, that is a Stage 7 decision at a
# sane velocity, not a v122 accent on every backbeat of the song.


def test_a_hat_on_a_sidestick_is_the_click_and_goes():
    hits = [Hit("sidestick", 1.0, 47), Hit("hihat_closed", 1.004, 122),
            Hit("hihat_closed", 1.333, 70), Hit("hihat_closed", 1.667, 68)]
    kept, dropped = drop_hats_on_sidesticks(hits)
    assert dropped == 1
    assert [h.instrument for h in kept] == [
        "sidestick", "hihat_closed", "hihat_closed"]
    assert [round(h.time, 3) for h in kept] == [1.0, 1.333, 1.667]


def test_a_hat_with_no_sidestick_under_it_stays():
    hits = [Hit("hihat_closed", t, 70) for t in (1.0, 1.333, 1.667)]
    kept, dropped = drop_hats_on_sidesticks(hits)
    assert dropped == 0
    assert kept == hits


def test_a_snare_does_not_take_its_hat_with_it():
    hits = [Hit("snare", 1.0, 110), Hit("hihat_closed", 1.004, 70)]
    kept, dropped = drop_hats_on_sidesticks(hits)
    assert dropped == 0
    assert kept == hits


def test_the_window_bounds_which_hat_belongs_to_the_click():
    hits = [Hit("sidestick", 1.0, 47), Hit("hihat_closed", 1.100, 70)]
    _, dropped = drop_hats_on_sidesticks(hits)
    assert dropped == 0, "100 ms away is the next triplet, not the same stroke"


@pytest.mark.parametrize("hat", ["hihat_open", "hihat_pedal"])
def test_every_hat_articulation_is_dropped(hat):
    hits = [Hit("sidestick", 1.0, 47), Hit(hat, 1.004, 118)]
    _, dropped = drop_hats_on_sidesticks(hits)
    assert dropped == 1


def test_the_ride_and_the_cymbals_are_not_touched():
    """Only the hat stem fires on a rim click; a crash beside one is a crash."""
    hits = [Hit("sidestick", 1.0, 47), Hit("ride", 1.004, 90),
            Hit("crash", 1.006, 95)]
    kept, dropped = drop_hats_on_sidesticks(hits)
    assert dropped == 0
    assert kept == hits


# ── sidestick_evidence: the measurement, on arrays ───────────────────────────


def _burst(sr, duration, freqs, amplitude=0.5, decay=120.0):
    """A decaying sum of tones — a crude drum stroke with a chosen spectrum."""
    t = np.arange(int(sr * duration)) / sr
    out = np.zeros_like(t)
    for hz in freqs:
        out += np.sin(2 * np.pi * hz * t)
    return amplitude * out / len(freqs) * np.exp(-decay * t)


def _stem(sr, seconds, at, burst):
    x = np.zeros(int(sr * seconds), dtype=float)
    start = int(at * sr)
    x[start:start + burst.size] += burst[: x.size - start]
    return x


def test_a_rim_click_measures_as_no_body_and_louder_in_the_hat_stem():
    sr, at = 22050, 1.0
    click = _burst(sr, 0.08, (2500.0, 4000.0, 6000.0))
    kit = _stem(sr, 3.0, at, click)
    snare = _stem(sr, 3.0, at, click * 0.10)     # barely in the snare stem
    hat = _stem(sr, 3.0, at, click * 0.90)       # mostly in the hat stem
    (body, margin), = sidestick_evidence(
        [Hit("snare", at, 47)], kit=kit, snare=snare, hat=hat, sample_rate=sr)
    assert body < SIDESTICK_BODY_SHARE
    assert margin < SIDESTICK_HAT_MARGIN_DB


def test_a_snare_measures_as_body_and_louder_in_the_snare_stem():
    sr, at = 22050, 1.0
    stroke = _burst(sr, 0.08, (200.0, 250.0, 320.0, 400.0))
    kit = _stem(sr, 3.0, at, stroke)
    snare = _stem(sr, 3.0, at, stroke * 0.95)
    hat = _stem(sr, 3.0, at, stroke * 0.05)
    (body, margin), = sidestick_evidence(
        [Hit("snare", at, 110)], kit=kit, snare=snare, hat=hat, sample_rate=sr)
    assert body > SIDESTICK_BODY_SHARE
    assert margin > SIDESTICK_HAT_MARGIN_DB


def test_the_offset_is_where_the_grid_sits_inside_the_recording():
    """Hit times are musical; the stems are audio. Get this wrong and the
    measurement reads whatever happened a second earlier."""
    sr, offset = 22050, 0.692
    click = _burst(sr, 0.08, (2500.0, 4000.0, 6000.0))
    kit = _stem(sr, 4.0, 1.0 + offset, click)
    snare = _stem(sr, 4.0, 1.0 + offset, click * 0.10)
    hat = _stem(sr, 4.0, 1.0 + offset, click * 0.90)
    hits = [Hit("snare", 1.0, 47)]
    aligned, = sidestick_evidence(hits, kit=kit, snare=snare, hat=hat,
                                 sample_rate=sr, offset=offset)
    (misaligned,) = sidestick_evidence(hits, kit=kit, snare=snare, hat=hat,
                                       sample_rate=sr, offset=0.0)
    assert aligned[1] < SIDESTICK_HAT_MARGIN_DB
    assert misaligned[1] >= SIDESTICK_HAT_MARGIN_DB, "silence is not a rim click"


def test_a_hit_past_the_end_of_the_audio_is_unmeasurable_not_a_crash():
    sr = 22050
    silence = np.zeros(sr, dtype=float)
    (body, margin), = sidestick_evidence(
        [Hit("snare", 30.0, 47)], kit=silence, snare=silence, hat=silence,
        sample_rate=sr)
    assert math.isnan(body) and math.isnan(margin)


def test_only_snares_are_measured():
    sr = 22050
    silence = np.zeros(2 * sr, dtype=float)
    evidence = sidestick_evidence(
        [Hit("kick", 1.0, 100), Hit("hihat_closed", 1.0, 70)],
        kit=silence, snare=silence, hat=silence, sample_rate=sr)
    assert len(evidence) == 2
    assert all(math.isnan(b) and math.isnan(m) for b, m in evidence)


# ── and the whole thing, in the pipeline ─────────────────────────────────────


def test_the_pipeline_keeps_a_sidestick_and_drops_its_phantom_hat():
    """Manlio's verse, end to end. Before this, verse-1, verse-2 and verse-3
    each came out with zero snare hits and a v122 closed hat on every
    backbeat — which is what Paolo heard at Reaper bar 8 beats 2 and 4."""
    timeline = Timeline(bpm=60.0)
    hits: list[Hit] = []
    for bar in range(12):
        base = bar * 4.0
        hits.append(Hit("kick", base + 0.0, 100))
        hits.append(Hit("kick", base + 2.0, 100))
        for beat in range(4):
            for third in range(3):
                at = base + beat + third / 3.0
                if third == 0 and beat in (1, 3):
                    hits.append(Hit("sidestick", at, 47))
                    hits.append(Hit("hihat_closed", at + 0.004, 122))
                else:
                    hits.append(Hit("hihat_closed", at, 70))
    hits.sort(key=lambda h: (h.time, h.instrument))
    kept, notes = clean_merged_hits(hits, timeline, subdivision=3)
    assert notes["sidestick_hats"] == 24
    assert sum(1 for h in kept if h.instrument == "sidestick") == 24
    backbeats = [h for h in kept if abs(h.time % 2.0 - 1.0) < 0.05]
    assert {h.instrument for h in backbeats} == {"sidestick"}
    assert max(h.velocity for h in kept) == 100


def test_the_pipeline_reports_the_pass_even_when_it_does_nothing():
    timeline = Timeline(bpm=60.0)
    hits = [Hit("kick", float(i) * 2, 100) for i in range(24)]
    hits += [Hit("snare", float(i) * 2 + 1, 110) for i in range(24)]
    hits.sort(key=lambda h: (h.time, h.instrument))
    _, notes = clean_merged_hits(hits, timeline, subdivision=3)
    assert notes["sidestick_hats"] == 0
