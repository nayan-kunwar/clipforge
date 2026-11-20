"""Per-user project storage, moments, manifests, quotas, and local ingest prep."""

from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from clipforge.audio_features import ensure_audio_features
from clipforge.config import Settings
from clipforge.models import Project, User, new_id
from clipforge.util import ClipForgeError, WorkDir, probe, read_json, run, write_json

log = logging.getLogger("clipforge.storage")

VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi"}


def user_root(settings: Settings, user_id: str) -> Path:
    return settings.data_dir / "users" / user_id


def projects_root(settings: Settings, user_id: str) -> Path:
    return user_root(settings, user_id) / "projects"


def project_root(settings: Settings, user_id: str, project_id: str) -> Path:
    return projects_root(settings, user_id) / project_id


def workdir_for_project(settings: Settings, project: Project) -> WorkDir:
    return WorkDir(projects_root(settings, project.owner_id), project.id)


def upload_root(settings: Settings, user_id: str, upload_id: str) -> Path:
    return settings.data_dir / "uploads" / user_id / upload_id


def upload_file_path(settings: Settings, user_id: str, upload_id: str, filename: str) -> Path:
    return upload_root(settings, user_id, upload_id) / safe_filename(filename)


def safe_filename(name: str) -> str:
    base = Path(name or "video").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return cleaned[:180] or "video.mp4"


def project_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def user_storage_used(settings: Settings, user_id: str) -> int:
    return project_size(user_root(settings, user_id))


def quota_for(user: User, settings: Settings) -> int:
    return int(user.quota_bytes or settings.user_storage_bytes)


def check_quota(settings: Settings, user: User, additional_bytes: int = 0) -> None:
    used = user_storage_used(settings, user.id)
    if used + max(0, additional_bytes) > quota_for(user, settings):
        raise ClipForgeError("Storage quota exceeded")


def source_path_from_metadata(work: WorkDir) -> Path | None:
    try:
        meta = work.load_metadata()
    except ClipForgeError:
        return None
    raw = meta.get("source_path")
    if not raw:
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = work.base / path
    return path if path.is_file() else None


def load_moments(work: WorkDir) -> list[dict[str, Any]]:
    if work.base.joinpath("moments.json").is_file():
        data = read_json(work.base / "moments.json")
        if isinstance(data, dict):
            items = data.get("moments") or []
        else:
            items = data or []
        return [item for item in items if isinstance(item, dict)]
    # Fall back to the ranked moments from the latest analysis.
    if work.analysis.is_file():
        data = read_json(work.analysis)
        meta = work.load_metadata() if work.metadata.is_file() else {}
        duration = float(meta.get("duration_s") or data.get("duration_s") or 0)
        return moments_from_selected(data.get("selected") or [], duration)
    return []


