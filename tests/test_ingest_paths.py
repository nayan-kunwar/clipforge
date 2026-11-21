"""Regression tests for ingest work-directory selection."""

from __future__ import annotations

from clipforge import ingest


def test_explicit_work_id_wins_over_youtube_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        ingest,
        "_fetch_metadata",
        lambda *args, **kwargs: {
            "id": "ytid1234567",
            "title": "Test video",
            "duration": 30,
        },
    )
    monkeypatch.setattr(ingest, "_download_analysis_media", lambda *args, **kwargs: None)

    work = ingest._ingest_url(
        "https://www.youtube.com/watch?v=ytid1234567",
        tmp_path,
        video_id="project-id-123",
    )

    assert work.video_id == "project-id-123"
    assert (tmp_path / "project-id-123" / "metadata.json").is_file()
    assert not (tmp_path / "ytid1234567" / "metadata.json").exists()
