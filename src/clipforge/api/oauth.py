"""Google OAuth web flow for per-user YouTube uploads."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from clipforge.config import Settings
from clipforge.deps import get_current_user, get_db, get_settings, require_csrf
from clipforge.models import OAuthToken, User
from clipforge.security import (
    encrypt_secret,
    oauth_enabled,
    sign_state,
    verify_state,
)

log = logging.getLogger("clipforge.api.oauth")

router = APIRouter(prefix="/oauth", tags=["oauth"])

SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/youtube.upload",
]


def _client_config(settings: Settings) -> dict:
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.resolved_google_redirect_uri],
        }
    }


@router.get("/google/start")
def start_google(
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not oauth_enabled(settings):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google OAuth is not configured")
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Google upload extras are not installed") from exc
    flow = Flow.from_client_config(_client_config(settings), scopes=SCOPES)
    flow.redirect_uri = settings.resolved_google_redirect_uri
    state = sign_state(settings, user.id)
    url, _state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
        state=state,
    )
    return {"url": url}


@router.get("/google/callback")
def google_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    target = f"{settings.public_base_url.rstrip('/')}/settings"
    user_id = verify_state(settings, state)
    if user_id is None:
        return RedirectResponse(target + "?google=error&reason=state", status_code=302)
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return RedirectResponse(target + "?google=error&reason=user", status_code=302)
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        return RedirectResponse(target + "?google=error&reason=dependency", status_code=302)
    try:
        flow = Flow.from_client_config(_client_config(settings), scopes=SCOPES)
        flow.redirect_uri = settings.resolved_google_redirect_uri
        flow.fetch_token(code=code)
        credentials = flow.credentials
        if not credentials.refresh_token:
            raise ValueError("Google did not return a refresh token")
        account_email = None
        try:
            response = httpx.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {credentials.token}"},
                timeout=10,
            )
            if response.status_code == 200:
                account_email = response.json().get("email")
        except Exception:  # noqa: BLE001 - profile lookup is optional
            pass
        token = db.get(OAuthToken, (user.id, "google"))
        if token is None:
            token = OAuthToken(user_id=user.id, provider="google")
            db.add(token)
        token.encrypted_refresh_token = encrypt_secret(settings, credentials.refresh_token)
        token.account_email = account_email
        token.scopes = " ".join(credentials.scopes or SCOPES)
        token.status = "connected"
        token.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if credentials.expiry:
            token.token_expires_at = credentials.expiry.replace(tzinfo=None)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - redirect with a safe error marker
        log.warning("Google OAuth callback failed: %s", exc)
        return RedirectResponse(target + "?google=error", status_code=302)
    return RedirectResponse(target + "?google=connected", status_code=302)


@router.post("/google/disconnect")
def disconnect_google(
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    token = db.get(OAuthToken, (user.id, "google"))
    if token is not None:
        db.delete(token)
        db.commit()
    return {"ok": True}
