"""The headless-render spike: drums MIDI through a VST3 instrument.

docs/review-ui.md Phase 4 / Gate R2. The plugin itself is not in this suite —
same rule `stems.py`'s demucs call already follows — so what is pinned here is
everything around it: which clock the rendered wav sits on, how the plugin is
located, and that a missing extra explains itself. The one test that needs the
real plugin skips unless it is installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rambass.midiio import DrumPerformance, Hit, write_drum_midi


def _midi(song, tmp_path, hits):
    timeline = song.timeline()
    path = tmp_path / "drums.mid"
    write_drum_midi(path, DrumPerformance(
        [Hit(name, timeline.bar_beat_to_seconds(bar, beat), velocity)
         for name, bar, beat, velocity in hits], timeline))
    return path


# ── the clock: a rendered candidate is anchored at musical bar 1 ─────────────


def test_the_render_starts_at_musical_bar_1_not_at_the_first_sample(song, tmp_path):
    """`clip_spans` cuts candidate clips with `bar_beat_to_seconds`, so the
    wav this produces must put musical bar 1 beat 1 at sample 0 — no count-in.

    This is CLAUDE.md's two clocks through the one door that would be hardest
    to spot: a rendered candidate carrying the count-in looks perfectly fine
    on its own and is wrong by exactly `count_in.bars` against every reference
    clip. `write_drum_midi` already anchors tick 0 at bar 1, so the job here is
    to not add anything back."""
    from rambass.ezrender import midi_messages

    song.count_in_bars = 4
    path = _midi(song, tmp_path, [("kick", 1, 1.0, 100), ("snare", 3, 1.0, 90)])
    messages = midi_messages(path)

    ons = [m for m in messages if m.type == "note_on"]
    assert ons[0].time == pytest.approx(0.0, abs=1e-6)
    # bar 3 beat 1 at 120bpm 4/4 = 4.0s from the musical zero.
    assert ons[1].time == pytest.approx(4.0, abs=1e-3)


def test_editing_the_count_in_does_not_move_the_render(song, tmp_path):
    """The same guard `test_a_candidate_clip_is_not_moved_by_the_count_in`
    puts on the clip boundaries, on the audio that boundary indexes into."""
    from rambass.ezrender import midi_messages

    path = _midi(song, tmp_path, [("kick", 5, 1.0, 100)])
    song.count_in_bars = 2
    before = [m.time for m in midi_messages(path) if m.type == "note_on"]
    song.count_in_bars = 8
    after = [m.time for m in midi_messages(path) if m.type == "note_on"]
    assert before == after


def test_every_note_on_gets_a_note_off_and_keeps_its_velocity(song, tmp_path):
    """A drum sample is one-shot, but a VST3 that never sees note_off can hold
    a voice per hit and run out of them part-way through a five-minute song."""
    from rambass.ezrender import midi_messages

    path = _midi(song, tmp_path, [("kick", 1, 1.0, 100), ("snare", 1, 3.0, 77)])
    messages = midi_messages(path)

    ons = [m for m in messages if m.type == "note_on"]
    offs = [m for m in messages if m.type == "note_off"]
    assert len(ons) == len(offs) == 2
    assert [m.velocity for m in ons] == [100, 77]
    assert all(off.time > on.time for on, off in zip(ons, offs, strict=True))


def test_the_render_runs_past_the_last_hit_so_the_cymbal_can_ring(song, tmp_path):
    """A crash on the last beat decays for seconds. Cutting the render at the
    last note_on truncates it, and the final section's clip is the one that
    ends up sounding wrong."""
    from rambass.ezrender import midi_messages, render_duration

    path = _midi(song, tmp_path, [("crash", 8, 1.0, 110)])
    messages = midi_messages(path)
    assert render_duration(messages, tail=4.0) == pytest.approx(14.0 + 4.0, abs=0.1)


# ── locating the plugin: the RAMBASS_FFMPEG rule, applied again ─────────────


def test_the_override_wins_and_a_wrong_one_is_an_error(tmp_path, monkeypatch):
    """Same reasoning as `locate_tool`: a set-but-wrong override must not fall
    through to discovery, or a typo in the variable reports "EZdrummer is not
    installed" and sends the reader to check the one thing that was fine."""
    from rambass.audio import AudioError
    from rambass.ezrender import PLUGIN_ENV, locate_plugin

    good = tmp_path / "EZdrummer 3.vst3"
    good.mkdir()
    monkeypatch.setenv(PLUGIN_ENV, str(good))
    assert locate_plugin() == good

    monkeypatch.setenv(PLUGIN_ENV, str(tmp_path / "nope.vst3"))
    with pytest.raises(AudioError) as caught:
        locate_plugin()
    assert PLUGIN_ENV in str(caught.value)


