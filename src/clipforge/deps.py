"""FastAPI dependencies: database, sessions, current user, admin, CSRF."""

from __future__ import annotations

import secrets
from typing import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from clipforge.config import Settings
from clipforge.models import AuthSession, Project, User
from clipforge.security import get_session

SESSION_COOKIE = "clipforge_session"
CSRF_COOKIE = "clipforge_csrf"
CSRF_HEADER = "X-CSRF-Token"


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    db: Session = request.app.state.db.sessionmaker()
    try:
        yield db
    finally:
        db.close()


def get_auth_session(request: Request, db: Session = Depends(get_db)) -> AuthSession:
    raw = request.cookies.get(SESSION_COOKIE)
    session = get_session(db, raw)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    db.commit()
    request.state.auth_session = session
    return session


def get_current_user(
    request: Request,
    session: AuthSession = Depends(get_auth_session),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is disabled")
    request.state.user = user
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def require_csrf(
    request: Request,
    session: AuthSession = Depends(get_auth_session),
) -> AuthSession:
    supplied = request.headers.get(CSRF_HEADER)
    if not supplied or not secrets.compare_digest(supplied, session.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
    return session


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_owned_project(
    project_id: str,
    user: User,
    db: Session,
) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


__all__ = [
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "SESSION_COOKIE",
    "client_ip",
    "get_auth_session",
    "get_current_user",
    "get_db",
    "get_owned_project",
    "get_settings",
    "require_admin",
    "require_csrf",
]
