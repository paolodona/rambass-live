"""Stem separation with Demucs.

Only needed for *Tutti in Fila*, where the original multitracks are gone and the
drums have to be pulled back out of the stereo mix. *Diversamente Giovani* has
real drum tracks, so it skips this step entirely.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .audio import AudioError, install_hint

#: Demucs models, best-sounding first. htdemucs_ft is noticeably cleaner on
#: cymbals than plain htdemucs but takes roughly four times as long.
MODELS = ("htdemucs_ft", "htdemucs", "mdx_extra", "htdemucs_6s")

STEM_NAMES = ("drums", "bass", "other", "vocals")


@dataclass
class SeparationResult:
    stems: dict[str, Path]
    model: str
    command: list[str]


def demucs_available() -> bool:
    if shutil.which("demucs"):
        return True
    probe = subprocess.run(
        [sys.executable, "-c", "import demucs"], capture_output=True, check=False
    )
    return probe.returncode == 0


def separate(
    source: str | Path,
    out_dir: str | Path,
    *,
    model: str = "htdemucs_ft",
    two_stems: str | None = None,
    device: str | None = None,
    shifts: int = 1,
    overlap: float = 0.25,
    jobs: int = 1,
    quiet: bool = False,
) -> SeparationResult:
    """Run Demucs on *source*, flattening the output into *out_dir*.

    Demucs writes ``<out>/<model>/<track name>/drums.wav``; we move the files up
    to ``<out>/drums.wav`` so that every song folder looks the same regardless of
    which model produced it.

    *two_stems="drums"* is much faster and is all we need when the only goal is
    the drum part — it gives ``drums.wav`` plus a ``no_drums.wav`` backing bed,
    and that second file is genuinely useful: it is the band-minus-drums
    reference to check the new programmed part against.
    """
    source = Path(source)
    out_dir = Path(out_dir)
    if not source.is_file():
        raise AudioError(f"no such audio file: {source}")
    if model not in MODELS:
        raise AudioError(f"unknown demucs model {model!r}; try one of {', '.join(MODELS)}")
    if not demucs_available():
        raise AudioError(
            "demucs is not installed.\n"
            f"  install it with:  {install_hint('separate')}\n"
            "  it pulls in torch, so expect a large download."
        )

    work = out_dir / ".demucs"
    work.mkdir(parents=True, exist_ok=True)

    base = [shutil.which("demucs")] if shutil.which("demucs") else [sys.executable, "-m", "demucs"]
    cmd = [
        *base,
        "-n", model,
        "-o", str(work),
        "--shifts", str(shifts),
        "--overlap", str(overlap),
        "-j", str(jobs),
    ]
    if two_stems:
        cmd += ["--two-stems", two_stems]
    if device:
        cmd += ["-d", device]
    cmd.append(str(source))

    result = subprocess.run(cmd, capture_output=quiet, text=True, check=False)
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-10:]
        raise AudioError("demucs failed:\n" + "\n".join(tail))

    produced: dict[str, Path] = {}
    for wav in sorted(work.rglob("*.wav")):
        target = out_dir / wav.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(wav), str(target))
        produced[wav.stem] = target

    shutil.rmtree(work, ignore_errors=True)
    if not produced:
        raise AudioError(f"demucs produced no stems in {work}")
    return SeparationResult(stems=produced, model=model, command=cmd)
