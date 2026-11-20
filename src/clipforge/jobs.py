"""Persistent job queue and event log."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Iterable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from clipforge.models import Job, JobEvent, utcnow
from clipforge.util import ClipForgeError

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running"}


def create_job(
    db: Session,
    *,
    user_id: str,
    project_id: str | None,
    kind: str,
    payload: dict[str, Any] | None = None,
    max_attempts: int = 2,
) -> Job:
    if kind != "upload":
        active = db.execute(
            select(Job).where(
                Job.user_id == user_id,
                Job.status.in_(ACTIVE_STATUSES),
            )
        ).scalars().all()
        if len(active) >= 2:
            raise ClipForgeError("You already have two active jobs; wait for one to finish")
    if project_id and kind != "upload":
        existing = db.execute(
            select(Job).where(
                Job.project_id == project_id,
                Job.kind == kind,
                Job.status.in_(ACTIVE_STATUSES),
            )
        ).scalars().first()
        if existing is not None:
            raise ClipForgeError(f"A {kind} job is already active for this project")
    job = Job(
        user_id=user_id,
        project_id=project_id,
        kind=kind,
        payload_json=payload or {},
        max_attempts=max_attempts,
    )
    db.add(job)
    db.flush()
    return job


def claim_job(db: Session, kinds: Iterable[str]) -> Job | None:
    """Atomically claim the oldest queued job for the given kinds."""
    kinds_list = list(kinds)
    if not kinds_list:
        return None
    job = db.execute(
        select(Job)
        .where(Job.status == "queued", Job.kind.in_(kinds_list))
        .order_by(Job.created_at)
        .limit(1)
    ).scalars().first()
    if job is None:
        return None
    result = db.execute(
        update(Job)
        .where(Job.id == job.id, Job.status == "queued")
        .values(
            status="running",
            stage="starting",
            started_at=utcnow(),
            lease_until=utcnow() + timedelta(seconds=60),
            attempts=Job.attempts + 1,
        )
    )
    db.commit()
    if result.rowcount != 1:
        return None
    db.refresh(job)
    return job


def heartbeat(db: Session, job_id: str, *, seconds: int = 60) -> None:
    db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "running")
        .values(lease_until=utcnow() + timedelta(seconds=seconds))
    )
    db.commit()


def requeue_expired(db: Session) -> int:
    """Requeue jobs whose worker lease expired (e.g. after a crash)."""
    result = db.execute(
        update(Job)
        .where(
            Job.status == "running",
            Job.lease_until.is_not(None),
            Job.lease_until < utcnow(),
        )
        .values(
            status="queued",
            stage="requeued",
            progress=0.0,
            lease_until=None,
            started_at=None,
        )
    )
    db.commit()
    return int(result.rowcount or 0)


def set_status(
    db: Session,
    job_id: str,
    status: str,
    *,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> None:
    values: dict[str, Any] = {"status": status}
    if status in TERMINAL_STATUSES:
        values["finished_at"] = utcnow()
        values["lease_until"] = None
    if error_code is not None:
        values["error_code"] = error_code
    if error_detail is not None:
        values["error_detail"] = error_detail[-4000:]
    db.execute(update(Job).where(Job.id == job_id).values(**values))
    db.commit()


def request_cancel(db: Session, job_id: str) -> None:
    db.execute(update(Job).where(Job.id == job_id).values(cancel_requested=True))
    db.commit()


def retry_job(db: Session, job_id: str) -> None:
    db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status="queued",
            stage=None,
            progress=0.0,
            error_code=None,
            error_detail=None,
            attempts=0,
            lease_until=None,
            cancel_requested=False,
            started_at=None,
            finished_at=None,
        )
    )
    db.commit()


def cancel_requested(db: Session, job_id: str) -> bool:
    value = db.execute(select(Job.cancel_requested).where(Job.id == job_id)).scalar_one_or_none()
    return bool(value)


def emit_event(
    db: Session,
    job_id: str,
    *,
    level: str = "info",
    message: str = "",
    progress: float | None = None,
    stage: str | None = None,
    data: dict[str, Any] | None = None,
) -> int:
    last = db.execute(select(func.max(JobEvent.seq)).where(JobEvent.job_id == job_id)).scalar()
    seq = int(last or 0) + 1
    event = JobEvent(
        job_id=job_id,
        seq=seq,
        level=level,
        message=message,
        progress=max(0.0, min(1.0, float(progress))) if progress is not None else None,
        stage=stage,
        data_json=data or {},
    )
    db.add(event)
    if progress is not None or stage is not None:
        values: dict[str, Any] = {}
        if progress is not None:
            values["progress"] = max(0.0, min(1.0, float(progress)))
        if stage is not None:
            values["stage"] = stage
        if values:
            db.execute(update(Job).where(Job.id == job_id).values(**values))
    db.commit()
    return seq


def events_after(db: Session, job_id: str, after_seq: int = 0, limit: int = 500) -> list[JobEvent]:
    return list(
        db.execute(
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.seq > after_seq)
            .order_by(JobEvent.seq)
            .limit(limit)
        ).scalars()
    )


def event_payload(event: JobEvent) -> dict[str, Any]:
    return {
        "seq": event.seq,
        "level": event.level,
        "message": event.message,
        "progress": event.progress,
        "stage": event.stage,
        "data": event.data_json or {},
        "created_at": event.created_at.isoformat() + "Z",
    }


def job_payload(job: Job, *, include_detail: bool = True) -> dict[str, Any]:
    payload = {
        "id": job.id,
        "project_id": job.project_id,
        "user_id": job.user_id,
        "kind": job.kind,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "error_code": job.error_code,
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
    }
    if include_detail:
        payload["error_detail"] = job.error_detail
    else:
        payload["error_detail"] = sanitize_error(job.error_detail)
    return payload


def sanitize_error(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"(?:[A-Za-z]:\\|/)[^\s\"']+", "<path>", value)
    text = re.sub(r"https?://\S+", "<url>", text)
    return text[-500:]


def _iso(value) -> str | None:
    return value.isoformat() + "Z" if value is not None else None


__all__ = [
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "cancel_requested",
    "claim_job",
    "create_job",
    "emit_event",
    "event_payload",
    "events_after",
    "heartbeat",
    "job_payload",
    "request_cancel",
    "requeue_expired",
    "retry_job",
    "sanitize_error",
    "set_status",
]
