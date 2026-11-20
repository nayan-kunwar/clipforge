"""Test helpers for media fixtures."""

from __future__ import annotations

import math
import subprocess
import wave
from pathlib import Path

import numpy as np


def write_sine_wav(path: Path, *, seconds: float = 2.0, freq: float = 440.0, sample_rate: int = 8000) -> Path:
    count = int(seconds * sample_rate)
    t = np.arange(count, dtype=np.float32) / float(sample_rate)
    samples = (0.5 * np.sin(2 * math.pi * freq * t) * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())
    return path


def make_test_video(path: Path, *, seconds: float = 4.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=640x360:rate=30:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return path
