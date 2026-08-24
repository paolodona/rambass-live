"""Finding ffmpeg, and reporting what the transcription actually produced.

Both are small, and both cost a working session when they are wrong: a laptop at
a venue with ffmpeg installed but not on PATH cannot render anything, and a
summary that silently omits an instrument is read as "the transcriber missed it"
and sends someone hunting for a bug that is not there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rambass.audio import AudioError, ffmpeg_path, ffprobe_path, locate_tool


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


def test_the_install_hint_survives_when_nothing_is_set(monkeypatch, tmp_path):
    """The winget roots are pointed at nothing on purpose: with three routes to
    resolution, "nothing is set" means all three are empty, and on a machine
    that really has the package folder this test would otherwise pass or fail
    depending on whose laptop ran it."""
    monkeypatch.delenv("RAMBASS_FFMPEG", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty"))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "empty"))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    with pytest.raises(AudioError) as caught:
        ffmpeg_path()
    message = str(caught.value)
    assert "winget install Gyan.FFmpeg" in message
    assert "RAMBASS_FFMPEG" in message, "the override is only useful if it is mentioned"


# ── the winget package folder, as a last resort ───────────────────────────────
#
# Belt and braces for the case above: on this machine winget created no shims at
# all (`WinGet\Links` is empty), so whether ffmpeg is findable came down to the
# PATH a process happened to inherit -- and a process older than the PATH edit,
# or one started with a scrubbed environment, inherits nothing. Rather than
# require an environment variable on every machine that will ever run this, look
# in the two places winget actually puts portable packages before giving up.
#
# Last resort on purpose. It runs only when the override is unset and PATH has
# nothing, so it can never override a deliberate choice -- see the two tests
# below that pin that ordering down.


def _winget_bin(root: Path, package: str, build: str, *, scope: str = "user") -> Path:
    r"""The real winget portable layout, which differs between the two scopes.

    `portablePackageUserRoot` defaults to `%LOCALAPPDATA%\Microsoft\WinGet`,
    `portablePackageMachineRoot` to `%PROGRAMFILES%\WinGet` -- no `Microsoft`
    segment in the machine one. A single pattern for both finds nothing on a
    machine-scope install, and the mistake is invisible in a test that builds
    the tree it expects.
    """
    prefix = root / "Microsoft" / "WinGet" if scope == "user" else root / "WinGet"
    binaries = prefix / "Packages" / package / build / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    for name in ("ffmpeg", "ffprobe"):
        binary = binaries / (f"{name}.exe" if _windows() else name)
        binary.write_text("", encoding="utf-8")
        binary.chmod(0o755)
    return binaries


def test_the_winget_package_folder_is_searched_when_nothing_else_has_it(
    monkeypatch, tmp_path,
):
    binaries = _winget_bin(
        tmp_path, "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe",
        "ffmpeg-9.0-full_build",
    )
    monkeypatch.delenv("RAMBASS_FFMPEG", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    assert Path(ffmpeg_path()).parent == binaries
    assert Path(ffprobe_path()).parent == binaries, "ffprobe sits beside it"


def test_the_machine_scope_winget_root_is_searched_too(monkeypatch, tmp_path):
    """`winget install --scope machine` puts portables under Program Files."""
    binaries = _winget_bin(tmp_path / "pf", "Gyan.FFmpeg_x", "ffmpeg-9.0-full_build",
                           scope="machine")
    monkeypatch.delenv("RAMBASS_FFMPEG", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty"))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "pf"))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    assert Path(ffmpeg_path()).parent == binaries


def test_path_beats_the_winget_fallback(monkeypatch, tmp_path):
    """A linked ffmpeg is a decision; a package folder left on disk is not. If
    discovery could outrank PATH, upgrading ffmpeg by hand would silently keep
    running the old winget copy."""
    _winget_bin(tmp_path, "Gyan.FFmpeg_x", "ffmpeg-9.0-full_build")
    monkeypatch.delenv("RAMBASS_FFMPEG", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: f"/usr/bin/{name}")
    assert ffmpeg_path() == "/usr/bin/ffmpeg"


def test_a_wrong_override_is_not_rescued_by_discovery(monkeypatch, tmp_path):
    """Same argument as falling through to PATH: if a typo in the variable is
    quietly papered over, the variable stops meaning anything and the reader is
    never told it is wrong."""
    _winget_bin(tmp_path, "Gyan.FFmpeg_x", "ffmpeg-9.0-full_build")
    monkeypatch.setenv("RAMBASS_FFMPEG", str(tmp_path / "nope"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    with pytest.raises(AudioError) as caught:
        ffmpeg_path()
    assert "RAMBASS_FFMPEG" in str(caught.value)


def test_the_newest_build_wins_when_an_upgrade_left_the_old_one_behind(
    monkeypatch, tmp_path,
):
    """`winget upgrade` does not always remove the previous build directory, and
    the folder name cannot be sorted: "ffmpeg-10.0-full_build" sorts *before*
    "ffmpeg-9.0-full_build", so a lexicographic pick would run last year's
    build for the rest of the decade. Newest mtime, not highest name."""
    old = _winget_bin(tmp_path, "Gyan.FFmpeg_x", "ffmpeg-9.0-full_build")
    new = _winget_bin(tmp_path, "Gyan.FFmpeg_x", "ffmpeg-10.0-full_build")
    suffix = ".exe" if _windows() else ""
    import os as _os

    _os.utime(old / f"ffmpeg{suffix}", (1_000_000, 1_000_000))
    _os.utime(new / f"ffmpeg{suffix}", (2_000_000, 2_000_000))
    monkeypatch.delenv("RAMBASS_FFMPEG", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    assert Path(ffmpeg_path()).parent == new


def test_nothing_anywhere_still_reaches_the_install_hint(monkeypatch, tmp_path):
    monkeypatch.delenv("RAMBASS_FFMPEG", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "empty"))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "empty"))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    with pytest.raises(AudioError) as caught:
        ffmpeg_path()
    assert "winget install Gyan.FFmpeg" in str(caught.value)


def test_the_route_taken_is_reported_so_doctor_can_say_which(monkeypatch, tmp_path):
    """`doctor` prints how ffmpeg was found, and "(via RAMBASS_FFMPEG)" for a
    file discovery found on its own sends the reader to check a variable that is
    not set. So resolution reports its route rather than doctor guessing it."""
    _winget_bin(tmp_path, "Gyan.FFmpeg_x", "ffmpeg-9.0-full_build")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)

    monkeypatch.delenv("RAMBASS_FFMPEG", raising=False)
    assert locate_tool("ffmpeg").route == "winget"

    monkeypatch.setattr("shutil.which", lambda name, *a, **k: f"/usr/bin/{name}")
    assert locate_tool("ffmpeg").route == "path"

    monkeypatch.setenv("RAMBASS_FFMPEG", str(_winget_roots_probe(tmp_path)))
    assert locate_tool("ffmpeg").route == "env"


def _winget_roots_probe(root: Path) -> Path:
    return next((root / "Microsoft" / "WinGet" / "Packages").glob("*/*/bin"))


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