def test_a_directory_override_is_searched_for_a_vst3(tmp_path, monkeypatch):
    from rambass.ezrender import PLUGIN_ENV, locate_plugin

    (tmp_path / "EZdrummer 3.vst3").mkdir()
    monkeypatch.setenv(PLUGIN_ENV, str(tmp_path))
    assert locate_plugin("ezdrummer").name == "EZdrummer 3.vst3"


def test_a_missing_plugin_names_the_variable_to_set(tmp_path, monkeypatch):
    from rambass.audio import AudioError
    from rambass.ezrender import PLUGIN_ENV, locate_plugin

    monkeypatch.delenv(PLUGIN_ENV, raising=False)
    monkeypatch.setattr("rambass.ezrender.VST3_DIRS", (tmp_path,))
    with pytest.raises(AudioError) as caught:
        locate_plugin("nothing-like-this")
    assert PLUGIN_ENV in str(caught.value)


def test_without_pedalboard_the_hint_names_the_extra(monkeypatch):
    """The `audio.require_module` contract: an install line that resolves the
    installer that exists here, not a `pip` this venv does not have."""
    from rambass.audio import AudioError
    from rambass.ezrender import load_instrument

    monkeypatch.setitem(__import__("sys").modules, "pedalboard", None)
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pedalboard":
            raise ImportError("no pedalboard")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    with pytest.raises(AudioError) as caught:
        load_instrument(Path("x.vst3"))
    assert "pedalboard" in str(caught.value)


# ── the spike's own gate: the real plugin, when it is here ──────────────────


def _real_plugin():
    try:
        from rambass.ezrender import locate_plugin

        return locate_plugin("ezdrummer")
    except Exception:
        return None


@pytest.mark.skipif(_real_plugin() is None,
                    reason="EZdrummer 3's VST3 is not installed here")
def test_the_plugin_renders_audible_drums_headlessly(song, tmp_path):
    """Gate R2. Measured on Paolo's machine 2026-08-24: EZdrummer 3.vst3 loads
    headlessly in ~12s with no licence dialog, and a fresh instance already
    has a kit, rendering four GM notes at peak 0.70. Neither was safe to
    assume — the spike existed because the activation path might have blocked
    a headless instantiation entirely."""
    pytest.importorskip("pedalboard")
    import numpy as np

    from rambass.ezrender import load_instrument, midi_messages, render_midi

    path = _midi(song, tmp_path, [
        ("kick", 1, 1.0, 100), ("snare", 1, 2.0, 105),
        ("hihat_closed", 1, 3.0, 80), ("crash", 1, 4.0, 110)])
    plugin = load_instrument(_real_plugin())
    audio = render_midi(midi_messages(path), plugin, sample_rate=44100.0,
                        tail=1.0)
    assert audio.shape[0] == 2
    assert np.abs(audio).max() > 0.01, "the plugin rendered silence"


# ── the CLI: `rambass review render` ────────────────────────────────────────


def test_render_writes_the_candidate_the_review_tool_reads(cwd_song, monkeypatch,
                                                           tmp_path):
    """qa/candidate.wav, which is exactly what `clip_sources` looks for --
    and never render/, which practice-tracks.md reserves for the gig base."""
    import numpy as np

    from rambass import ezrender
    from rambass.cli import main
    from rambass.review import clip_sources

    _midi(cwd_song, cwd_song.directory / "midi", [("kick", 1, 1.0, 100)])
    (cwd_song.directory / "midi" / "drums.mid").rename(
        cwd_song.drum_midi_path("quantized"))

    monkeypatch.setattr(ezrender, "locate_plugin", lambda *a, **k: tmp_path)
    monkeypatch.setattr(ezrender, "load_instrument", lambda *a, **k: object())
    monkeypatch.setattr(
        ezrender, "render_midi",
        lambda *a, **k: np.zeros((2, 4410), dtype=np.float32))

    assert main(["review", "render", cwd_song.slug]) == 0
    assert cwd_song.path("qa", "candidate.wav").is_file()
    assert not cwd_song.path("render", f"{cwd_song.slug}.wav").exists()
    assert clip_sources(cwd_song)["cand"].available


def test_render_without_drum_midi_names_the_command_that_makes_it(cwd_song,
                                                                  capsys):
    from rambass.cli import main

    assert main(["review", "render", cwd_song.slug]) == 2
    assert "rambass drums clean" in capsys.readouterr().err


def test_doctor_reports_the_vst_extra(monkeypatch):
    """A spike that only ever works on one machine is a spike nobody can
    reproduce. `doctor` is where "is this machine set up" is answered, and it
    has to name the plug-in as well as the package: `pedalboard` installed
    with no VST3 found renders nothing and says nothing about why."""
    from rambass.doctor import run_checks

    names = {check.name: check for check in run_checks()}
    assert "pedalboard" in names
    assert "VST3 instrument" in names
