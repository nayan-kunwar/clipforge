"""Transactional email through Brevo, with a safe local-development fallback."""

from __future__ import annotations

import html
import logging

import httpx
from sqlalchemy.orm import Session

from clipforge.config import Settings
from clipforge.models import EmailLog

log = logging.getLogger("clipforge.email")

BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def send_email(
    db: Session,
    settings: Settings,
    *,
    to_email: str,
    subject: str,
    text: str,
    html_body: str,
    template: str,
) -> dict[str, object]:
    """Send an email and record the attempt. Never raises for provider errors."""
    if not settings.brevo_api_key:
        log.warning("Brevo is not configured; email to %s was not sent", to_email)
        db.add(
            EmailLog(
                to_email=to_email,
                template=template,
                status="skipped",
                error="CLIPFORGE_BREVO_API_KEY is not configured",
            )
        )
        db.commit()
        return {"status": "skipped", "error": "email is not configured"}

    payload = {
        "sender": {
            "name": settings.brevo_sender_name,
            "email": settings.brevo_sender_email,
        },
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": text,
        "htmlContent": html_body,
    }
    try:
        response = httpx.post(
            BREVO_URL,
            json=payload,
            headers={
                "api-key": settings.brevo_api_key,
                "accept": "application/json",
                "content-type": "application/json",
            },
            timeout=settings.brevo_timeout_seconds,
        )
        response.raise_for_status()
        provider_id = None
        try:
            provider_id = str(response.json().get("messageId") or "")
        except ValueError:
            pass
        db.add(
            EmailLog(
                to_email=to_email,
                template=template,
                status="sent",
                provider_id=provider_id or None,
            )
        )
        db.commit()
        return {"status": "sent", "provider_id": provider_id}
    except Exception as exc:  # noqa: BLE001 - provider errors are non-fatal
        log.warning("Brevo delivery failed for %s: %s", to_email, exc)
        db.add(
            EmailLog(
                to_email=to_email,
                template=template,
                status="failed",
                error=str(exc)[:2000],
            )
        )
        db.commit()
        return {"status": "failed", "error": str(exc)}


def send_invite_email(
    db: Session,
    settings: Settings,
    *,
    to_email: str,
    invite_url: str,
) -> dict[str, object]:
    safe_url = html.escape(invite_url)
    subject = "You are invited to ClipForge"
    text = (
        "You have been invited to ClipForge.\n\n"
        f"Accept your invitation: {invite_url}\n\n"
        "This link expires in 7 days."
    )
    body = (
        "<p>You have been invited to <strong>ClipForge</strong>.</p>"
        f'<p><a href="{safe_url}">Accept your invitation</a></p>'
        '<p style="color:#666">This link expires in 7 days.</p>'
    )
    return send_email(
        db,
        settings,
        to_email=to_email,
        subject=subject,
        text=text,
        html_body=body,
        template="invite",
    )


def send_password_reset_email(
    db: Session,
    settings: Settings,
    *,
    to_email: str,
    reset_url: str,
) -> dict[str, object]:
    safe_url = html.escape(reset_url)
    subject = "Reset your ClipForge password"
    text = (
        "A password reset was requested for your ClipForge account.\n\n"
        f"Reset your password: {reset_url}\n\n"
        "This link expires in 1 hour. Ignore this email if you did not request it."
    )
    body = (
        "<p>A password reset was requested for your <strong>ClipForge</strong> account.</p>"
        f'<p><a href="{safe_url}">Reset your password</a></p>'
        '<p style="color:#666">This link expires in 1 hour. '
        "Ignore this email if you did not request it.</p>"
    )
    return send_email(
        db,
        settings,
        to_email=to_email,
        subject=subject,
        text=text,
        html_body=body,
        template="password_reset",
    )


__all__ = ["send_email", "send_invite_email", "send_password_reset_email"]
