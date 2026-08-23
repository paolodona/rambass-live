"""The CLI must not crash on the characters it prints itself.

Measured, on this machine, before this existed:

    $ rambass sections manlio
    rambass: 'charmap' codec can't encode characters in position 0-1:
             character maps to <undefined>

Every report in `cli.py` opens with `──`, the section tables use `→` and the
prose uses en dashes. On Windows a console that has not been switched to UTF-8
gives Python a `cp1252` stdout, and printing a box-drawing character raises
`UnicodeEncodeError`. So the command produced *nothing* — no report, no exit code
anybody would read as "your terminal codepage" — and the only workaround was to
set `PYTHONIOENCODING=utf-8` before every invocation.

Fixing it in the profile would fix it for one person on one machine. It belongs
here: docs/setup.md promises these commands run on a venue laptop, and a venue
laptop is exactly where nobody is going to be debugging a codepage.
"""

from __future__ import annotations

import io
import sys

import pytest

from rambass import cli


class _Cp1252Stream(io.TextIOWrapper):
    """A stdout like Windows gives you when the codepage is not UTF-8."""

    def __init__(self) -> None:
        self.buffer_bytes = io.BytesIO()
        super().__init__(self.buffer_bytes, encoding="cp1252", newline="")
        self.reconfigured_to: str | None = None

    def reconfigure(self, *, encoding=None, errors=None, **kwargs):  # noqa: D102
        if encoding:
            self.reconfigured_to = encoding
        return None


def test_the_streams_are_switched_to_utf8(monkeypatch):
    stream = _Cp1252Stream()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    cli.use_utf8()
    assert stream.reconfigured_to == "utf-8"


@pytest.mark.parametrize("encoding", ["utf-8", "UTF-8", "utf8"])
def test_a_stream_that_is_already_utf8_is_left_alone(monkeypatch, encoding):
    """Reconfiguring a working stream is a no-op at best and a way to lose
    buffered output at worst, so don't."""

    class Already:
        def __init__(self) -> None:
            self.encoding = encoding
            self.touched = False

        def reconfigure(self, **kwargs):
            self.touched = True

    stream = Already()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    cli.use_utf8()
    assert not stream.touched


def test_a_stream_that_cannot_be_reconfigured_is_not_a_crash(monkeypatch):
    """Redirected to a pipe, captured by pytest, replaced by a test double —
    plenty of things are not a TextIOWrapper. None of them may take the CLI
    down before it has printed anything."""

    class Awkward:
        encoding = "cp1252"

    monkeypatch.setattr(sys, "stdout", Awkward())
    monkeypatch.setattr(sys, "stderr", Awkward())
    cli.use_utf8()          # must simply return


def test_no_streams_at_all_is_not_a_crash(monkeypatch):
    """pythonw, a service, a frozen build: sys.stdout can be None."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    cli.use_utf8()


def test_main_switches_the_encoding_before_running_anything(monkeypatch):
    """Before, not after: the failure was in the *first* line of output."""
    order: list[str] = []
    monkeypatch.setattr(cli, "use_utf8", lambda: order.append("utf8"))
    monkeypatch.setattr(cli, "build_parser", lambda: _StubParser(order))
    cli.main([])
    assert order == ["utf8", "command"]


class _StubParser:
    def __init__(self, order):
        self.order = order

    def parse_args(self, argv):
        import argparse

        return argparse.Namespace(func=lambda args: self.order.append("command") or 0)


@pytest.mark.parametrize("text", ["──", "→", "±", "—", "verse-2 ▸ sidestick"])
def test_say_survives_a_cp1252_stdout(monkeypatch, text):
    """The end-to-end guarantee: whatever the console codepage is, a report that
    starts with a box-drawing character still reaches it."""
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", newline="")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    cli.use_utf8()
    cli._say(text)
    stream.flush()
    assert text.encode("utf-8") in raw.getvalue()
