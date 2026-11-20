"""Job status, SSE event streaming, and cancellation."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from clipforge.deps import get_current_user, get_db, require_csrf
from clipforge.jobs import (
    TERMINAL_STATUSES,
    event_payload,
    events_after,
    job_payload,
    request_cancel,
)
from clipforge.models import Job, User

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _owned_job(job_id: str, user: User, db: Session) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.get("/{job_id}")
def get_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return job_payload(_owned_job(job_id, user, db))


@router.post("/{job_id}/cancel")
def cancel_job(
    job_id: str,
    user: User = Depends(get_current_user),
    _csrf=Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    job = _owned_job(job_id, user, db)
    request_cancel(db, job.id)
    return {"ok": True, "job": job_payload(job)}


def _fetch_events(request: Request, job_id: str, after: int):
    db = request.app.state.db
    with db.session() as session:
        job = session.get(Job, job_id)
        if job is None:
            return [], True
        items = events_after(session, job_id, after)
        done = job.status in TERMINAL_STATUSES
        return items, done


@router.get("/{job_id}/events")
async def job_events(
    job_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    _owned_job(job_id, user, db)

    async def stream():
        after = 0
        try:
            after = int(last_event_id or "0")
        except ValueError:
            after = 0
        yield "retry: 2000\n\n"
        idle = 0
        while True:
            items, done = await asyncio.to_thread(_fetch_events, request, job_id, after)
            if items:
                idle = 0
                for item in items:
                    after = item.seq
                    payload = event_payload(item)
                    yield (
                        f"id: {item.seq}\n"
                        f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                    )
            else:
                idle += 1
            if done:
                yield "event: done\ndata: {}\n\n"
                break
            if idle >= 20:
                idle = 0
                yield ": ping\n\n"
            await asyncio.sleep(0.75)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
