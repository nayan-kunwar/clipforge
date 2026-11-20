"""Audio feature extraction shared by ingest, highlight detection, and the timeline.

One ffmpeg decode produces:

- per-second RMS / peak / transient values used by ``detect.py``
- a compact min/max waveform used by the web timeline

The per-second features are cached in ``features.npz`` and the waveform in
``waveform.json`` inside a work directory so analysis and the UI do not need to
decode the audio again.
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from clipforge.util import ClipForgeError, WorkDir

AUDIO_SR = 8000
WAVEFORM_BUCKETS_PER_SECOND = 20
SAMPLES_PER_BUCKET = AUDIO_SR // WAVEFORM_BUCKETS_PER_SECOND
WAVEFORM_VERSION = 1


@dataclass
class AudioFeatures:
    """Per-second audio features plus an int8 min/max waveform."""

    rms_db: np.ndarray
    peak_db: np.ndarray
    transient: np.ndarray
    waveform: list[list[int]]
    duration_s: float

    @property
    def seconds(self) -> int:
        return int(len(self.rms_db))


def extract_audio_features(audio_path: Path | str) -> AudioFeatures:
    """Decode audio with ffmpeg and compute features and waveform peaks."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-v",
        "error",
        "-i",
        str(audio_path),
        "-ac",
        "1",
        "-ar",
        str(AUDIO_SR),
        "-f",
        "s16le",
        "pipe:1",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.stdout is None:
        raise ClipForgeError("Could not read ffmpeg audio output")

    frame_samples = AUDIO_SR  # one second
    frame_bytes = frame_samples * 2
    rms_db: list[float] = []
    peak_db: list[float] = []
    transient: list[float] = []
    waveform: list[list[int]] = []
    total_samples = 0
    buf = b""

    try:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            while len(buf) >= frame_bytes:
                frame = buf[:frame_bytes]
                buf = buf[frame_bytes:]
                samples = np.frombuffer(frame, dtype=np.int16)
                total_samples += int(len(samples))
                _append_frame_features(samples, rms_db, peak_db, transient)
                _append_waveform(samples, waveform)

        if buf:
            usable = len(buf) - (len(buf) % 2)
            if usable:
                samples = np.frombuffer(buf[:usable], dtype=np.int16)
                total_samples += int(len(samples))
                _append_waveform(samples, waveform)
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        stderr = proc.stderr.read() if proc.stderr else b""
        ret = proc.wait()

    if ret != 0:
        err = stderr.decode("utf-8", errors="replace")[-1000:]
        raise ClipForgeError(f"ffmpeg audio decode failed: {err}")

    duration = total_samples / AUDIO_SR
    return AudioFeatures(
        rms_db=np.asarray(rms_db, dtype=np.float32),
        peak_db=np.asarray(peak_db, dtype=np.float32),
        transient=np.asarray(transient, dtype=np.float32),
        waveform=waveform,
        duration_s=duration,
    )


def _append_frame_features(
    samples: np.ndarray,
    rms_db: list[float],
    peak_db: list[float],
    transient: list[float],
) -> None:
    values = samples.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(values * values) + 1e-12))
    peak = float(np.max(np.abs(values)) + 1e-12)
    tr = float(np.mean(np.abs(np.diff(values)))) if len(values) > 1 else 0.0
    rms_db.append(20 * math.log10(rms))
    peak_db.append(20 * math.log10(peak))
    transient.append(tr)


def _append_waveform(samples: np.ndarray, waveform: list[list[int]]) -> None:
    for start in range(0, len(samples), SAMPLES_PER_BUCKET):
        bucket = samples[start : start + SAMPLES_PER_BUCKET]
        if bucket.size == 0:
            continue
        low = int(round(float(bucket.min()) / 256.0))
        high = int(round(float(bucket.max()) / 256.0))
        waveform.append([max(-127, min(127, low)), max(-127, min(127, high))])


def waveform_payload(features: AudioFeatures) -> dict[str, Any]:
    return {
        "version": WAVEFORM_VERSION,
        "buckets_per_second": WAVEFORM_BUCKETS_PER_SECOND,
        "duration_s": round(float(features.duration_s), 3),
        "peaks": features.waveform,
    }


def save_audio_features(
    features: AudioFeatures,
    *,
    features_path: Path,
    waveform_path: Path,
) -> None:
    """Write the cache files used by detection and the web timeline."""
    features_path.parent.mkdir(parents=True, exist_ok=True)
    waveform_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        features_path,
        rms_db=features.rms_db,
        peak_db=features.peak_db,
        transient=features.transient,
        duration_s=np.asarray([features.duration_s], dtype=np.float32),
    )
    # Compact JSON keeps a two-hour waveform small enough to serve to the UI.
    waveform_path.write_text(
        json.dumps(waveform_payload(features), separators=(",", ":")),
        encoding="utf-8",
    )


def load_audio_features(features_path: Path) -> AudioFeatures | None:
    """Load cached per-second features. Waveform data is not loaded."""
    if not features_path.is_file():
        return None
    with np.load(features_path) as data:
        try:
            return AudioFeatures(
                rms_db=np.array(data["rms_db"], dtype=np.float32),
                peak_db=np.array(data["peak_db"], dtype=np.float32),
                transient=np.array(data["transient"], dtype=np.float32),
                waveform=[],
                duration_s=float(data["duration_s"][0]),
            )
        except (KeyError, IndexError, ValueError):
            return None


def ensure_audio_features(work: WorkDir, *, force: bool = False) -> AudioFeatures | None:
    """Compute and cache features for a work directory when possible."""
    if not force and work.features.is_file() and work.waveform.is_file():
        cached = load_audio_features(work.features)
        if cached is not None:
            return cached
    if not work.audio.is_file():
        return None
    features = extract_audio_features(work.audio)
    save_audio_features(
        features,
        features_path=work.features,
        waveform_path=work.waveform,
    )
    return features
