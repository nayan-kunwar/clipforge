"""Tests for analysis artifact persistence."""

from __future__ import annotations

import json
from pathlib import Path

from clipforge.detect import detect_moments
from clipforge.util import WorkDir, write_json

from .conftest import requires_ffmpeg
from .helpers import write_sine_wav


def _make_work(tmp_path: Path, *, seconds: float = 6.0) -> WorkDir:
    work = WorkDir(tmp_path, "clip").ensure()
    write_json(
        work.metadata,
        {
            "id": "clip",
            "video_id": "clip",
            "title": "Test source",
            "duration_s": seconds,
            "source_type": "local",
        },
    )
    write_json(
        work.comments,
        [
            {"text": "great moment at 00:00:04", "like_count": 12},
            {"text": "another one at 00:00:04", "like_count": 3},
        ],
    )
    return work


@requires_ffmpeg
def test_detect_writes_analysis_and_candidate_files(tmp_path) -> None:
    work = _make_work(tmp_path)
    write_sine_wav(work.audio, seconds=6.0)

    moments = detect_moments(
        work,
        count=2,
        max_len=2.0,
        user_timestamps=[1.0],
        skip_transcript=True,
        min_separation=1.0,
    )

    assert moments
    assert work.candidates.is_file()
    assert work.analysis.is_file()

    candidates = json.loads(work.candidates.read_text(encoding="utf-8"))
    assert candidates["duration_s"] == 6.0
    assert candidates["selected"]

    analysis = json.loads(work.analysis.read_text(encoding="utf-8"))
    assert analysis["version"] == 1
    assert analysis["duration_s"] == 6.0
    assert analysis["options"]["skip_transcript"] is True
    assert analysis["candidates"]["user"]
    assert analysis["candidates"]["comments"]
    assert "audio" in analysis["candidates"]
    assert "transcript" in analysis["candidates"]
    assert analysis["selected"]
    assert analysis["candidates"]["user"][0]["start"] == 1.0
