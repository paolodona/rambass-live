"""Tempo refinement and wander, exercised without librosa.

Both live in `analyze.py`, which is the librosa tier — but the maths itself is
plain numpy over an onset envelope, and that is deliberate: it is the part that
decides where every drum hit lands, so it should be testable on a laptop with
the core install.
"""

import numpy as np
import pytest

from rambass.analyze import TempoAnalysis, pulse_wander, refine_tempo

FRAME = 0.005  # seconds per envelope frame, about what librosa gives at hop 256


def envelope(beat_times, *, duration, width=0.012):
    """A synthetic onset envelope: a narrow bump at each beat."""
    times = np.arange(0.0, duration, FRAME)
    env = np.zeros_like(times)
    for beat in beat_times:
        env += np.exp(-0.5 * ((times - beat) / width) ** 2)
    return env, times


def even_beats(bpm, duration, *, first=0.25, shift_after=None, shift=0.0):
    period = 60.0 / bpm
    out = []
    t = first
    while t < duration:
        out.append(t + (shift if shift_after is not None and t >= shift_after else 0.0))
        t += period
    return out


def test_refine_tempo_beats_the_bin_grid_it_was_given():
    """The whole point: a coarse estimate a bin out is pulled back to the truth.

    117.45 for a 116 BPM song is exactly the failure this exists for — that is a
    tempogram bin centre, and it is 1.2% wrong, which is three seconds of slip
    across a five-minute song.
    """
    truth = 116.02
    env, times = envelope(even_beats(truth, 240.0), duration=240.0)
    assert refine_tempo(env, times, 117.45) == pytest.approx(truth, abs=0.02)


def test_refine_tempo_does_not_wander_off_to_half_or_double_time():
    truth = 132.0
    env, times = envelope(even_beats(truth, 180.0), duration=180.0)
    assert refine_tempo(env, times, 130.0) == pytest.approx(truth, abs=0.02)


def test_refine_tempo_leaves_a_hopeless_input_alone():
    env, times = envelope([0.5, 1.0], duration=1.5)
    assert refine_tempo(env, times, 120.0) == 120.0


def test_a_click_locked_take_reads_as_no_wander():
    env, times = envelope(even_beats(120.0, 200.0), duration=200.0)
    offsets = [ms for _, ms in pulse_wander(env, times, 120.0)]
    assert offsets, "expected several windows"
    assert max(abs(ms) for ms in offsets) < 3.0


def test_wander_finds_a_band_that_moved_mid_song():
    """Second half played 40 ms behind: mean removed, that reads as +-20 ms."""
    beats = even_beats(120.0, 200.0, shift_after=100.0, shift=0.040)
    env, times = envelope(beats, duration=200.0)
    measured = pulse_wander(env, times, 120.0)
    early = [ms for t, ms in measured if t < 100.0]
    late = [ms for t, ms in measured if t > 100.0]
    assert np.mean(late) - np.mean(early) == pytest.approx(40.0, abs=6.0)


def test_wander_needs_something_to_measure():
    env, times = envelope([0.5], duration=1.0)
    assert pulse_wander(env, times, 120.0) == []


# ── what the report makes of those numbers ───────────────────────────────
def test_the_budget_is_half_a_sixteenth():
    analysis = TempoAnalysis(bpm=120.0, bpm_rounded=120.0)
    # a sixteenth at 120 BPM is 125 ms, so a hit snaps wrong past 62.5 ms
    assert analysis.subdivision_budget_ms == pytest.approx(62.5)


def test_steadiness_is_measured_against_that_budget():
    tight = TempoAnalysis(bpm=120.0, bpm_rounded=120.0,
                          wander=[(10.0, -20.0), (30.0, 20.0)])
    loose = TempoAnalysis(bpm=120.0, bpm_rounded=120.0,
                          wander=[(10.0, -40.0), (30.0, 40.0)])
    assert tight.steady
    assert not loose.steady
    assert loose.wander_pp == pytest.approx(80.0)


def test_bars_are_counted_from_the_first_downbeat():
    """Not from zero: an intro's worth of silence is not a bar of music."""
    analysis = TempoAnalysis(bpm=120.0, bpm_rounded=120.0,
                             downbeat_time=2.0, duration=2.0 + 16 * 2.0)
    assert analysis.estimated_bars == 16


def test_summary_states_the_verdict_without_offering_a_choice():
    analysis = TempoAnalysis(bpm=116.02, bpm_rounded=116.02, duration=300.0,
                             wander=[(10.0, -5.0), (30.0, 5.0)])
    text = analysis.summary()
    assert "116.02" in text
    assert "tempo map" not in text
    assert "holds the fixed grid" in text


def test_wander_never_reports_more_than_half_a_beat():
    """Phase is wrapped, not unwrapped, on purpose.

    An unwrapped phase can invent a whole-beat jump out of one noisy window, and
    "the band was a beat late for twenty seconds" is a measurement artifact, not
    a thing that happened. Half a beat at 120 BPM is 250 ms.
    """
    rng = np.random.default_rng(7)
    beats = []
    t = 0.3
    while t < 300.0:
        beats.append(t + rng.normal(0, 0.02))
        t += 0.5
    env, times = envelope(beats, duration=300.0)
    offsets = [ms for _, ms in pulse_wander(env, times, 120.0, window=10.0)]
    assert offsets
    assert max(abs(ms) for ms in offsets) <= 250.0


