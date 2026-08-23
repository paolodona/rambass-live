"""Audio plumbing: locate ffmpeg, decode to numpy, write WAV files.

Decoding goes through ffmpeg rather than a Python audio library because the
source material is a pile of MP3s of varying vintage and ffmpeg reads all of it
without complaint. That also keeps ``librosa`` needed only for the actual DSP.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import numpy as np

AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".aif", ".aiff", ".ogg", ".opus"}


class AudioError(RuntimeError):
    """Raised when audio tooling is missing or a decode fails."""


def require_module(name: str, extra: str):
    """Import *name* or explain which extra installs it."""
    try:
        return __import__(name)
    except ImportError as exc:
        raise AudioError(
            f"this command needs the '{name}' package.\n"
            f"  install it with:  pip install -e '.[{extra}]'\n"
            f"  (or:              uv pip install -e '.[{extra}]')"
        ) from exc


#: Point this at ffmpeg when it is installed but not on PATH — which is the
#: normal state of a winget install on Windows, where the binary lands in
#: ``%LOCALAPPDATA%\\Microsoft\\WinGet\\Packages\\Gyan.FFmpeg_...\\bin`` and
#: nothing links it. Either the directory or the binary itself.
FFMPEG_ENV = "RAMBASS_FFMPEG"


def _tool_path(name: str) -> str:
    """*name* from :data:`FFMPEG_ENV` if it is set, else from PATH.

    A set-but-wrong override is an error rather than a silent fall-through to
    PATH: otherwise a typo in the variable produces "ffmpeg is not on PATH",
    which sends the reader to check the one thing that was never the problem.
    """
    override = os.environ.get(FFMPEG_ENV, "").strip().strip('"')
    if override:
        base = Path(override)
        candidates = [base] if base.is_file() else []
        directory = base.parent if base.is_file() else base
        for suffix in (".exe", ""):
            candidates.append(directory / f"{name}{suffix}")
        for candidate in candidates:
            if candidate.is_file():
                # A directory override names ffmpeg; ffprobe sits beside it.
                found = candidate if candidate.stem == name else None
                if found is None:
                    continue
                return str(found)
        raise AudioError(
            f"{FFMPEG_ENV} is set to {override!r} but there is no {name} there.\n"
            f"  it should be the ffmpeg directory, or the ffmpeg binary itself"
        )
    path = shutil.which(name)
    if not path:
        raise AudioError(
            f"{name} is not on PATH. Install it:\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   apt install ffmpeg\n"
            "  Windows: winget install Gyan.FFmpeg\n"
            f"Already installed? Set {FFMPEG_ENV} to the folder holding it "
            f"(or to the binary) instead of editing PATH."
        )
    return path


def ffmpeg_path() -> str:
    return _tool_path("ffmpeg")


def ffprobe_path() -> str:
    return _tool_path("ffprobe")


def run(cmd: list[str], *, quiet: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        cmd,
        capture_output=quiet,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-8:]
        raise AudioError(
            f"command failed: {' '.join(cmd[:3])} ...\n" + "\n".join(tail)
        )
    return result


def duration_seconds(path: str | Path) -> float:
    result = run([
        ffprobe_path(), "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise AudioError(f"could not read duration of {path}") from exc


def load_mono(path: str | Path, sample_rate: int = 22050) -> tuple[np.ndarray, int]:
    """Decode any audio file to a mono float32 numpy array via ffmpeg."""
    path = Path(path)
    if not path.is_file():
        raise AudioError(f"no such audio file: {path}")
    result = subprocess.run(
        [
            ffmpeg_path(), "-v", "error", "-nostdin",
            "-i", str(path),
            "-ac", "1", "-ar", str(sample_rate),
            "-f", "f32le", "-",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
        raise AudioError(f"ffmpeg could not decode {path}:\n" + "\n".join(tail))
    samples = np.frombuffer(result.stdout, dtype="<f4").astype(np.float32)
    if samples.size == 0:
        raise AudioError(f"{path} decoded to zero samples")
    return samples, sample_rate


def load_audio(
    path: str | Path,
    sample_rate: int = 48000,
    channels: int = 2,
) -> tuple[np.ndarray, int]:
    """Decode audio to a ``(frames, channels)`` float32 array via ffmpeg.

    Unlike :func:`load_mono` this preserves stereo, which matters for anything
    that is going to be played back rather than analysed.
    """
    path = Path(path)
    if not path.is_file():
        raise AudioError(f"no such audio file: {path}")
    result = subprocess.run(
        [
            ffmpeg_path(), "-v", "error", "-nostdin",
            "-i", str(path),
            "-ac", str(channels), "-ar", str(sample_rate),
            "-f", "f32le", "-",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
        raise AudioError(f"ffmpeg could not decode {path}:\n" + "\n".join(tail))
    flat = np.frombuffer(result.stdout, dtype="<f4").astype(np.float32)
    if flat.size == 0:
        raise AudioError(f"{path} decoded to zero samples")
    return flat.reshape(-1, channels), sample_rate


def write_wav(
    path: str | Path,
    samples: np.ndarray,
    sample_rate: int = 44100,
    *,
    bit_depth: int = 16,
) -> Path:
    """Write a mono or stereo WAV with the stdlib — no soundfile needed."""
    if bit_depth not in (16, 24):
        raise AudioError("bit_depth must be 16 or 24")
    data = np.asarray(samples, dtype=np.float32)
    if data.ndim == 1:
        data = data[:, None]
    channels = data.shape[1]

    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 1.0:
        data = data / peak

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(bit_depth // 8)
        handle.setframerate(sample_rate)
        if bit_depth == 16:
            ints = np.clip(data * 32767.0, -32768, 32767).astype("<i2")
            handle.writeframes(ints.tobytes())
        else:
            ints = np.clip(data * 8388607.0, -8388608, 8388607).astype("<i4")
            packed = bytearray()
            for value in ints.reshape(-1):
                packed += struct.pack("<i", int(value))[:3]
            handle.writeframes(bytes(packed))
    return path


def to_wav(
    source: str | Path,
    target: str | Path,
    *,
    sample_rate: int = 48000,
    channels: int = 2,
) -> Path:
    """Transcode anything to a WAV suitable for dropping into Reaper."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    run([
        ffmpeg_path(), "-v", "error", "-y", "-nostdin",
        "-i", str(source),
        "-ac", str(channels), "-ar", str(sample_rate),
        "-c:a", "pcm_s24le",
        str(target),
    ])
    return target


def measure_loudness(path: str | Path) -> dict:
    """EBU R128 integrated loudness and true peak, via ffmpeg's loudnorm.

    Backing tracks that jump 6 dB between songs make the front-of-house engineer
    hate you, so every render gets measured and matched.
    """
    result = subprocess.run(
        [
            ffmpeg_path(), "-v", "info", "-nostdin",
            "-i", str(path),
            "-af", "loudnorm=print_format=summary",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    out: dict = {}
    for line in (result.stderr or "").splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key in {
            "Input Integrated", "Input True Peak", "Input LRA",
            "Input Threshold", "Output Integrated", "Output True Peak",
        }:
            try:
                out[key] = float(value.split()[0])
            except (ValueError, IndexError):
                pass
    if not out:
        raise AudioError(f"could not measure loudness of {path}")
    return out
