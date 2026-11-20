"""Resumable chunked uploads for local video sources."""

from __future__ import annotations

import shutil
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from clipforge.config import Settings
from clipforge.deps import get_current_user, get_db, get_settings, require_csrf
from clipforge.models import Upload, User, utcnow
from clipforge.schemas import UploadInitIn
from clipforge.storage import (
    check_quota,
    safe_filename,
    upload_file_path,
    upload_root,
)
from clipforge.util import ClipForgeError, probe

router = APIRouter(prefix="/uploads", tags=["uploads"])


def _get_upload(upload_id: str, user: User, db: Session) -> Upload:
    upload = db.get(Upload, upload_id)
    if upload is None or upload.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    return upload


def _upload_payload(upload: Upload, settings: Settings) -> dict:
    return {
        "id": upload.id,
        "filename": upload.filename,
        "size_bytes": upload.size_bytes,
        "received_bytes": upload.received_bytes,
        "status": upload.status,
        "chunk_size": settings.upload_chunk_bytes,
        "created_at": upload.created_at.isoformat() + "Z",
        "expires_at": upload.expires_at.isoformat() + "Z" if upload.expires_at else None,
    }


@router.post("")
def init_upload(
    body: UploadInitIn,
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    if body.size <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty upload")
    if body.size > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.max_upload_bytes / 1024**3:g} GB limit",
        )
    try:
        check_quota(settings, user, body.size)
    except ClipForgeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    free = shutil.disk_usage(settings.data_dir).free
    if free < body.size + 512 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_507_INSUFFICIENT_STORAGE, detail="Not enough disk space")

    upload = Upload(
        user_id=user.id,
        filename=safe_filename(body.filename),
        content_type=body.content_type,
        size_bytes=body.size,
        sha256=body.sha256,
        status="uploading",
        expires_at=utcnow() + timedelta(hours=settings.upload_ttl_hours),
    )
    db.add(upload)
    db.flush()
    folder = upload_root(settings, user.id, upload.id)
    folder.mkdir(parents=True, exist_ok=True)
    upload_file_path(settings, user.id, upload.id, upload.filename).touch()
    db.commit()
    return _upload_payload(upload, settings)


@router.get("/{upload_id}")
def get_upload(
    upload_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    upload = _get_upload(upload_id, user, db)
    return _upload_payload(upload, settings)


@router.put("/{upload_id}")
async def put_chunk(
    upload_id: str,
    request: Request,
    offset: int = 0,
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    upload = _get_upload(upload_id, user, db)
    if upload.status != "uploading":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Upload is not in progress")
    if upload.expires_at and upload.expires_at <= utcnow():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Upload has expired")
    if offset != upload.received_bytes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Offset mismatch: expected {upload.received_bytes}",
        )
    body = await request.body()
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty chunk")
    if len(body) > settings.upload_chunk_bytes + 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Chunk is too large")
    if upload.received_bytes + len(body) > upload.size_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Chunk exceeds declared size")
    path = upload_file_path(settings, user.id, upload.id, upload.filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(body)
    upload.received_bytes += len(body)
    db.commit()
    return _upload_payload(upload, settings)


@router.post("/{upload_id}/complete")
def complete_upload(
    upload_id: str,
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    upload = _get_upload(upload_id, user, db)
    if upload.status == "complete":
        return _upload_payload(upload, settings)
    if upload.received_bytes != upload.size_bytes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Upload incomplete: {upload.received_bytes}/{upload.size_bytes} bytes",
        )
    path = upload_file_path(settings, user.id, upload.id, upload.filename)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload file is missing")
    try:
        info = probe(path)
    except ClipForgeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    streams = info.get("streams") or []
    if not any(stream.get("codec_type") == "video" for stream in streams):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File contains no video stream")
    duration = float((info.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not read video duration")
    if duration > settings.max_source_seconds:
        path.unlink(missing_ok=True)
        upload.status = "failed"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Source is longer than the {settings.max_source_seconds / 3600:g} hour limit",
        )
    upload.status = "complete"
    db.commit()
    return _upload_payload(upload, settings)


@router.delete("/{upload_id}")
def delete_upload(
    upload_id: str,
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    upload = _get_upload(upload_id, user, db)
    db.delete(upload)
    db.commit()
    shutil.rmtree(upload_root(settings, user.id, upload_id), ignore_errors=True)
    return {"ok": True}