# ── following the take (transcribe.dewander is the same tier of maths) ────
def test_dewander_pulls_hits_onto_the_grid_the_band_played():
    from rambass.transcribe import dewander

    # the band sat 40 ms behind early on and 20 ms ahead later
    wander = [(10.0, 40.0), (30.0, -20.0)]
    corrected = dewander([10.0, 30.0], wander)
    assert corrected[0] == pytest.approx(10.0 - 0.040)
    assert corrected[1] == pytest.approx(30.0 + 0.020)


def test_dewander_interpolates_between_windows():
    from rambass.transcribe import dewander

    assert dewander([20.0], [(10.0, 40.0), (30.0, 0.0)])[0] == pytest.approx(20.0 - 0.020)


def test_dewander_with_nothing_measured_changes_nothing():
    from rambass.transcribe import dewander

    assert list(dewander([1.0, 2.0], [])) == [1.0, 2.0]


def test_best_anchor_shift_finds_a_late_anchor():
    """A whole song read 30 ms late is the failure this fixes."""
    from rambass.transcribe import best_anchor_shift

    sixteenth = 60.0 / 116.0 / 4
    truth = np.arange(0, 200) * sixteenth
    late = truth + 0.030
    assert best_anchor_shift(late, sixteenth) == pytest.approx(-0.030, abs=0.003)


def test_best_anchor_shift_cannot_move_a_hit_to_another_note():
    """Bounded to +-half a subdivision by construction — the safety property."""
    from rambass.transcribe import best_anchor_shift

    rng = np.random.default_rng(3)
    sixteenth = 0.1293
    for _ in range(5):
        times = np.sort(rng.uniform(0, 300, 400))
        assert abs(best_anchor_shift(times, sixteenth)) <= sixteenth / 2


def test_best_anchor_shift_leaves_an_aligned_take_alone():
    from rambass.transcribe import best_anchor_shift

    sixteenth = 60.0 / 116.0 / 4
    times = np.arange(0, 200) * sixteenth
    assert abs(best_anchor_shift(times, sixteenth)) < 0.003


# ── the whole-subdivision hole that best_anchor_shift cannot see ─────────
def _grid_hits(beat, bars=32, shift=0.0):
    """A plain backbeat: kick on 1 and 3, snare on 2 and 4, hats on 8ths."""
    from rambass.midiio import Hit

    out = []
    for b in range(bars):
        base = b * 4 * beat + shift
        out += [Hit("kick", base, 100), Hit("kick", base + 2 * beat, 100),
                Hit("snare", base + beat, 100), Hit("snare", base + 3 * beat, 100)]
        for e in range(8):
            out.append(Hit("hihat_closed", base + e * beat / 2, 80))
    return out


def test_parity_shift_finds_a_part_displaced_by_a_whole_sixteenth():
    """Measured on Manlio: the kick was playing the "e" of every beat."""
    from rambass.transcribe import beat_parity_shift

    beat = 1.0
    displaced = _grid_hits(beat, shift=-beat / 4)   # everything a 16th early
    assert beat_parity_shift(displaced, beat) == pytest.approx(beat / 4, abs=1e-9)


def test_parity_shift_leaves_a_correct_anchor_alone():
    from rambass.transcribe import beat_parity_shift

    assert beat_parity_shift(_grid_hits(1.0), 1.0) == 0.0


def test_parity_shift_needs_enough_evidence():
    """Four hits are not a backbeat; refuse rather than shove the song around."""
    from rambass.midiio import Hit
    from rambass.transcribe import beat_parity_shift

    assert beat_parity_shift([Hit("kick", 0.25, 100)] * 4, 1.0) == 0.0


def test_parity_shift_ignores_the_cymbals_it_cannot_reason_about():
    """Only kick and snare vote — a hat on every 16th says nothing about beat 1."""
    from rambass.midiio import Hit
    from rambass.transcribe import beat_parity_shift

    hats = [Hit("hihat_closed", i * 0.25, 80) for i in range(200)]
    assert beat_parity_shift(hats, 1.0) == 0.0


def test_parity_advice_reports_a_near_miss_instead_of_acting():
    """A suggestive-but-not-conclusive case must reach the human, not the MIDI."""
    from rambass.midiio import Hit
    from rambass.transcribe import parity_advice

    beat = 1.0
    hits = []
    for b in range(40):          # two thirds on the beat, one third on the "e"
        base = b * beat
        hits.append(Hit("kick", base + (beat / 4 if b % 3 else 0.0), 100))
        hits.append(Hit("snare", base + (beat / 4 if b % 3 else 0.0), 100))
    shift, note = parity_advice(hits, beat)
    assert shift == 0.0
    assert "check by ear" in note


def test_parity_advice_is_silent_when_it_acts():
    from rambass.transcribe import parity_advice

    shift, note = parity_advice(_grid_hits(1.0, shift=-0.25), 1.0)
    assert shift == pytest.approx(0.25)
    assert note == ""
