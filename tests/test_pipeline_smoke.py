"""End-to-end smoke test for the shared pipeline."""

from __future__ import annotations

from pathlib import Path

from clipforge.pipeline import JobContext, JobEvent, run_project
from clipforge.util import read_json

from .conftest import requires_ffmpeg
from .helpers import make_test_video


@requires_ffmpeg
def test_run_project_renders_a_vertical_clip(tmp_path) -> None:
    source = make_test_video(tmp_path / "source.mp4", seconds=4.0)
    events: list[JobEvent] = []
    ctx = JobContext(emit=events.append)

    work, moments, renders, manifest = run_project(
        str(source),
        tmp_path / "work",
        count=1,
        max_len=2.0,
        skip_transcript=True,
        skip_1080=True,
        fps=30,
        preset="ultrafast",
        ctx=ctx,
    )

    assert moments
    assert renders
    assert manifest["clips"]
    assert work.analysis.is_file()
    assert work.waveform.is_file()

    clip_path = Path(manifest["clips"][0]["file"])
    assert clip_path.is_file()
    assert manifest["clips"][0]["ok"] is True
    assert manifest["clips"][0]["length_s"] <= 2.05

    analysis = read_json(work.analysis)
    assert analysis["selected"]
    assert events
    assert any(event.stage == "render" for event in events)
    assert events[-1].progress == 1.0
