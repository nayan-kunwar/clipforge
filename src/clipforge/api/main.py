"""FastAPI application factory and static SPA hosting."""

from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy import func, select, text

from clipforge import __version__
from clipforge.api import admin, auth, clips, jobs, media, oauth, projects, uploads
from clipforge.config import Settings
from clipforge.db import Database
from clipforge.models import User
from clipforge.runtime_settings import load_overrides
from clipforge.security import hash_password

log = logging.getLogger("clipforge.api")

PRIVACY_HTML = """<!doctype html><html><head><title>ClipForge Privacy</title></head>
<body style="font-family:system-ui;max-width:760px;margin:40px auto;line-height:1.6">
<h1>ClipForge Privacy</h1>
<p>ClipForge is a private, invite-only tool for creating short video clips from content
the user owns or has permission to use.</p>
<p>Account data is limited to your email, password hash, project metadata, and optional
Google OAuth refresh token. Uploaded media and rendered clips are stored on this server
and are visible only to their owner. Administrators can see account metadata, job status,
storage usage, and sanitized errors, but not private media.</p>
<p>Google uploads use only the <code>youtube.upload</code> scope. You can disconnect the
Google account at any time in Settings. Contact the instance administrator for deletion
requests.</p>
</body></html>"""

TERMS_HTML = """<!doctype html><html><head><title>ClipForge Terms</title></head>
<body style="font-family:system-ui;max-width:760px;margin:40px auto;line-height:1.6">
<h1>ClipForge Terms</h1>
<p>Use ClipForge only with content you own or have explicit permission to use. You are
responsible for complying with copyright law, YouTube's Terms of Service, and any other
platform rules that apply to the source material.</p>
<p>This instance is invite-only. Do not share access or use it to process content you do
not have rights to. Automated uploads are capped by the shared YouTube API quota.</p>
<p>The service is provided as-is, without warranty. The instance administrator may remove
accounts or content that violate these terms.</p>
</body></html>"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()
        db = Database(settings.resolved_database_url)
        db.create_all()
        with db.session() as session:
            load_overrides(session, settings)
            _bootstrap_admin(session, settings)
        app.state.settings = settings
        app.state.db = db
        log.info("ClipForge web starting (env=%s)", settings.app_env)
        try:
            yield
        finally:
            db.engine.dispose()

    app = FastAPI(
        title="ClipForge",
        version=__version__,
        docs_url="/api/docs" if settings.app_env != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.app_env != "production" else None,
        lifespan=lifespan,
    )
    if settings.allowed_host_list != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if settings.session_cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if not request.url.path.startswith("/api/"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
                "frame-ancestors 'none'",
            )
        return response

    for router in (auth.router, projects.router, uploads.router, media.router, jobs.router,
                   clips.router, oauth.router, admin.router):
        app.include_router(router, prefix="/api/v1")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/readyz")
    def readyz(request: Request) -> JSONResponse:
        db: Database = request.app.state.db
        try:
            with db.session() as session:
                session.execute(text("SELECT 1"))
            usage = shutil.disk_usage(settings.data_dir)
            return JSONResponse(
                {
                    "ok": True,
                    "disk_free_bytes": usage.free,
                    "disk_total_bytes": usage.total,
                }
            )
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy() -> str:
        return PRIVACY_HTML

    @app.get("/terms", response_class=HTMLResponse)
    def terms() -> str:
        return TERMS_HTML

    _mount_spa(app, settings)
    return app


def _bootstrap_admin(session, settings: Settings) -> None:
    count = session.execute(select(func.count(User.id))).scalar() or 0
    if count:
        return
    if not settings.admin_email or not settings.admin_initial_password:
        log.warning(
            "No users exist. Set CLIPFORGE_ADMIN_EMAIL and CLIPFORGE_ADMIN_INITIAL_PASSWORD "
            "to bootstrap an admin account."
        )
        return
    email = settings.admin_email.strip().lower()
    session.add(
        User(
            email=email,
            password_hash=hash_password(settings.admin_initial_password),
            role="admin",
        )
    )
    session.commit()
    log.warning("Created bootstrap admin %s; change the password after first login", email)


def _mount_spa(app: FastAPI, settings: Settings) -> None:
    dist = Path(settings.web_dist)
    index = dist / "index.html"
    if (dist / "assets").is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(
            {
                "message": "ClipForge API is running. Build the React app in web/ or run the Vite dev server.",
                "docs": "/api/docs",
            }
        )


app = create_app()
