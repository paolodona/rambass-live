"""An install hint has to name an installer that exists on this machine.

This checkout's ``.venv`` was created by ``uv venv``, which seeds no pip. So
every ``install it with:  pip install -e '.[lyrics]'`` in the code was printing
a command that dies with "The term 'pip' is not recognized" -- and a hint that
sends the reader to install the tool they already have, by a route that does not
exist, costs the same session as no hint at all. Same rule as ffmpeg: resolve
what is actually here, and name the route.
"""

from __future__ import annotations

import pytest

from rambass import doctor
from rambass.audio import AudioError, install_alternatives, install_hint, require_module


def only(*tools):
    """A ``shutil.which`` that finds exactly *tools*."""
    return lambda name, *a, **k: f"/usr/bin/{name}" if name in tools else None


@pytest.fixture
def uv_only(monkeypatch):
    """This machine: uv on PATH, no pip anywhere."""
    monkeypatch.setattr("shutil.which", only("uv"))
    monkeypatch.setattr("rambass.audio._pip_module_present", lambda: False)


def test_a_uv_created_venv_is_told_to_use_uv(uv_only):
    assert install_hint("lyrics") == "uv pip install -e '.[lyrics]'"


def test_pip_on_path_is_preferred_and_uv_offered_as_the_faster_alternative(monkeypatch):
    """When pip works, print pip: it is what the docs say and what most readers
    expect. uv is then an aside, not the instruction."""
    monkeypatch.setattr("shutil.which", only("pip", "uv"))
    monkeypatch.setattr("rambass.audio._pip_module_present", lambda: True)
    assert install_hint("audio") == "pip install -e '.[audio]'"
    assert install_alternatives("audio") == ["uv pip install -e '.[audio]'"]


def test_pip_importable_but_not_on_path_goes_through_the_interpreter(monkeypatch):
    """An unactivated venv has the module and no console script. `pip install`
    then reaches for whatever pip is on the system, or nothing."""
    monkeypatch.setattr("shutil.which", only())
    monkeypatch.setattr("rambass.audio._pip_module_present", lambda: True)
    assert install_hint("audio") == "python -m pip install -e '.[audio]'"


def test_the_core_tier_installs_the_package_itself(uv_only):
    """There is no '[core]' extra, so naming one is a command that fails."""
    assert install_hint("core") == "uv pip install -e ."
    assert install_hint() == "uv pip install -e ."


def test_no_installer_at_all_says_how_to_get_one(monkeypatch):
    monkeypatch.setattr("shutil.which", only())
    monkeypatch.setattr("rambass.audio._pip_module_present", lambda: False)
    hint = install_hint("audio")
    assert "ensurepip" in hint, "with no installer, the next step is to get one"
    assert "install -e '.[audio]'" in hint


def test_require_module_quotes_the_installer_that_exists(uv_only):
    with pytest.raises(AudioError) as caught:
        require_module("nonexistent_module_xyz", "audio")
    message = str(caught.value)
    assert "install it with:  uv pip install -e '.[audio]'" in message
    assert "  pip install -e" not in message, "the broken route must not be the instruction"


def test_doctor_fixes_name_the_working_installer(uv_only, monkeypatch):
    monkeypatch.setattr(doctor, "_locate", lambda tool: None)
    fixes = {c.name: c.fix for c in doctor.run_checks()}
    assert fixes["librosa"] == "uv pip install -e '.[audio]'"
    assert fixes["faster_whisper"] == "uv pip install -e '.[lyrics]'"
    assert fixes["yaml"] == "uv pip install -e ."


def test_doctor_reports_which_installer_it_found(uv_only, monkeypatch):
    """The same reason ffmpeg's route is printed: "use pip" is unactionable on a
    venv that has none, and the reader cannot tell that from the outside."""
    monkeypatch.setattr(doctor, "_locate", lambda tool: None)
    check = next(c for c in doctor.run_checks() if c.name == "installer")
    assert check.ok
    assert "uv pip" in check.detail
