"""Tests for audio feature and waveform extraction."""

from __future__ import annotations

import json

import numpy as np
import pytest

from clipforge.audio_features import (
    WAVEFORM_BUCKETS_PER_SECOND,
    ensure_audio_features,
    extract_audio_features,
    load_audio_features,
    save_audio_features,
)
from clipforge.util import WorkDir

from .conftest import requires_ffmpeg
from .helpers import write_sine_wav


@requires_ffmpeg
def test_extract_audio_features_from_wav(tmp_path) -> None:
    wav = write_sine_wav(tmp_path / "tone.wav", seconds=2.0)
    features = extract_audio_features(wav)

    assert features.seconds >= 1
    assert features.duration_s >= 1.5
    assert len(features.rms_db) == features.seconds
    assert len(features.peak_db) == features.seconds
    assert len(features.transient) == features.seconds
    assert len(features.waveform) >= features.seconds * WAVEFORM_BUCKETS_PER_SECOND - 5
    assert all(-127 <= value <= 127 for pair in features.waveform for value in pair)


@requires_ffmpeg
def test_save_and_load_audio_features(tmp_path) -> None:
    wav = write_sine_wav(tmp_path / "tone.wav", seconds=2.0)
    features = extract_audio_features(wav)
    features_path = tmp_path / "features.npz"
    waveform_path = tmp_path / "waveform.json"

    save_audio_features(features, features_path=features_path, waveform_path=waveform_path)

    loaded = load_audio_features(features_path)
    assert loaded is not None
    assert loaded.duration_s == pytest.approx(features.duration_s, abs=0.01)
    np.testing.assert_allclose(loaded.rms_db, features.rms_db)
    np.testing.assert_allclose(loaded.peak_db, features.peak_db)
    np.testing.assert_allclose(loaded.transient, features.transient)

    payload = json.loads(waveform_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["buckets_per_second"] == WAVEFORM_BUCKETS_PER_SECOND
    assert payload["duration_s"] > 0
    assert payload["peaks"] == features.waveform


@requires_ffmpeg
def test_ensure_audio_features_caches_workdir_files(tmp_path) -> None:
    work = WorkDir(tmp_path, "video").ensure()
    write_sine_wav(work.audio)

    first = ensure_audio_features(work)
    assert first is not None
    assert work.features.is_file()
    assert work.waveform.is_file()

    second = ensure_audio_features(work)
    assert second is not None
    assert np.array_equal(first.rms_db, second.rms_db)
    assert second.waveform == []  # cached loads skip waveform payload
