"""Quota and moment validation tests."""

from __future__ import annotations

import pytest

from clipforge.config import Settings
from clipforge.db import Database
from clipforge.models import User
from clipforge.quota import quota_status, reserve_upload
from clipforge.storage import validate_moments
from clipforge.util import ClipForgeError


def _database(tmp_path) -> tuple[Database, Settings]:
    settings = Settings(
        data_dir=tmp_path,
        upload_daily_global=1,
        upload_daily_per_user=1,
    )
    db = Database(settings.resolved_database_url)
    db.create_all()
    return db, settings


def test_upload_quota_is_shared_and_per_user(tmp_path) -> None:
    db, settings = _database(tmp_path)
    with db.session() as session:
        user = User(email="quota@example.com", password_hash="x", role="member")
        session.add(user)
        session.commit()
        session.refresh(user)
        reserve_upload(session, settings, user)
        status = quota_status(session, settings, user)
        assert status["uploads_used"] == 1
        assert status["units_used"] == 1600
        with pytest.raises(ClipForgeError):
            reserve_upload(session, settings, user)


def test_validate_moments_enforces_one_to_sixty_seconds() -> None:
    valid = validate_moments(
        [
            {"id": "a", "start": 10, "end": 40, "title": "A"},
            {"id": "b", "start": 50, "end": 51, "title": "B"},
        ],
        duration=120,
    )
    assert [moment["id"] for moment in valid] == ["a", "b"]
    assert valid[0]["end"] - valid[0]["start"] == 30

    with pytest.raises(ClipForgeError):
        validate_moments([{"start": 10, "end": 10.5}], duration=120)
    with pytest.raises(ClipForgeError):
        validate_moments([{"start": 10, "end": 71}], duration=120)


def test_validate_moments_clamps_to_source_bounds() -> None:
    moments = validate_moments([{"id": "a", "start": 95, "end": 150}], duration=100)
    assert moments[0]["end"] <= 100
    assert moments[0]["start"] >= 0
