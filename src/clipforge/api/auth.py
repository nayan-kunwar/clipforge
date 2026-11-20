"""Authentication, invite acceptance, password reset, and sessions."""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from clipforge.config import Settings
from clipforge.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    client_ip,
    get_auth_session,
    get_current_user,
    get_db,
    get_settings,
    require_csrf,
)
from clipforge.emailer import send_password_reset_email
from clipforge.models import AuthSession, Invite, OAuthToken, PasswordReset, User, utcnow
from clipforge.quota import quota_status
from clipforge.schemas import ChangePasswordIn, ForgotPasswordIn, InviteAcceptIn, LoginIn, ResetPasswordIn
from clipforge.security import (
    clear_rate_limit,
    create_session,
    hash_password,
    hash_token,
    new_token,
    password_is_acceptable,
    rate_limit,
    revoke_user_sessions,
    verify_password,
)
from clipforge.storage import quota_for, user_storage_used

log = logging.getLogger("clipforge.api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookies(response: Response, raw: str, csrf: str, settings: Settings) -> None:
    max_age = settings.session_days * 24 * 3600
    response.set_cookie(
        SESSION_COOKIE,
        raw,
        max_age=max_age,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def _user_payload(db: Session, user: User, settings: Settings, session: AuthSession) -> dict:
    google = db.get(OAuthToken, (user.id, "google"))
    used = user_storage_used(settings, user.id)
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "role": user.role,
            "created_at": user.created_at.isoformat() + "Z",
            "last_login_at": user.last_login_at.isoformat() + "Z" if user.last_login_at else None,
        },
        "csrf": session.csrf_token,
        "quota": {
            "used_bytes": used,
            "quota_bytes": quota_for(user, settings),
        },
        "google": {
            "connected": google is not None and google.status == "connected",
            "account_email": google.account_email if google else None,
            "channel_id": google.channel_id if google else None,
            "status": google.status if google else None,
        },
        "upload_quota": quota_status(db, settings, user),
    }


@router.post("/login")
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    email = body.email.strip().lower()
    ip = client_ip(request)
    if not rate_limit(f"login:{ip}:{email}", limit=5, window_seconds=15 * 60):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts")
    user = db.execute(select(User).where(User.email == email)).scalars().first()
    if user is None or not user.is_active or not verify_password(user.password_hash, body.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    clear_rate_limit(f"login:{ip}:{email}")
    user.last_login_at = utcnow()
    raw, session = create_session(
        db,
        user,
        settings,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    _set_cookies(response, raw, session.csrf_token, settings)
    return _user_payload(db, user, settings, session)


@router.post("/logout")
def logout(
    response: Response,
    session: AuthSession = Depends(get_auth_session),
    db: Session = Depends(get_db),
) -> dict:
    db.delete(session)
    db.commit()
    _clear_cookies(response)
    return {"ok": True}


@router.get("/me")
def me(
    user: User = Depends(get_current_user),
    session: AuthSession = Depends(get_auth_session),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    return _user_payload(db, user, settings, session)


@router.post("/accept-invite")
def accept_invite(
    body: InviteAcceptIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    invite = db.execute(select(Invite).where(Invite.token_hash == hash_token(body.token))).scalars().first()
    if invite is None or invite.accepted_at or invite.revoked_at or invite.expires_at <= utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation is invalid or expired")
    email = (invite.email or (body.email or "")).strip().lower()
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is required")
    if not password_is_acceptable(body.password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 10 characters")
    if db.execute(select(User).where(User.email == email)).scalars().first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists")
    user_count = db.execute(select(func.count(User.id))).scalar() or 0
    if user_count >= settings.max_invited_users + 1:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This instance has reached its user limit")
    user = User(email=email, password_hash=hash_password(body.password), role="member")
    db.add(user)
    invite.accepted_at = utcnow()
    db.flush()
    raw, session = create_session(
        db,
        user,
        settings,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    _set_cookies(response, raw, session.csrf_token, settings)
    return _user_payload(db, user, settings, session)


@router.post("/forgot-password")
def forgot_password(
    body: ForgotPasswordIn,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    ip = client_ip(request)
    if not rate_limit(f"forgot:{ip}", limit=5, window_seconds=15 * 60):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")
    email = body.email.strip().lower()
    user = db.execute(select(User).where(User.email == email, User.is_active.is_(True))).scalars().first()
    response: dict = {"ok": True, "message": "If the account exists, a reset email has been sent."}
    if user is None:
        return response
    token = new_token(32)
    db.add(
        PasswordReset(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=utcnow() + timedelta(hours=1),
        )
    )
    db.commit()
    reset_url = f"{settings.public_base_url.rstrip('/')}/reset-password?token={token}"
    result = send_password_reset_email(db, settings, to_email=user.email, reset_url=reset_url)
    if result.get("status") == "skipped" and settings.app_env != "production":
        response["dev_reset_url"] = reset_url
    return response


@router.post("/reset-password")
def reset_password(
    body: ResetPasswordIn,
    db: Session = Depends(get_db),
) -> dict:
    reset = db.execute(
        select(PasswordReset).where(PasswordReset.token_hash == hash_token(body.token))
    ).scalars().first()
    if reset is None or reset.used_at or reset.expires_at <= utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset link is invalid or expired")
    if not password_is_acceptable(body.password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 10 characters")
    user = db.get(User, reset.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account no longer exists")
    user.password_hash = hash_password(body.password)
    reset.used_at = utcnow()
    revoke_user_sessions(db, user.id)
    db.commit()
    return {"ok": True}


@router.post("/change-password")
def change_password(
    body: ChangePasswordIn,
    user: User = Depends(get_current_user),
    session: AuthSession = Depends(get_auth_session),
    _csrf: AuthSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if not password_is_acceptable(body.new_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 10 characters")
    user.password_hash = hash_password(body.new_password)
    revoke_user_sessions(db, user.id, except_hash=session.id_hash)
    db.commit()
    return {"ok": True}


@router.get("/sessions")
def list_sessions(
    user: User = Depends(get_current_user),
    session: AuthSession = Depends(get_auth_session),
    db: Session = Depends(get_db),
) -> dict:
    sessions = db.execute(
        select(AuthSession).where(AuthSession.user_id == user.id).order_by(AuthSession.created_at.desc())
    ).scalars().all()
    return {
        "sessions": [
            {
                "id": item.id_hash,
                "created_at": item.created_at.isoformat() + "Z",
                "last_seen_at": item.last_seen_at.isoformat() + "Z",
                "ip": item.ip,
                "user_agent": item.user_agent,
                "current": item.id_hash == session.id_hash,
            }
            for item in sessions
        ]
    }


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
    session: AuthSession = Depends(get_auth_session),
    _csrf: AuthSession = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    target = db.get(AuthSession, session_id)
    if target is None or target.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    db.delete(target)
    db.commit()
    return {"ok": True, "current": target.id_hash == session.id_hash}
