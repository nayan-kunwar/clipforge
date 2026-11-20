"""Passwords, sessions, token encryption, CSRF, and small rate limits."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Iterable

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete
from sqlalchemy.orm import Session

from clipforge.config import Settings
from clipforge.models import AuthSession, User, utcnow

_password_hasher = PasswordHasher()
_limit_lock = threading.Lock()
_limits: dict[str, deque[float]] = defaultdict(deque)


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, ValueError):
        return False


def password_is_acceptable(password: str) -> bool:
    return len(password or "") >= 10


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), token_hash)


def create_session(
    db: Session,
    user: User,
    settings: Settings,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, AuthSession]:
    raw = new_token(32)
    session = AuthSession(
        id_hash=hash_token(raw),
        user_id=user.id,
        csrf_token=new_token(24),
        expires_at=utcnow() + timedelta(days=settings.session_days),
        ip=(ip or "")[:64] or None,
        user_agent=(user_agent or "")[:2000] or None,
    )
    db.add(session)
    return raw, session


def get_session(db: Session, raw_token: str | None) -> AuthSession | None:
    if not raw_token:
        return None
    session = db.get(AuthSession, hash_token(raw_token))
    if session is None:
        return None
    if session.expires_at <= utcnow():
        db.delete(session)
        db.commit()
        return None
    session.last_seen_at = utcnow()
    return session


def delete_session(db: Session, session: AuthSession) -> None:
    db.delete(session)


def revoke_user_sessions(db: Session, user_id: str, *, except_hash: str | None = None) -> None:
    stmt = delete(AuthSession).where(AuthSession.user_id == user_id)
    if except_hash:
        stmt = stmt.where(AuthSession.id_hash != except_hash)
    db.execute(stmt)


def rate_limit(key: str, *, limit: int = 5, window_seconds: int = 15 * 60) -> bool:
    """Return True when the call is allowed."""
    now = time.monotonic()
    with _limit_lock:
        bucket = _limits[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def clear_rate_limit(key: str) -> None:
    with _limit_lock:
        _limits.pop(key, None)


def _fernet(settings: Settings) -> Fernet:
    seed = settings.token_encryption_key or settings.secret_key
    key = base64.urlsafe_b64encode(hashlib.sha256(seed.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(settings: Settings, value: str) -> str:
    return _fernet(settings).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(settings: Settings, value: str) -> str:
    try:
        return _fernet(settings).decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("Stored token cannot be decrypted") from exc


def sign_state(settings: Settings, user_id: str, *, ttl_seconds: int = 900) -> str:
    issued = int(time.time())
    payload = f"{user_id}:{issued}"
    signature = hmac.new(
        settings.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}:{signature}"


def verify_state(settings: Settings, state: str, *, ttl_seconds: int = 900) -> str | None:
    try:
        user_id, issued_raw, signature = state.split(":", 2)
        issued = int(issued_raw)
    except (ValueError, AttributeError):
        return None
    if time.time() - issued > ttl_seconds:
        return None
    payload = f"{user_id}:{issued}"
    expected = hmac.new(
        settings.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return user_id


def oauth_enabled(settings: Settings) -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def unique_nonempty(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = (value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


__all__ = [
    "clear_rate_limit",
    "create_session",
    "decrypt_secret",
    "delete_session",
    "encrypt_secret",
    "get_session",
    "hash_password",
    "hash_token",
    "new_token",
    "oauth_enabled",
    "password_is_acceptable",
    "rate_limit",
    "revoke_user_sessions",
    "sign_state",
    "token_matches",
    "verify_password",
    "verify_state",
]
