"""Finding ffmpeg, and reporting what the transcription actually produced.

Both are small, and both cost a working session when they are wrong: a laptop at
a venue with ffmpeg installed but not on PATH cannot render anything, and a
summary that silently omits an instrument is read as "the transcriber missed it"
and sends someone hunting for a bug that is not there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rambass.audio import AudioError, ffmpeg_path, ffprobe_path


# ── RAMBASS_FFMPEG ───────────────────────────────────────────────────────────
#
# Windows installs ffmpeg somewhere like
# %LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_.../bin and does not
# always put it on PATH. Editing the system PATH to fix that is a reboot and a
# support conversation; naming the directory in one environment variable is not.


@pytest.fixture
def fake_ffmpeg(tmp_path: Path) -> Path:
    binary = tmp_path / ("ffmpeg.exe" if _windows() else "ffmpeg")
    binary.write_text("", encoding="utf-8")
    binary.chmod(0o755)
    probe = tmp_path / ("ffprobe.exe" if _windows() else "ffprobe")
    probe.write_text("", encoding="utf-8")
    probe.chmod(0o755)
    return binary


def _windows() -> bool:
    import sys

    return sys.platform == "win32"


def test_a_directory_in_rambass_ffmpeg_is_searched(monkeypatch, fake_ffmpeg):
    monkeypatch.setenv("RAMBASS_FFMPEG", str(fake_ffmpeg.parent))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    assert Path(ffmpeg_path()) == fake_ffmpeg
    assert Path(ffprobe_path()).stem == "ffprobe"


def test_the_binary_itself_in_rambass_ffmpeg_works_too(monkeypatch, fake_ffmpeg):
    """Both are natural things to paste in, so both are accepted."""
    monkeypatch.setenv("RAMBASS_FFMPEG", str(fake_ffmpeg))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    assert Path(ffmpeg_path()) == fake_ffmpeg
    assert Path(ffprobe_path()).stem == "ffprobe"


def test_path_still_wins_when_there_is_no_override(monkeypatch):
    monkeypatch.delenv("RAMBASS_FFMPEG", raising=False)
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: f"/usr/bin/{name}")
    assert ffmpeg_path() == "/usr/bin/ffmpeg"


def test_a_wrong_override_says_so_instead_of_falling_through(monkeypatch, tmp_path):
    """Silently ignoring it is the worst outcome: the message then blames PATH
    for a typo in the variable, and the reader checks the wrong thing."""
    monkeypatch.setenv("RAMBASS_FFMPEG", str(tmp_path / "nope"))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    with pytest.raises(AudioError) as caught:
        ffmpeg_path()
    assert "RAMBASS_FFMPEG" in str(caught.value)


def test_the_install_hint_survives_when_nothing_is_set(monkeypatch):
    monkeypatch.delenv("RAMBASS_FFMPEG", raising=False)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    with pytest.raises(AudioError) as caught:
        ffmpeg_path()
    message = str(caught.value)
    assert "winget install Gyan.FFmpeg" in message
    assert "RAMBASS_FFMPEG" in message, "the override is only useful if it is mentioned"


# ── the report counts what is in the part, not what a band produced ──────────
#
# Measured on Manlio: the summary listed 6 instruments and 827 hits when the
# part held 7 instruments and 1122, because split_cymbal_runs creates
# `hihat_open` and nothing else does — so `hihat_open` never had a key in
# per_instrument and the refresh loop, which iterated over the existing keys,
# never counted its 295 hits. Paolo read that summary, saw no open hats at all,
# and reasonably concluded the transcriber had missed the cymbals.


def test_the_summary_counts_instruments_no_band_ever_produced():
    from rambass.midiio import Hit
    from rambass.transcribe import TranscriptionReport, recount

    report = TranscriptionReport(per_instrument={"hihat_closed": 9, "crash": 4})
    hits = ([Hit("hihat_closed", float(i), 70) for i in range(3)]
            + [Hit("hihat_open", float(i) + 0.5, 80) for i in range(5)])
    recount(report, hits)
    assert report.per_instrument == {"hihat_closed": 3, "hihat_open": 5}


def test_an_instrument_that_lost_every_hit_leaves_the_table():
    from rambass.midiio import Hit
    from rambass.transcribe import TranscriptionReport, recount

    report = TranscriptionReport(per_instrument={"crash": 300, "kick": 2})
    recount(report, [Hit("kick", 0.0, 100), Hit("kick", 1.0, 100)])
    assert report.per_instrument == {"kick": 2}


def test_recounting_an_empty_part_is_not_a_crash():
    from rambass.transcribe import TranscriptionReport, recount

    report = TranscriptionReport(per_instrument={"kick": 5})
    recount(report, [])
    assert report.per_instrument == {}
