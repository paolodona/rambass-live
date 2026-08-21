"""``rambass doctor`` — check the toolchain before a work session."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass


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

    for tool, needed in (("ffmpeg", "decoding audio, rendering video"),
                         ("ffprobe", "reading durations")):
        path = shutil.which(tool)
        checks.append(Check(
            tool, bool(path),
            _version([tool, "-version"]) if path else "not on PATH",
            needed,
            "brew install ffmpeg  (macOS)  ·  apt install ffmpeg  (Linux)",
        ))

    for module, needed, extra in (
        ("yaml", "reading song.yaml", "core"),
        ("mido", "reading and writing MIDI", "core"),
        ("numpy", "all audio maths", "core"),
        ("librosa", "tempo detection and drum transcription", "audio"),
        ("scipy", "librosa's DSP", "audio"),
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
        "core commands (list, show, check, quantize, click, reaper, gx100, video ass)",
        "work with the base install. analyze / transcribe need [audio]; stems needs",
        "[separate]; rendering video and reading MP3s needs ffmpeg.",
    ]
    return "\n".join(lines), core_ok
