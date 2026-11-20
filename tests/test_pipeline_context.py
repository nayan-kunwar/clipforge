"""Tests for the shared job context."""

from __future__ import annotations

import threading

import pytest

from clipforge.pipeline import JobCancelled, JobContext


def test_job_context_emits_stage_progress_and_data() -> None:
    events = []
    ctx = JobContext(emit=events.append)
    ctx.set_stage("ingest", "Ingesting")
    ctx.update(1.5, "Almost done", clip_id="abc")

    assert events[0].stage == "ingest"
    assert events[0].message == "Ingesting"
    assert events[0].progress == 0.0
    assert events[-1].progress == 1.0
    assert events[-1].data == {"clip_id": "abc"}


def test_job_context_checks_cancellation() -> None:
    event = threading.Event()
    ctx = JobContext(cancel_event=event)
    ctx.check_cancelled()

    event.set()
    with pytest.raises(JobCancelled):
        ctx.check_cancelled()
