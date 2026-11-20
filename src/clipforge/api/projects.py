"""Project creation, timeline, moments, render triggers, and clips."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from clipforge.config import Settings
from clipforge.deps import get_current_user, get_db, get_owned_project, get_settings, require_csrf
from clipforge.jobs import create_job, job_payload
from clipforge.models import Job, Project, Upload, User, utcnow
from clipforge.schemas import MomentsPatchIn, ProjectCreateIn, RenderIn
from clipforge.storage import (
    check_quota,
    load_moments,
    manifest_clips,
    moments_from_selected,
    poster_path,
    project_root,
    project_size,
    save_moments,
    upload_file_path,
    user_storage_used,
    validate_moments,
    workdir_for_project,
)
from clipforge.util import (
    ClipForgeError,
    clamp_clip_duration,
    extract_video_id,
    is_url,
    read_json,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _project_payload(project: Project, settings: Settings, work, db: Session) -> dict[str, Any]:
    moments = load_moments(work)
    clips = manifest_clips(work)
    preview = work.video360.is_file()
    poster = poster_path(work).is_file()
    latest_job = db.execute(
        select(Job).where(Job.project_id == project.id).order_by(Job.created_at.desc()).limit(1)
    ).scalars().first()
    return {
        "id": project.id,
        "title": project.title,
        "source_type": project.source_type,
        "source_url": project.source_ref if project.source_type == "youtube" else None,
        "duration_s": project.duration_s,
        "status": project.status,
        "created_at": project.created_at.isoformat() + "Z",
        "updated_at": project.updated_at.isoformat() + "Z",
        "options": project.options_json or {},
        "preview_url": f"/api/v1/media/projects/{project.id}/preview" if preview else None,
        "poster_url": f"/api/v1/media/projects/{project.id}/poster" if poster else None,
        "waveform_url": f"/api/v1/projects/{project.id}/waveform" if work.waveform.is_file() else None,
        "moment_count": len(moments),
        "clip_count": len(clips),
        "storage_bytes": project_size(project_root(settings, project.owner_id, project.id)),
        "latest_job": job_payload(latest_job) if latest_job is not None else None,
    }


def _normalize_options(raw: dict[str, Any] | None, settings: Settings) -> dict[str, Any]:
    raw = raw or {}
    try:
        max_len = clamp_clip_duration(float(raw.get("max_len", 60)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid max length") from exc
    count = max(1, min(50, int(raw.get("count", 8))))
    fps = max(15, min(60, int(raw.get("fps", 30))))
    crf = max(0, min(51, int(raw.get("crf", 19))))
    preset = str(raw.get("preset", settings.default_ffmpeg_preset))
    if preset not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower"}:
        preset = settings.default_ffmpeg_preset
    timestamps = raw.get("timestamps") or []
    if isinstance(timestamps, str):
        from clipforge.util import parse_timestamp_list

        timestamps = parse_timestamp_list(timestamps)
    else:
        timestamps = [float(value) for value in timestamps]
    keywords = raw.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [part.strip() for part in keywords.split(",") if part.strip()]
    return {
        "max_len": max_len,
        "count": count,
        "fps": fps,
        "crf": crf,
        "preset": preset,
        "skip_transcript": bool(raw.get("skip_transcript", settings.default_skip_transcript)),
        "whisper_model": str(raw.get("whisper_model", settings.default_whisper_model)),
        "min_separation": max(1.0, float(raw.get("min_separation", 90.0))),
        "keywords": [str(value) for value in keywords],
        "timestamps": [float(value) for value in timestamps],
        "fetch_comments": bool(raw.get("fetch_comments", True)),
        "skip_1080": bool(raw.get("skip_1080", False)),
    }


@router.get("")
def list_projects(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    projects = db.execute(
        select(Project).where(Project.owner_id == user.id).order_by(Project.updated_at.desc())
    ).scalars().all()
    items = []
    for project in projects:
        work = workdir_for_project(settings, project)
        items.append(_project_payload(project, settings, work, db))
    return {
        "projects": items,
        "storage": {
            "used_bytes": user_storage_used(settings, user.id),
            "quota_bytes": user.quota_bytes or settings.user_storage_bytes,
        },
    }


@router.post("")
def create_project(
    body: ProjectCreateIn,
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    options = _normalize_options(body.options, settings)
    if bool(body.youtube_url) == bool(body.upload_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide exactly one of youtube_url or upload_id",
        )

    upload: Upload | None = None
    if body.youtube_url:
        url = body.youtube_url.strip()
        if not is_url(url) or extract_video_id(url) is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only YouTube URLs are supported")
        source_type = "youtube"
        source_ref = url
        title = (body.title or "YouTube project").strip()[:300]
    else:
        upload = db.get(Upload, body.upload_id)
        if upload is None or upload.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
        if upload.status != "complete":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload is not complete")
        try:
            check_quota(settings, user, upload.size_bytes)
        except ClipForgeError as exc:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
        source_type = "upload"
        source_ref = upload.id
        title = (body.title or Path(upload.filename).stem or "Uploaded video").strip()[:300]

    project = Project(
        owner_id=user.id,
        title=title,
        source_type=source_type,
        source_ref=source_ref,
        status="ingesting",
        options_json=options,
    )
    db.add(project)
    db.flush()

    if upload is not None:
        upload_path = upload_file_path(settings, user.id, upload.id, upload.filename)
        project.source_bytes = upload.size_bytes
        payload = {
            "source_type": "upload",
            "upload_path": str(upload_path),
            "filename": upload.filename,
            "options": options,
        }
    else:
        payload = {
            "source_type": "youtube",
            "source_url": source_ref,
            "options": options,
        }

    try:
        job = create_job(db, user_id=user.id, project_id=project.id, kind="ingest", payload=payload)
    except ClipForgeError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    work = workdir_for_project(settings, project)
    return {"project": _project_payload(project, settings, work, db), "job": job_payload(job)}


@router.get("/{project_id}")
def get_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    project = get_owned_project(project_id, user, db)
    work = workdir_for_project(settings, project)
    return _project_payload(project, settings, work, db)


@router.delete("/{project_id}")
def delete_project(
    project_id: str,
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    project = get_owned_project(project_id, user, db)
    for job in db.execute(
        select(Job).where(Job.project_id == project.id, Job.status.in_(["queued", "running"]))
    ).scalars():
        job.cancel_requested = True
    db.delete(project)
    db.commit()
    shutil.rmtree(project_root(settings, user.id, project_id), ignore_errors=True)
    return {"ok": True}


@router.get("/{project_id}/timeline")
def get_timeline(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    project = get_owned_project(project_id, user, db)
    work = workdir_for_project(settings, project)
    waveform = None
    if work.waveform.is_file():
        try:
            waveform = read_json(work.waveform)
        except (OSError, ValueError):
            waveform = None
    analysis = None
    if work.analysis.is_file():
        try:
            analysis = read_json(work.analysis)
        except (OSError, ValueError):
            analysis = None

    candidates: list[dict[str, Any]] = []
    if analysis:
        for source, items in (analysis.get("candidates") or {}).items():
            for index, item in enumerate(items or []):
                start = float(item.get("start") or 0)
                duration = float(item.get("duration") or 0)
                candidates.append(
                    {
                        **item,
                        "id": f"{source}-{index}",
                        "source": source,
                        "start": start,
                        "end": start + duration,
                        "duration": duration,
                    }
                )
    moments = load_moments(work)
    if not moments and analysis:
        moments = moments_from_selected(analysis.get("selected") or [], project.duration_s)
    return {
        "project_id": project.id,
        "duration_s": project.duration_s,
        "status": project.status,
        "analysis_available": analysis is not None,
        "waveform": waveform,
        "candidates": candidates,
        "moments": moments,
    }


@router.get("/{project_id}/waveform")
def get_waveform(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    project = get_owned_project(project_id, user, db)
    work = workdir_for_project(settings, project)
    if not work.waveform.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Waveform not ready")
    return FileResponse(work.waveform, media_type="application/json")


@router.post("/{project_id}/cleanup")
def cleanup_project(
    project_id: str,
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    project = get_owned_project(project_id, user, db)
    work = workdir_for_project(settings, project)
    freed = 0
    if work.src1080.is_dir():
        for path in work.src1080.iterdir():
            if path.is_file():
                try:
                    freed += path.stat().st_size
                    path.unlink()
                except OSError:
                    continue
    transcript_clips = work.base / "transcript_clips"
    if transcript_clips.is_dir():
        freed += project_size(transcript_clips)
        shutil.rmtree(transcript_clips, ignore_errors=True)
    return {"ok": True, "freed_bytes": freed}


@router.post("/{project_id}/analyze")
def analyze_project(
    project_id: str,
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    project = get_owned_project(project_id, user, db)
    if project.status in {"ingesting", "queued"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ingest is not finished")
    try:
        job = create_job(
            db,
            user_id=user.id,
            project_id=project.id,
            kind="analyze",
            payload={"options": project.options_json or {}},
        )
    except ClipForgeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    project.status = "analyzing"
    db.commit()
    return {"job": job_payload(job)}


@router.patch("/{project_id}/moments")
def patch_moments(
    project_id: str,
    body: MomentsPatchIn,
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    project = get_owned_project(project_id, user, db)
    work = workdir_for_project(settings, project)
    try:
        moments = validate_moments(
            [item.model_dump() for item in body.moments],
            project.duration_s,
        )
    except (ClipForgeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    save_moments(work, moments)
    project.updated_at = utcnow()
    db.commit()
    return {"moments": moments}


@router.post("/{project_id}/render")
def render_project(
    project_id: str,
    body: RenderIn,
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    project = get_owned_project(project_id, user, db)
    work = workdir_for_project(settings, project)
    moments = load_moments(work)
    if not moments:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No moments selected")
    wanted = set(body.moment_ids or [])
    selected = [m for m in moments if m.get("enabled", True) and (not wanted or m.get("id") in wanted)]
    if not selected:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No enabled moments to render")
    preset = body.preset
    allowed_presets = {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower"}
    if preset not in allowed_presets:
        preset = settings.default_ffmpeg_preset
    try:
        job = create_job(
            db,
            user_id=user.id,
            project_id=project.id,
            kind="render",
            payload={
                "moment_ids": [m["id"] for m in selected],
                "fps": max(15, min(60, body.fps)),
                "crf": max(0, min(51, body.crf)),
                "preset": preset,
            },
        )
    except ClipForgeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    project.status = "rendering"
    db.commit()
    return {"job": job_payload(job)}


@router.get("/{project_id}/clips")
def get_clips(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    project = get_owned_project(project_id, user, db)
    work = workdir_for_project(settings, project)
    clips = []
    for clip in manifest_clips(work):
        clip_id = str(clip.get("id"))
        clips.append(
            {
                **clip,
                "file_url": f"/api/v1/media/projects/{project.id}/clips/{clip_id}",
                "download_url": f"/api/v1/media/projects/{project.id}/clips/{clip_id}?download=1",
                "thumb_url": f"/api/v1/media/projects/{project.id}/clips/{clip_id}/thumb",
            }
        )
    return {"clips": clips, "project_id": project.id}


@router.get("/{project_id}/manifest")
def download_manifest(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    project = get_owned_project(project_id, user, db)
    work = workdir_for_project(settings, project)
    if not work.manifest.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manifest not ready")
    return FileResponse(
        work.manifest,
        media_type="application/json",
        filename=f"{project.id}_CLIPS_MANIFEST.json",
    )
