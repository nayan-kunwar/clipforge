"""Background worker for ingest, analysis, rendering, and YouTube uploads."""

from __future__ import annotations

import argparse
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from clipforge import pipeline, storage
from clipforge.config import Settings
from clipforge.db import Database
from clipforge.jobs import (
    cancel_requested,
    claim_job,
    emit_event,
    heartbeat,
    requeue_expired,
    set_status,
)
from clipforge.models import Job, OAuthToken, Project, User, utcnow
from clipforge.quota import refund_upload, reserve_upload
from clipforge.runtime_settings import load_overrides
from clipforge.security import decrypt_secret
from clipforge.util import ClipForgeError, read_json, write_json

log = logging.getLogger("clipforge.worker")

MEDIA_KINDS = ("ingest", "analyze", "render")
UPLOAD_KINDS = ("upload",)
PRESETS = {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower"}


def _emitter(db: Database, job_id: str):
    def emit(event) -> None:
        try:
            with db.session() as session:
                emit_event(
                    session,
                    job_id,
                    level=event.level,
                    message=event.message,
                    progress=event.progress,
                    stage=event.stage,
                    data=event.data,
                )
        except Exception:  # noqa: BLE001 - never let logging kill a job
            log.exception("Could not persist job event for %s", job_id)

    return emit


def _watcher(db: Database, job_id: str, ctx: pipeline.JobContext, stop: threading.Event) -> None:
    while not stop.wait(15):
        try:
            with db.session() as session:
                if cancel_requested(session, job_id):
                    ctx.cancel_event.set()
                heartbeat(session, job_id, seconds=90)
        except Exception:  # noqa: BLE001
            log.exception("Heartbeat failed for %s", job_id)
            return


def _project_status(db: Database, project_id: str | None, status: str, **fields: Any) -> None:
    if not project_id:
        return
    with db.session() as session:
        project = session.get(Project, project_id)
        if project is None:
            return
        project.status = status
        for key, value in fields.items():
            setattr(project, key, value)
        session.commit()


def _set_job_status(
    db: Database,
    job_id: str,
    status: str,
    *,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> None:
    with db.session() as session:
        set_status(session, job_id, status, error_code=error_code, error_detail=error_detail)


def _load_job_context(db: Database, job_id: str) -> tuple[Job, Project | None, dict[str, Any]]:
    with db.session() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise ClipForgeError("Job disappeared")
        project = session.get(Project, job.project_id) if job.project_id else None
        return job, project, dict(job.payload_json or {})


def _run_ingest(
    db: Database,
    settings: Settings,
    job: Job,
    project: Project,
    payload: dict[str, Any],
    ctx: pipeline.JobContext,
) -> None:
    work = storage.workdir_for_project(settings, project)
    source_type = payload.get("source_type") or project.source_type
    if source_type == "youtube":
        url = str(payload.get("source_url") or project.source_ref)
        pipeline.ingest_project(
            url,
            work.root,
            video_id=work.video_id,
            write_comments=bool((payload.get("options") or {}).get("fetch_comments", True)),
            max_duration_s=settings.max_source_seconds,
            ctx=ctx,
        )
        meta = work.load_metadata()
        duration = float(meta.get("duration_s") or 0)
        if duration > settings.max_source_seconds:
            raise ClipForgeError(
                f"Source is longer than the {settings.max_source_seconds / 3600:g} hour limit"
            )
        title = str(meta.get("title") or project.title)
    else:
        upload_path = Path(str(payload.get("upload_path") or ""))
        if not upload_path.is_file():
            raise ClipForgeError("Uploaded source file is missing")
        meta = storage.prepare_local_project(
            work,
            upload_path,
            str(payload.get("filename") or upload_path.name),
            settings,
            title=project.title,
            on_progress=lambda value, message: ctx.update(value, message),
            cancel_check=ctx.check_cancelled,
        )
        duration = float(meta.get("duration_s") or 0)
        title = str(meta.get("title") or project.title)
        shutil.rmtree(upload_path.parent, ignore_errors=True)

    source_bytes = storage.project_size(storage.project_root(settings, project.owner_id, project.id))
    with db.session() as session:
        user = session.get(User, project.owner_id)
        limit = storage.quota_for(user, settings) if user is not None else settings.user_storage_bytes
    if source_bytes > limit:
        raise ClipForgeError("Project exceeds the user storage quota")
    _project_status(
        db,
        project.id,
        "ready",
        title=title[:300],
        duration_s=duration,
        source_bytes=source_bytes,
    )


def _run_analyze(
    db: Database,
    settings: Settings,
    job: Job,
    project: Project,
    payload: dict[str, Any],
    ctx: pipeline.JobContext,
) -> None:
    work = storage.workdir_for_project(settings, project)
    options = dict(payload.get("options") or project.options_json or {})
    moments = pipeline.analyze_project(
        work,
        count=int(options.get("count") or 8),
        max_len=float(options.get("max_len") or 59.95),
        user_timestamps=options.get("timestamps") or None,
        keywords=options.get("keywords") or None,
        skip_transcript=bool(options.get("skip_transcript", settings.default_skip_transcript)),
        whisper_model=str(options.get("whisper_model") or settings.default_whisper_model),
        min_separation=float(options.get("min_separation") or 90.0),
        ctx=ctx,
    )
    if not (work.base / "moments.json").is_file():
        analysis = read_json(work.analysis) if work.analysis.is_file() else {}
        selected = analysis.get("selected") or moments
        storage.save_moments(work, storage.moments_from_selected(selected, project.duration_s))
    _project_status(db, project.id, "analyzed")


def _run_render(
    db: Database,
    settings: Settings,
    job: Job,
    project: Project,
    payload: dict[str, Any],
    ctx: pipeline.JobContext,
) -> None:
    work = storage.workdir_for_project(settings, project)
    moments = storage.load_moments(work)
    wanted = set(str(value) for value in (payload.get("moment_ids") or []))
    selected = [m for m in moments if (not wanted or str(m.get("id")) in wanted) and m.get("enabled", True)]
    if not selected:
        raise ClipForgeError("No enabled moments to render")
    preset = str(payload.get("preset") or settings.default_ffmpeg_preset)
    if preset not in PRESETS:
        preset = settings.default_ffmpeg_preset
    source = storage.source_path_from_metadata(work)
    pipeline.render_project(
        work,
        selected,
        fps=max(15, min(60, int(payload.get("fps") or 30))),
        crf=max(0, min(51, int(payload.get("crf") or 19))),
        preset=preset,
        fallback_source=source,
        merge_manifest=bool(wanted),
        ctx=ctx,
    )
    _project_status(db, project.id, "rendered")


def _run_upload(
    db: Database,
    settings: Settings,
    job: Job,
    project: Project,
    payload: dict[str, Any],
    ctx: pipeline.JobContext,
) -> None:
    work = storage.workdir_for_project(settings, project)
    clip_id = str(payload.get("clip_id") or "")
    privacy = str(payload.get("privacy") or "private").lower()
    if privacy not in {"private", "unlisted", "public"}:
        raise ClipForgeError("Invalid privacy setting")
    if not work.manifest.is_file():
        raise ClipForgeError("Manifest is missing")
    manifest = read_json(work.manifest)
    clip = next((item for item in manifest.get("clips") or [] if str(item.get("id")) == clip_id), None)
    if clip is None:
        raise ClipForgeError("Clip not found in manifest")
    path = storage.clip_media_path(work, clip_id)
    if path is None:
        raise ClipForgeError("Rendered clip file is missing")

    with db.session() as session:
        token = session.get(OAuthToken, (project.owner_id, "google"))
        user = session.get(User, project.owner_id)
        if token is None or token.status != "connected" or user is None:
            raise ClipForgeError("Google account is not connected")
        refresh_token = decrypt_secret(settings, token.encrypted_refresh_token)
        reserve_upload(session, settings, user)

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        from clipforge.upload import upload_video

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            scopes=["https://www.googleapis.com/auth/youtube.upload"],
        )
        creds.refresh(Request())
        youtube = build("youtube", "v3", credentials=creds)
        response = upload_video(
            youtube,
            path,
            title=str(clip.get("suggested_title") or clip.get("overlay") or clip_id)[:100],
            description=str(clip.get("suggested_description") or ""),
            tags=list(clip.get("tags") or []),
            privacy=privacy,
            on_progress=lambda value: ctx.update(value, "Uploading to YouTube"),
        )
    except Exception:
        with db.session() as session:
            user = session.get(User, project.owner_id)
            if user is not None:
                refund_upload(session, user)
        raise

    youtube_id = response.get("id")
    clip["youtube_id"] = youtube_id
    clip["youtube_url"] = f"https://www.youtube.com/watch?v={youtube_id}" if youtube_id else None
    clip["privacy"] = privacy
    clip["published_at"] = utcnow().isoformat() + "Z"
    clip["stale"] = False
    manifest["not_uploaded"] = not all(item.get("youtube_id") for item in manifest.get("clips") or [])
    write_json(work.manifest, manifest)
    if youtube_id:
        with db.session() as session:
            token = session.get(OAuthToken, (project.owner_id, "google"))
            if token is not None:
                token.channel_id = (response.get("snippet") or {}).get("channelId") or token.channel_id
                session.commit()
    ctx.update(1.0, f"Uploaded {clip_id}")


def process_job(db: Database, settings: Settings, job_id: str) -> None:
    job, project, payload = _load_job_context(db, job_id)
    ctx = pipeline.JobContext(emit=_emitter(db, job_id))
    stop = threading.Event()
    watcher = threading.Thread(target=_watcher, args=(db, job_id, ctx, stop), daemon=True)
    watcher.start()
    try:
        if job.kind != "upload" and project is None:
            raise ClipForgeError("Project is missing")
        if job.kind == "ingest":
            _run_ingest(db, settings, job, project, payload, ctx)
        elif job.kind == "analyze":
            _run_analyze(db, settings, job, project, payload, ctx)
        elif job.kind == "render":
            _run_render(db, settings, job, project, payload, ctx)
        elif job.kind == "upload":
            _run_upload(db, settings, job, project, payload, ctx)
        else:
            raise ClipForgeError(f"Unknown job kind: {job.kind}")
        ctx.update(1.0, "Job complete")
        _set_job_status(db, job_id, "succeeded")
    except pipeline.JobCancelled:
        _set_job_status(db, job_id, "cancelled")
        if project is not None and job.kind != "upload":
            _project_status(db, project.id, "failed")
        log.info("Job %s cancelled", job_id)
    except Exception as exc:  # noqa: BLE001 - job failures must be recorded
        code = "clipforge_error" if isinstance(exc, ClipForgeError) else type(exc).__name__
        detail = str(exc) or code
        log.exception("Job %s failed: %s", job_id, detail)
        try:
            ctx.error(detail)
        except Exception:  # noqa: BLE001
            pass
        _set_job_status(db, job_id, "failed", error_code=code, error_detail=detail)
        if project is not None and job.kind != "upload":
            _project_status(db, project.id, "failed")
    finally:
        stop.set()
        watcher.join(timeout=1)


def run_worker(queue: str = "all", *, once: bool = False) -> None:
    settings = Settings()
    settings.ensure_dirs()
    db = Database(settings.resolved_database_url)
    db.create_all()
    with db.session() as session:
        load_overrides(session, settings)
    with db.session() as session:
        requeue_expired(session)
        storage.cleanup_expired_uploads(settings, session)
    kinds = MEDIA_KINDS if queue == "media" else UPLOAD_KINDS if queue == "upload" else MEDIA_KINDS + UPLOAD_KINDS
    log.info("Worker started (queue=%s)", queue)
    while True:
        with db.session() as session:
            load_overrides(session, settings)
        claimed = None
        with db.session() as session:
            claimed = claim_job(session, kinds)
        if claimed is None:
            if once:
                return
            time.sleep(2)
            continue
        log.info("Claimed %s job %s", claimed.kind, claimed.id)
        process_job(db, settings, claimed.id)
        if once:
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clipforge-worker")
    parser.add_argument("--queue", choices=["media", "upload", "all"], default="all")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        run_worker(args.queue, once=args.once)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
