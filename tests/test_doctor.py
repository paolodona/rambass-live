"""``rambass doctor`` has to agree with the code that actually runs the tools.

Found while verifying the shell profile: with `RAMBASS_FFMPEG` set and every
audio command working, `doctor` still reported

    [MISS] ffmpeg          not on PATH
           fix: brew install ffmpeg  (macOS)  ·  apt install ffmpeg  (Linux)

because it asked `shutil.which` directly while `audio.load_mono` asks
`audio.ffmpeg_path`, which honours the override. A pre-flight check that
disagrees with the thing it is checking is worse than no check: it sends you to
install something you have, and next time it says something is missing you will
not believe it.
"""

from __future__ import annotations

from rambass import doctor


def _check(checks, name):
    return next(item for item in checks if item.name == name)


def test_ffmpeg_found_only_through_the_override_still_reads_as_ok(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: None)
    monkeypatch.setattr(doctor, "_tool_path",
                        lambda name: f"/somewhere/{name}")
    monkeypatch.setattr(doctor, "_version", lambda cmd: "ffmpeg version 9.0")
    checks = doctor.run_checks()
    assert _check(checks, "ffmpeg").ok
    assert "9.0" in _check(checks, "ffmpeg").detail


def test_the_detail_says_where_it_was_found(monkeypatch):
    """So that "it works here but not in my other shell" is one line to diagnose."""
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: None)
    monkeypatch.setattr(doctor, "_tool_path", lambda name: rf"C:\ff\bin\{name}.exe")
    monkeypatch.setattr(doctor, "_version", lambda cmd: "")
    detail = _check(doctor.run_checks(), "ffmpeg").detail
    assert "C:\\ff\\bin\\ffmpeg.exe" in detail


def test_genuinely_missing_ffmpeg_still_fails(monkeypatch):
    from rambass.audio import AudioError

    monkeypatch.setattr("shutil.which", lambda name, *a, **k: None)

    def absent(name):
        raise AudioError("nope")

    monkeypatch.setattr(doctor, "_tool_path", absent)
    check = _check(doctor.run_checks(), "ffmpeg")
    assert not check.ok
    assert "not found" in check.detail.lower()


def test_the_fix_mentions_the_override(monkeypatch):
    """The commonest cause on Windows is not "not installed" but "installed and
    not linked", and the fix for that is an environment variable."""
    from rambass.audio import AudioError

    monkeypatch.setattr("shutil.which", lambda name, *a, **k: None)
    monkeypatch.setattr(doctor, "_tool_path",
                        lambda name: (_ for _ in ()).throw(AudioError("nope")))
    assert "RAMBASS_FFMPEG" in _check(doctor.run_checks(), "ffmpeg").fix


def test_the_version_probe_uses_the_resolved_path(monkeypatch):
    """Not the bare name: if it is not on PATH, `ffmpeg -version` cannot run."""
    seen: list[list[str]] = []
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: None)
    monkeypatch.setattr(doctor, "_tool_path", lambda name: rf"C:\ff\{name}.exe")
    monkeypatch.setattr(doctor, "_version", lambda cmd: seen.append(cmd) or "v")
    doctor.run_checks()
    assert [rf"C:\ff\ffmpeg.exe", "-version"] in seen
