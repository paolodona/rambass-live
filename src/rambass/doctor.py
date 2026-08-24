"""``rambass doctor`` — check the toolchain before a work session."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from dataclasses import dataclass

from .audio import AudioError, FFMPEG_ENV, locate_tool as _locate


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    needed_for: str
    fix: str = ""


def _version(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    text = (result.stdout or result.stderr or "").strip()
    return text.splitlines()[0] if text else ""


def _module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def run_checks() -> list[Check]:
    checks: list[Check] = []

    checks.append(Check(
        "python", sys.version_info >= (3, 10),
        f"{sys.version.split()[0]} at {sys.executable}",
        "everything", "install Python 3.10 or newer",
    ))

    # Through audio.locate_tool, not shutil.which, so this agrees with the code
    # that actually runs the tools. Asked directly, `which` says "not on PATH"
    # for an ffmpeg that RAMBASS_FFMPEG has already located and that every audio
    # command is happily using -- and a pre-flight check which disagrees with the
    # thing it is checking is worse than no check. The route comes back from the
    # resolver for the same reason: doctor guessing it printed "(via
    # RAMBASS_FFMPEG)" for anything `which` could not see, which would now be a
    # lie for a winget discovery and send the reader to inspect an unset variable.
    routes = {
        "env": f"  (via {FFMPEG_ENV})",
        "winget": f"  (found in the winget package folder; set {FFMPEG_ENV} to pin it)",
        "path": "",
    }
    for tool, needed in (("ffmpeg", "decoding audio, rendering video"),
                         ("ffprobe", "reading durations")):
        try:
            location = _locate(tool)
        except AudioError:
            location = None
        if location is not None:
            version = _version([location.path, "-version"])
            where = routes.get(location.route, "")
            detail = f"{version or location.path}{where}"
        else:
            detail = "not found on PATH"
        checks.append(Check(
            tool, location is not None, detail, needed,
            "brew install ffmpeg  (macOS)  ·  apt install ffmpeg  (Linux)  ·  "
            f"winget install Gyan.FFmpeg  (Windows).  Already installed but not "
            f"linked? Set {FFMPEG_ENV} to the folder holding it.",
        ))

    for module, needed, extra in (
        ("yaml", "reading song.yaml", "core"),
        ("mido", "reading and writing MIDI", "core"),
        ("numpy", "all audio maths", "core"),
        ("librosa", "tempo detection and drum transcription", "audio"),
        ("scipy", "librosa's DSP", "audio"),
        ("faster_whisper", "drafting lyric cues from audio", "lyrics"),
        ("demucs", "separating drums out of a stereo mix", "separate"),
    ):
        present = _module(module)
        checks.append(Check(
            module, present,
            "installed" if present else "missing",
            needed,
            f"pip install -e '.[{extra}]'" if extra != "core" else "pip install -e .",
        ))

    torch_note = ""
    if _module("torch"):
        try:
            import torch  # noqa: PLC0415 - probing on purpose

            if torch.backends.mps.is_available():
                torch_note = "Apple Silicon GPU (mps) available — use `--device mps`"
            elif torch.cuda.is_available():
                torch_note = "CUDA available — use `--device cuda`"
            else:
                torch_note = "CPU only — separation will be slow but works"
        except Exception as exc:  # noqa: BLE001 - diagnostics must not crash
            torch_note = f"torch present but unusable: {exc}"
        checks.append(Check("torch", True, torch_note, "demucs acceleration"))

    return checks


def report() -> tuple[str, bool]:
    """Formatted report and whether the core toolchain is usable."""
    checks = run_checks()
    width = max(len(c.name) for c in checks)
    lines = ["rambass doctor", ""]
    core_ok = True
    for check in checks:
        mark = "ok  " if check.ok else "MISS"
        lines.append(f"[{mark}] {check.name.ljust(width)}  {check.detail}")
        if not check.ok:
            lines.append(f"{'':>7} needed for: {check.needed_for}")
            if check.fix:
                lines.append(f"{'':>7} fix: {check.fix}")
            if check.name in ("python", "yaml", "mido", "numpy"):
                core_ok = False
    lines += [
        "",
        "core commands (list, show, check, quantize, click, reaper, gx100, lyrics)",
        "work with the base install. analyze / drums transcribe need [audio];",
        "lyrics transcribe needs [lyrics]; stems needs [separate]; rendering video",
        "and reading MP3s needs ffmpeg.",
    ]
    return "\n".join(lines), core_ok