def moments_from_selected(selected: Iterable[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    moments: list[dict[str, Any]] = []
    for item in selected:
        start = max(0.0, float(item.get("start") or 0))
        end = start + max(0.1, float(item.get("duration") or 0))
        if duration > 0:
            end = min(end, duration)
        if end <= start:
            continue
        moments.append(
            {
                "id": str(item.get("id") or f"m_{new_id()[:8]}"),
                "start": round(start, 3),
                "end": round(end, 3),
                "peak": round(float(item.get("peak") or start), 3),
                "title": str(item.get("title") or "HIGHLIGHT"),
                "source": str((item.get("sources") or ["auto"])[0]),
                "score": float(item.get("score") or 0.0),
                "why": str(item.get("why") or ""),
                "enabled": True,
            }
        )
    return moments


def save_moments(work: WorkDir, moments: list[dict[str, Any]]) -> None:
    write_json(work.base / "moments.json", {"version": 1, "moments": moments})


def validate_moments(
    moments: Iterable[dict[str, Any]],
    duration: float,
    *,
    max_clip_seconds: float = 60.0,
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for index, item in enumerate(moments, 1):
        start = max(0.0, float(item.get("start") or 0))
        end = item.get("end")
        if end is None:
            end = start + float(item.get("duration") or 0)
        end = float(end)
        if duration > 0:
            start = min(start, max(0.0, duration - 0.1))
            end = min(end, duration)
        if end - start < 1.0:
            raise ClipForgeError(f"Moment {index} must be at least 1 second")
        if end - start > max_clip_seconds + 0.001:
            raise ClipForgeError(f"Moment {index} is longer than {max_clip_seconds:g} seconds")
        moment_id = str(item.get("id") or f"m_{new_id()[:8]}")
        cleaned.append(
            {
                "id": moment_id,
                "start": round(start, 3),
                "end": round(end, 3),
                "peak": round(float(item.get("peak") or start), 3),
                "title": str(item.get("title") or "HIGHLIGHT")[:120],
                "source": str(item.get("source") or "user"),
                "score": float(item.get("score") or 0.0),
                "why": str(item.get("why") or ""),
                "enabled": bool(item.get("enabled", True)),
            }
        )
    return cleaned


def manifest_clips(work: WorkDir) -> list[dict[str, Any]]:
    if not work.manifest.is_file():
        return []
    try:
        data = read_json(work.manifest)
    except (OSError, ValueError):
        return []
    clips = data.get("clips") or []
    return [clip for clip in clips if isinstance(clip, dict)]


def clip_media_path(work: WorkDir, clip_id: str) -> Path | None:
    for clip in manifest_clips(work):
        if str(clip.get("id")) != clip_id:
            continue
        raw = clip.get("file")
        if not raw:
            return None
        path = Path(str(raw))
        if not path.is_absolute():
            path = work.base / path
        try:
            resolved = path.resolve()
            resolved.relative_to(work.base.resolve())
        except (ValueError, OSError):
            return None
        return resolved if resolved.is_file() else None
    return None


def clip_thumb_path(work: WorkDir, clip_id: str) -> Path | None:
    for clip in manifest_clips(work):
        if str(clip.get("id")) != clip_id:
            continue
        raw = clip.get("thumb")
        if not raw:
            return None
        path = Path(str(raw))
        if not path.is_absolute():
            path = work.base / path
        try:
            resolved = path.resolve()
            resolved.relative_to(work.base.resolve())
        except (ValueError, OSError):
            return None
        return resolved if resolved.is_file() else None
    return None


def poster_path(work: WorkDir) -> Path:
    return work.base / "poster.jpg"


def prepare_local_project(
    work: WorkDir,
    uploaded_path: Path,
    original_name: str,
    settings: Settings,
    *,
    title: str | None = None,
    on_progress=None,
    cancel_check=None,
) -> dict[str, Any]:
    """Move an uploaded file into a project, create preview/audio/poster."""
    work.ensure()
    suffix = Path(original_name).suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        suffix = ".mp4"
    source_dir = work.base / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    destination = source_dir / f"source{suffix}"
    if uploaded_path.resolve() != destination.resolve():
        if destination.exists():
            destination.unlink()
        shutil.move(str(uploaded_path), str(destination))
    if cancel_check is not None:
        cancel_check()

    if on_progress is not None:
        on_progress(0.15, "Probing source media")
    info = probe(destination)
    duration = float((info.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise ClipForgeError("Could not read the uploaded video duration")
    if duration > settings.max_source_seconds:
        raise ClipForgeError(
            f"Source is longer than the {settings.max_source_seconds / 3600:g} hour limit"
        )

    if on_progress is not None:
        on_progress(0.25, "Creating preview proxy")
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(destination),
            "-vf",
            "scale=-2:360",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            str(work.video360),
        ],
        check=True,
        capture=True,
    )
    if cancel_check is not None:
        cancel_check()

    if on_progress is not None:
        on_progress(0.55, "Extracting audio")
    from clipforge.ingest import _extract_audio

    _extract_audio(destination, work.audio)

    if on_progress is not None:
        on_progress(0.7, "Preparing waveform")
    ensure_audio_features(work, force=True)

    if on_progress is not None:
        on_progress(0.85, "Creating poster")
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{min(1.0, max(0.0, duration / 2)):.3f}",
            "-i",
            str(work.video360),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(poster_path(work)),
        ],
        check=True,
        capture=True,
    )

    meta = {
        "id": work.video_id,
        "video_id": work.video_id,
        "title": title or Path(original_name).stem,
        "channel": "",
        "channel_id": "",
        "webpage_url": "",
        "source_path": str(destination),
        "source_type": "local",
        "duration_s": duration,
        "duration": duration,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(work.metadata, meta)
    if not work.comments.is_file():
        write_json(work.comments, [])
    if on_progress is not None:
        on_progress(1.0, "Ingest complete")
    return meta


__all__ = [
    "check_quota",
    "clip_media_path",
    "clip_thumb_path",
    "load_moments",
    "manifest_clips",
    "moments_from_selected",
    "poster_path",
    "prepare_local_project",
    "project_root",
    "project_size",
    "projects_root",
    "quota_for",
    "safe_filename",
    "save_moments",
    "source_path_from_metadata",
    "upload_file_path",
    "upload_root",
    "user_root",
    "user_storage_used",
    "validate_moments",
    "workdir_for_project",
]
