"""Shared orchestration for the CLI, the web API, and background workers.

The CLI and the web app must behave the same way. This module is the single
place that composes ingest, detection, rendering, and manifest generation.
Callers provide a :class:`JobContext` to receive progress, log, and cancellation
signals without changing the underlying media code.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from clipforge.audio_features import ensure_audio_features
from clipforge.detect import detect_moments
from clipforge.ingest import download_segments, ingest, windows_from_moments
from clipforge.manifest import build_manifest, write_manifest
from clipforge.render import render_moments
from clipforge.util import (
    ClipForgeError,
    WorkDir,
    clamp_clip_duration,
    is_url,
)

log = logging.getLogger("clipforge")


@dataclass
class JobEvent:
    """A progress/log event emitted by a pipeline stage."""

    level: str = "info"
    message: str = ""
    progress: float | None = None
    stage: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class JobCancelled(ClipForgeError):
    """Raised when a job is cancelled between pipeline stages or clips."""


class JobContext:
    """Collects progress and checks for cancellation.

    ``emit`` is optional. The CLI uses a no-op context and relies on the
    existing logger; the worker will persist events to the database.
    """

    def __init__(
        self,
        emit: Callable[[JobEvent], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._emit_cb = emit
        self.cancel_event = cancel_event or threading.Event()
        self.stage: str | None = None
        self.progress: float | None = None

    def emit(
        self,
        level: str,
        message: str,
        *,
        progress: float | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        if progress is not None:
            self.progress = max(0.0, min(1.0, float(progress)))
        if self._emit_cb is not None:
            self._emit_cb(
                JobEvent(
                    level=level,
                    message=message,
                    progress=self.progress if progress is not None else None,
                    stage=self.stage,
                    data=data or {},
                )
            )

    def set_stage(self, stage: str, message: str | None = None, *, progress: float = 0.0) -> None:
        self.stage = stage
        self.emit("info", message or stage.replace("_", " ").title(), progress=progress)

    def update(self, progress: float, message: str | None = None, **data: Any) -> None:
        self.emit("info", message or "", progress=progress, data=data)

    def info(self, message: str, *, progress: float | None = None, **data: Any) -> None:
        self.emit("info", message, progress=progress, data=data)

    def warning(self, message: str, *, progress: float | None = None, **data: Any) -> None:
        self.emit("warning", message, progress=progress, data=data)

    def error(self, message: str, *, progress: float | None = None, **data: Any) -> None:
        self.emit("error", message, progress=progress, data=data)

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelled("Job cancelled")


def ingest_project(
    source: str,
    work_root: Path,
    *,
    video_id: str | None = None,
    write_comments: bool = True,
    ctx: JobContext | None = None,
) -> WorkDir:
    """Ingest a URL or local file and prepare analysis artifacts."""
    ctx = ctx or JobContext()
    ctx.check_cancelled()
    ctx.set_stage("ingest", "Ingesting source")
    work = ingest(
        source,
        Path(work_root),
        video_id=video_id,
        write_comments=write_comments,
    )
    ctx.check_cancelled()
    ctx.update(0.85, "Preparing audio features")
    try:
        ensure_audio_features(work)
    except ClipForgeError as exc:
        ctx.warning("Audio feature extraction failed", error=str(exc))
    ctx.update(1.0, "Ingest complete")
    return work


def analyze_project(
    work: WorkDir,
    *,
    count: int = 8,
    max_len: float = 59.95,
    user_timestamps: list[float] | None = None,
    keywords: Iterable[str] | None = None,
    skip_transcript: bool = False,
    whisper_model: str = "small",
    min_separation: float = 90.0,
    ctx: JobContext | None = None,
) -> list[dict[str, Any]]:
    """Detect and rank highlight moments, persisting analysis artifacts."""
    ctx = ctx or JobContext()
    ctx.check_cancelled()
    ctx.set_stage("analyze", "Analyzing highlight signals")
    moments = detect_moments(
        work,
        count=count,
        max_len=max_len,
        user_timestamps=user_timestamps,
        keywords=keywords,
        skip_transcript=skip_transcript,
        whisper_model=whisper_model,
        min_separation=min_separation,
    )
    ctx.check_cancelled()
    ctx.update(1.0, f"Selected {len(moments)} moment(s)", moment_count=len(moments))
    return moments


def render_project(
    work: WorkDir,
    moments: list[dict[str, Any]],
    *,
    font: str | None = None,
    fps: int = 30,
    crf: int = 19,
    preset: str = "medium",
    src_files: list[dict[str, Any]] | None = None,
    ctx: JobContext | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Render selected moments and update the manifest."""
    ctx = ctx or JobContext()
    ctx.check_cancelled()
    if not moments:
        raise ClipForgeError("No moments selected for rendering")
    ctx.set_stage("render", f"Rendering {len(moments)} clip(s)")
    total = len(moments)

    def _progress(done: int, count: int, record: dict[str, Any]) -> None:
        ctx.update(
            done / max(1, count),
            f"Rendered {done}/{count} clip(s)",
            clip_id=record.get("id"),
        )

    renders = render_moments(
        work,
        moments,
        font=font,
        fps=fps,
        crf=crf,
        preset=preset,
        src_files=src_files,
        on_progress=_progress,
        cancel_check=ctx.check_cancelled,
    )
    ctx.check_cancelled()
    manifest = build_manifest(work, moments, renders)
    write_manifest(work, manifest)
    ctx.update(1.0, f"Rendered {len(renders)}/{total} clip(s)")
    return renders, manifest


def run_project(
    source: str,
    out: Path,
    *,
    video_id: str | None = None,
    count: int = 8,
    max_len: float = 60.0,
    user_timestamps: list[float] | None = None,
    keywords: Iterable[str] | None = None,
    skip_transcript: bool = False,
    whisper_model: str = "small",
    min_separation: float = 90.0,
    skip_1080: bool = False,
    write_comments: bool = True,
    font: str | None = None,
    fps: int = 30,
    crf: int = 19,
    preset: str = "medium",
    ctx: JobContext | None = None,
) -> tuple[WorkDir, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Run the full ingest -> detect -> render pipeline."""
    ctx = ctx or JobContext()
    clip_len = clamp_clip_duration(max_len)
    work = ingest_project(
        source,
        Path(out),
        video_id=video_id,
        write_comments=write_comments,
        ctx=ctx,
    )
    moments = analyze_project(
        work,
        count=count,
        max_len=clip_len,
        user_timestamps=user_timestamps,
        keywords=keywords,
        skip_transcript=skip_transcript,
        whisper_model=whisper_model,
        min_separation=min_separation,
        ctx=ctx,
    )

    src_files: list[dict[str, Any]] | None = None
    meta = work.load_metadata()
    url = str(meta.get("webpage_url") or (source if is_url(source) else ""))
    if url and not skip_1080 and moments:
        ctx.set_stage("download", "Downloading 1080p segments")
        try:
            src_files = download_segments(url, work, windows_from_moments(moments))
        except ClipForgeError as exc:
            ctx.warning(
                "1080p segment download failed, rendering from the analysis file",
                error=str(exc),
            )
            src_files = None

    renders, manifest = render_project(
        work,
        moments,
        font=font,
        fps=fps,
        crf=crf,
        preset=preset,
        src_files=src_files,
        ctx=ctx,
    )
    return work, moments, renders, manifest
