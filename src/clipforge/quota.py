"""YouTube API quota tracking (shared by the whole deployment)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from clipforge.config import Settings
from clipforge.models import QuotaUsage, User
from clipforge.util import ClipForgeError

UPLOAD_UNITS = 1600
PACIFIC = ZoneInfo("America/Los_Angeles")


def pacific_date() -> str:
    return datetime.now(PACIFIC).date().isoformat()


def quota_status(db: Session, settings: Settings, user: User | None = None) -> dict:
    day = pacific_date()
    rows = db.query(QuotaUsage).filter(QuotaUsage.date_pacific == day).all()
    used_global = sum(row.units for row in rows)
    used_user = 0
    if user is not None:
        used_user = next((row.units for row in rows if row.user_id == user.id), 0)
    daily_user_cap = int(user.upload_daily_cap or settings.upload_daily_per_user) if user else settings.upload_daily_per_user
    return {
        "date_pacific": day,
        "units_used": used_global,
        "units_limit": 10000,
        "uploads_used": sum(row.uploads for row in rows),
        "uploads_limit": settings.upload_daily_global,
        "user_units_used": used_user,
        "user_uploads_limit": daily_user_cap,
        "estimated_uploads_left": max(0, settings.upload_daily_global - sum(row.uploads for row in rows)),
    }


def reserve_upload(db: Session, settings: Settings, user: User) -> None:
    day = pacific_date()
    rows = db.query(QuotaUsage).filter(QuotaUsage.date_pacific == day).all()
    global_uploads = sum(row.uploads for row in rows)
    user_row = next((row for row in rows if row.user_id == user.id), None)
    user_uploads = user_row.uploads if user_row else 0
    user_cap = int(user.upload_daily_cap or settings.upload_daily_per_user)
    if global_uploads >= settings.upload_daily_global:
        raise ClipForgeError("The shared daily YouTube upload limit has been reached")
    if user_uploads >= user_cap:
        raise ClipForgeError("Your daily YouTube upload limit has been reached")
    if user_row is None:
        user_row = QuotaUsage(date_pacific=day, user_id=user.id, units=0, uploads=0)
        db.add(user_row)
    user_row.units += UPLOAD_UNITS
    user_row.uploads += 1
    db.commit()


def refund_upload(db: Session, user: User) -> None:
    day = pacific_date()
    row = db.get(QuotaUsage, (day, user.id))
    if row is None:
        return
    row.units = max(0, row.units - UPLOAD_UNITS)
    row.uploads = max(0, row.uploads - 1)
    db.commit()


__all__ = ["PACIFIC", "UPLOAD_UNITS", "pacific_date", "quota_status", "refund_upload", "reserve_upload"]
