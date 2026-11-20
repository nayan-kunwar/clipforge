"""Privacy-preserving admin API: accounts, invites, jobs, usage, and errors."""

from __future__ import annotations

import shutil
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from clipforge.config import Settings
from clipforge.deps import get_db, get_settings, require_admin, require_csrf
from clipforge.emailer import send_invite_email, send_password_reset_email
from clipforge.jobs import (
    job_payload,
    request_cancel,
    retry_job,
    sanitize_error,
)
from clipforge.models import (
    AdminAudit,
    Invite,
    Job,
    OAuthToken,
    PasswordReset,
    Project,
    User,
    utcnow,
)
from clipforge.quota import quota_status
from clipforge.runtime_settings import save_overrides
from clipforge.schemas import AdminSettingsPatchIn, AdminUserPatchIn, InviteCreateIn
from clipforge.security import hash_token, new_token, revoke_user_sessions
from clipforge.storage import quota_for, user_storage_used

router = APIRouter(prefix="/admin", tags=["admin"])


def _audit(db: Session, admin: User, action: str, target_type: str | None = None, target_id: str | None = None, **meta) -> None:
    db.add(
        AdminAudit(
            admin_id=admin.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            metadata_json=meta,
        )
    )


def _invite_url(settings: Settings, token: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/invite/{token}"


def _invite_payload(invite: Invite) -> dict:
    if invite.revoked_at:
        state = "revoked"
    elif invite.accepted_at:
        state = "accepted"
    elif invite.expires_at <= utcnow():
        state = "expired"
    else:
        state = "pending"
    return {
        "id": invite.id,
        "email": invite.email,
        "created_at": invite.created_at.isoformat() + "Z",
        "expires_at": invite.expires_at.isoformat() + "Z",
        "accepted_at": invite.accepted_at.isoformat() + "Z" if invite.accepted_at else None,
        "revoked_at": invite.revoked_at.isoformat() + "Z" if invite.revoked_at else None,
        "state": state,
    }


@router.get("/users")
def list_users(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    users = db.execute(select(User).order_by(User.created_at)).scalars().all()
    items = []
    for user in users:
        project_count = db.execute(select(func.count(Project.id)).where(Project.owner_id == user.id)).scalar() or 0
        google = db.get(OAuthToken, (user.id, "google"))
        items.append(
            {
                "id": user.id,
                "email": user.email,
                "role": user.role,
                "is_active": user.is_active,
                "created_at": user.created_at.isoformat() + "Z",
                "last_login_at": user.last_login_at.isoformat() + "Z" if user.last_login_at else None,
                "storage_bytes": user_storage_used(settings, user.id),
                "quota_bytes": quota_for(user, settings),
                "project_count": project_count,
                "google_connected": bool(google and google.status == "connected"),
            }
        )
    return {"users": items}


@router.patch("/users/{user_id}")
def patch_user(
    user_id: str,
    body: AdminUserPatchIn,
    admin: User = Depends(require_admin),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id and body.is_active is False:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot disable your own account")
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.quota_bytes is not None:
        user.quota_bytes = max(0, body.quota_bytes)
    if body.upload_daily_cap is not None:
        user.upload_daily_cap = max(0, body.upload_daily_cap)
    _audit(db, admin, "user.update", "user", user.id, fields=body.model_dump(exclude_none=True))
    db.commit()
    return {"ok": True}


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: str,
    admin: User = Depends(require_admin),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    token = new_token(32)
    db.add(
        PasswordReset(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=utcnow() + timedelta(hours=1),
        )
    )
    _audit(db, admin, "user.reset_password", "user", user.id)
    db.commit()
    reset_url = f"{settings.public_base_url.rstrip('/')}/reset-password?token={token}"
    result = send_password_reset_email(db, settings, to_email=user.email, reset_url=reset_url)
    payload: dict = {"ok": True, "email": result}
    if result.get("status") == "skipped":
        payload["reset_url"] = reset_url
    return payload


@router.delete("/users/{user_id}/sessions")
def revoke_sessions(
    user_id: str,
    admin: User = Depends(require_admin),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    revoke_user_sessions(db, user.id)
    _audit(db, admin, "user.revoke_sessions", "user", user.id)
    db.commit()
    return {"ok": True}


@router.get("/invites")
def list_invites(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    invites = db.execute(select(Invite).order_by(Invite.created_at.desc())).scalars().all()
    return {"invites": [_invite_payload(invite) for invite in invites]}


@router.post("/invites")
def create_invite(
    body: InviteCreateIn,
    admin: User = Depends(require_admin),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    active_users = db.execute(select(func.count(User.id))).scalar() or 0
    pending = db.execute(
        select(func.count(Invite.id)).where(
            Invite.accepted_at.is_(None),
            Invite.revoked_at.is_(None),
            Invite.expires_at > utcnow(),
        )
    ).scalar() or 0
    if active_users + pending >= settings.max_invited_users + 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invite limit reached")
    email = str(body.email).strip().lower() if body.email else None
    token = new_token(32)
    invite = Invite(
        email=email,
        token_hash=hash_token(token),
        created_by=admin.id,
        expires_at=utcnow() + timedelta(days=max(1, min(30, body.expires_days))),
    )
    db.add(invite)
    _audit(db, admin, "invite.create", "invite", invite.id, email=email)
    db.commit()
    url = _invite_url(settings, token)
    result = {"status": "skipped"}
    if email:
        result = send_invite_email(db, settings, to_email=email, invite_url=url)
    return {"invite": _invite_payload(invite), "invite_url": url, "email": result}


@router.delete("/invites/{invite_id}")
def revoke_invite(
    invite_id: str,
    admin: User = Depends(require_admin),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    invite = db.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    invite.revoked_at = utcnow()
    _audit(db, admin, "invite.revoke", "invite", invite.id)
    db.commit()
    return {"ok": True}


@router.post("/invites/{invite_id}/resend")
def resend_invite(
    invite_id: str,
    admin: User = Depends(require_admin),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    invite = db.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.accepted_at or invite.revoked_at:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invite is no longer usable")
    token = new_token(32)
    invite.token_hash = hash_token(token)
    invite.expires_at = utcnow() + timedelta(days=7)
    _audit(db, admin, "invite.resend", "invite", invite.id)
    db.commit()
    url = _invite_url(settings, token)
    result = {"status": "skipped"}
    if invite.email:
        result = send_invite_email(db, settings, to_email=invite.email, invite_url=url)
    return {"invite": _invite_payload(invite), "invite_url": url, "email": result}


@router.get("/jobs")
def list_jobs(
    status_filter: str | None = Query(default=None, alias="status"),
    kind: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(Job, User.email).join(User, Job.user_id == User.id).order_by(Job.created_at.desc()).limit(limit)
    if status_filter:
        stmt = stmt.where(Job.status == status_filter)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    rows = db.execute(stmt).all()
    jobs = []
    for job, email in rows:
        payload = job_payload(job, include_detail=False)
        payload["user_email"] = email
        jobs.append(payload)
    return {"jobs": jobs}


@router.post("/jobs/{job_id}/cancel")
def admin_cancel_job(
    job_id: str,
    admin: User = Depends(require_admin),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    request_cancel(db, job.id)
    _audit(db, admin, "job.cancel", "job", job.id)
    db.commit()
    return {"ok": True}


@router.post("/jobs/{job_id}/retry")
def admin_retry_job(
    job_id: str,
    admin: User = Depends(require_admin),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    retry_job(db, job.id)
    _audit(db, admin, "job.retry", "job", job.id)
    db.commit()
    return {"ok": True}


@router.get("/stats")
def stats(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    usage = shutil.disk_usage(settings.data_dir)
    return {
        "users": db.execute(select(func.count(User.id))).scalar() or 0,
        "projects": db.execute(select(func.count(Project.id))).scalar() or 0,
        "jobs_queued": db.execute(select(func.count(Job.id)).where(Job.status == "queued")).scalar() or 0,
        "jobs_running": db.execute(select(func.count(Job.id)).where(Job.status == "running")).scalar() or 0,
        "jobs_failed": db.execute(select(func.count(Job.id)).where(Job.status == "failed")).scalar() or 0,
        "disk_total_bytes": usage.total,
        "disk_used_bytes": usage.used,
        "disk_free_bytes": usage.free,
        "quota": quota_status(db, settings),
    }


@router.get("/errors")
def recent_errors(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.execute(
        select(Job, User.email)
        .join(User, Job.user_id == User.id)
        .where(Job.status == "failed")
        .order_by(Job.finished_at.desc())
        .limit(50)
    ).all()
    return {
        "errors": [
            {
                "job_id": job.id,
                "user_email": email,
                "kind": job.kind,
                "project_id": job.project_id,
                "error_code": job.error_code,
                "error": sanitize_error(job.error_detail),
                "finished_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
            }
            for job, email in rows
        ]
    }


@router.get("/settings")
def get_admin_settings(
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> dict:
    return {
        "settings": {
            "max_upload_bytes": settings.max_upload_bytes,
            "max_source_seconds": settings.max_source_seconds,
            "user_storage_bytes": settings.user_storage_bytes,
            "upload_daily_global": settings.upload_daily_global,
            "upload_daily_per_user": settings.upload_daily_per_user,
            "default_skip_transcript": settings.default_skip_transcript,
            "max_invited_users": settings.max_invited_users,
        }
    }


@router.patch("/settings")
def patch_admin_settings(
    body: AdminSettingsPatchIn,
    admin: User = Depends(require_admin),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    applied = save_overrides(db, body.model_dump(exclude_none=True))
    for key, value in applied.items():
        setattr(settings, key, value)
    _audit(db, admin, "settings.update", "settings", None, values=applied)
    db.commit()
    return {"ok": True, "applied": applied}


@router.get("/audit")
def audit_log(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.execute(select(AdminAudit).order_by(AdminAudit.created_at.desc()).limit(200)).scalars().all()
    return {
        "audit": [
            {
                "id": row.id,
                "admin_id": row.admin_id,
                "action": row.action,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "created_at": row.created_at.isoformat() + "Z",
            }
            for row in rows
        ]
    }
