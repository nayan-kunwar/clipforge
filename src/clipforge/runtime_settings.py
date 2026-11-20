"""Runtime setting overrides stored in SQLite for admin-configurable limits."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from clipforge.config import Settings
from clipforge.models import AppSetting

OVERRIDABLE: dict[str, type] = {
    "max_upload_bytes": int,
    "max_source_seconds": float,
    "user_storage_bytes": int,
    "upload_daily_global": int,
    "upload_daily_per_user": int,
    "default_skip_transcript": bool,
}


def load_overrides(db: Session, settings: Settings) -> None:
    for row in db.query(AppSetting).all():
        if row.key not in OVERRIDABLE:
            continue
        try:
            setattr(settings, row.key, OVERRIDABLE[row.key](row.value_json))
        except (TypeError, ValueError):
            continue


def save_overrides(db: Session, values: dict[str, Any]) -> dict[str, Any]:
    applied: dict[str, Any] = {}
    for key, value in values.items():
        if key not in OVERRIDABLE or value is None:
            continue
        cast = OVERRIDABLE[key](value)
        row = db.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value_json=cast)
            db.add(row)
        else:
            row.value_json = cast
        applied[key] = cast
    db.commit()
    return applied


__all__ = ["OVERRIDABLE", "load_overrides", "save_overrides"]
