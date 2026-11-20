"""Clip metadata, re-rendering, YouTube uploads, and manual fallback."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from clipforge.config import Settings
from clipforge.deps import get_current_user, get_db, get_owned_project, get_settings, require_csrf
from clipforge.jobs import create_job, job_payload
from clipforge.models import OAuthToken, User
from clipforge.quota import quota_status
from clipforge.schemas import ClipPatchIn, UploadClipsIn
from clipforge.storage import load_moments, workdir_for_project
from clipforge.util import ClipForgeError, read_json, write_json

router = APIRouter(tags=["clips"])

VALID_PRIVACY = {"private", "unlisted", "public"}


def _get_manifest(project_id: str, user: User, db: Session, settings: Settings) -> tuple:

    project = get_owned_project(project_id, user, db)
    work = workdir_for_project(settings, project)
    if not work.manifest.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manifest not ready")
    return project, work, read_json(work.manifest)


@router.patch("/projects/{project_id}/clips/{clip_id}")
def patch_clip(
    project_id: str,
    clip_id: str,
    body: ClipPatchIn,
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    _project, work, manifest = _get_manifest(project_id, user, db, settings)
    clip = next((item for item in manifest.get("clips") or [] if str(item.get("id")) == clip_id), None)
    if clip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")
    if body.overlay is not None:
        overlay = body.overlay.strip()[:120]
        if overlay != clip.get("overlay"):
            clip["stale"] = True
        clip["overlay"] = overlay
    if body.suggested_title is not None:
        clip["suggested_title"] = body.suggested_title.strip()[:100]
    if body.suggested_description is not None:
        clip["suggested_description"] = body.suggested_description[:5000]
    if body.tags is not None:
        tags = [str(tag).strip() for tag in body.tags if str(tag).strip()]
        if sum(len(tag) + 1 for tag in tags) > 500:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tags exceed YouTube limits")
        clip["tags"] = tags
    write_json(work.manifest, manifest)
    return {"clip": clip}


@router.post("/projects/{project_id}/clips/{clip_id}/rerender")
def rerender_clip(
    project_id: str,
    clip_id: str,
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    project = get_owned_project(project_id, user, db)
    work = workdir_for_project(settings, project)
    moments = load_moments(work)
    moment = next((item for item in moments if str(item.get("id")) == clip_id), None)
    if moment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Moment not found")
    try:
        job = create_job(
            db,
            user_id=user.id,
            project_id=project.id,
            kind="render",
            payload={"moment_ids": [clip_id], "fps": 30, "crf": 19, "preset": settings.default_ffmpeg_preset},
        )
    except ClipForgeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    project.status = "rendering"
    db.commit()
    return {"job": job_payload(job)}


@router.post("/projects/{project_id}/upload")
def upload_clips(
    project_id: str,
    body: UploadClipsIn,
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    project = get_owned_project(project_id, user, db)
    privacy = (body.privacy or "private").lower()
    if privacy not in VALID_PRIVACY:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid privacy setting")
    token = db.get(OAuthToken, (user.id, "google"))
    if token is None or token.status != "connected":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google is not connected. Download the clip and upload manually, or connect Google in Settings.",
        )
    status_payload = quota_status(db, settings, user)
    if status_payload["uploads_used"] >= settings.upload_daily_global:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Shared daily upload limit reached")
    if status_payload["user_units_used"] >= status_payload["user_uploads_limit"] * 1600:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Your daily upload limit reached")

    work = workdir_for_project(settings, project)
    clips = []
    if work.manifest.is_file():
        clips = read_json(work.manifest).get("clips") or []
    wanted = set(body.clip_ids or [])
    selected = [clip for clip in clips if not wanted or str(clip.get("id")) in wanted]
    if not selected:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No clips selected")

    jobs = []
    for clip in selected:
        try:
            job = create_job(
                db,
                user_id=user.id,
                project_id=project.id,
                kind="upload",
                payload={"clip_id": str(clip.get("id")), "privacy": privacy},
                max_attempts=2,
            )
        except ClipForgeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        jobs.append(job_payload(job))
    db.commit()
    return {
        "jobs": jobs,
        "quota": quota_status(db, settings, user),
        "manual_fallback": {
            "message": "You can also download each clip and upload it in YouTube Studio.",
        },
    }


@router.post("/projects/{project_id}/clips/{clip_id}/manual")
def mark_manual(
    project_id: str,
    clip_id: str,
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    _project, work, manifest = _get_manifest(project_id, user, db, settings)
    clip = next((item for item in manifest.get("clips") or [] if str(item.get("id")) == clip_id), None)
    if clip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")
    clip["manual_published_at"] = datetime.now(timezone.utc).isoformat()
    write_json(work.manifest, manifest)
    return {"clip": clip}
