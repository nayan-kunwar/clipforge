"""Owner-only media streaming for previews, posters, and rendered clips."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from clipforge.config import Settings
from clipforge.deps import get_current_user, get_db, get_owned_project, get_settings
from clipforge.models import User
from clipforge.storage import (
    clip_media_path,
    clip_thumb_path,
    poster_path,
    workdir_for_project,
)

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/projects/{project_id}/preview")
def project_preview(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    project = get_owned_project(project_id, user, db)
    work = workdir_for_project(settings, project)
    if not work.video360.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preview not ready")
    return FileResponse(work.video360, media_type="video/mp4")


@router.get("/projects/{project_id}/poster")
def project_poster(
    project_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    project = get_owned_project(project_id, user, db)
    work = workdir_for_project(settings, project)
    path = poster_path(work)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Poster not ready")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/projects/{project_id}/clips/{clip_id}")
def clip_video(
    project_id: str,
    clip_id: str,
    download: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    project = get_owned_project(project_id, user, db)
    work = workdir_for_project(settings, project)
    path = clip_media_path(work, clip_id)
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clip not found")
    filename = f"{clip_id}.mp4" if download else None
    return FileResponse(path, media_type="video/mp4", filename=filename)


@router.get("/projects/{project_id}/clips/{clip_id}/thumb")
def clip_thumb(
    project_id: str,
    clip_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    project = get_owned_project(project_id, user, db)
    work = workdir_for_project(settings, project)
    path = clip_thumb_path(work, clip_id)
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thumbnail not found")
    return FileResponse(path, media_type="image/jpeg")
