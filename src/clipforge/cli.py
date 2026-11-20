"""Command-line interface for ClipForge."""

from __future__ import annotations

import argparse
import logging
import threading
from pathlib import Path

from clipforge import __version__
from clipforge.pipeline import analyze_project, ingest_project, render_project, run_project
from clipforge.render import render_clip
from clipforge.upload import (
    DEFAULT_PRIVACY,
    QUOTA_NOTE,
    resolve_client_secret,
    upload_from_work,
)
from clipforge.util import (
    ClipForgeError,
    WorkDir,
    clamp_clip_duration,
    parse_timestamp,
    parse_timestamp_list,
    require_bin,
    slugify,
)

log = logging.getLogger("clipforge")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(verbose=getattr(args, "verbose", False))
    try:
        return int(args.func(args) or 0)
    except ClipForgeError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.error("Interrupted")
        return 130


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clipforge",
        description=(
            "ClipForge — discover highlight moments in a long video, render "
            "vertical Shorts (max 60s), and optionally upload them to YouTube as Private."
        ),
    )
    parser.add_argument("--version", action="version", version=f"clipforge {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="Run the web app and background worker")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8000)
    serve_p.add_argument("--reload", action="store_true")
    serve_p.add_argument("--no-worker", action="store_true", help="Run only the web server")
    serve_p.set_defaults(func=cmd_serve)

    worker_p = sub.add_parser("worker", help="Run the background media/upload worker")
    worker_p.add_argument("--queue", choices=["media", "upload", "all"], default="all")
    worker_p.set_defaults(func=cmd_worker)

    user_p = sub.add_parser("user", help="Manage web accounts")
    user_sub = user_p.add_subparsers(dest="user_command", required=True)
    admin_p = user_sub.add_parser("create-admin", help="Create or promote an admin account")
    admin_p.add_argument("email")
    admin_p.add_argument("--password", help="Password (prompted when omitted)")
    admin_p.set_defaults(func=cmd_create_admin)

    run_p = sub.add_parser("run", help="Full pipeline: ingest → detect → render → manifest")
    run_p.add_argument("source", help="YouTube URL or path to a local video file")
    _add_work_root(run_p)
    _add_detect_args(run_p)
    _add_render_args(run_p)
    run_p.add_argument(
        "--skip-1080",
        action="store_true",
        help="Render from the analysis file instead of downloading 1080p segments",
    )
    run_p.add_argument(
        "--no-comments",
        action="store_true",
        help="Skip fetching YouTube comments",
    )
    run_p.set_defaults(func=cmd_run)

    ing_p = sub.add_parser("ingest", help="Import a URL or local file into a work directory")
    ing_p.add_argument("source", help="YouTube URL or path to a local video file")
    _add_work_root(ing_p)
    ing_p.add_argument("--no-comments", action="store_true", help="Skip fetching YouTube comments")
    ing_p.set_defaults(func=cmd_ingest)

    det_p = sub.add_parser("detect", help="Find and rank highlight moments in a work directory")
    _add_work_dir(det_p)
    _add_detect_args(det_p)
    det_p.set_defaults(func=cmd_detect)

    rend_p = sub.add_parser(
        "render",
        help="Render Shorts from detected moments, or cut a single local file",
    )
    _add_work_dir(rend_p, required=False)
    rend_p.add_argument("--input", type=Path, help="Local video to cut (single-clip mode)")
    rend_p.add_argument("--start", default="0", help="Start timestamp for single-clip mode")
    rend_p.add_argument("--duration", type=float, default=59.95, help="Duration in seconds")
    rend_p.add_argument("--title", default="HIGHLIGHT", help="Title overlay text")
    rend_p.add_argument("--output-dir", type=Path, help="Output directory for single-clip mode")
    rend_p.add_argument("--output", type=Path, help="Output MP4 path for single-clip mode")
    _add_render_args(rend_p)
    rend_p.set_defaults(func=cmd_render)

    up_p = sub.add_parser(
        "upload",
        help="Upload rendered clips to YouTube as Private (requires your OAuth client secret)",
    )
    _add_work_dir(up_p)
    up_p.add_argument("--client-secret", help="Path to client_secret.json (or set CLIPFORGE_CLIENT_SECRET)")
    up_p.add_argument("--token", default="token.json", help="Path to store/reuse the OAuth token")
    up_p.add_argument(
        "--privacy",
        default=DEFAULT_PRIVACY,
        choices=["private", "unlisted", "public"],
        help="Upload privacy (default: private)",
    )
    up_p.add_argument("--category-id", default="22", help="YouTube category id (default 22 = People & Blogs)")
    up_p.add_argument("--clips", help="Comma-separated clip ids to upload (default: all)")
    up_p.set_defaults(func=cmd_upload)

    return parser


def _add_work_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("work"),
        help="Output root (a per-video folder is created under this, default: ./work)",
    )
    parser.add_argument("--video-id", help="Override the work-folder / video id")


def _add_work_dir(parser: argparse.ArgumentParser, required: bool = True) -> None:
    parser.add_argument(
        "--work-dir",
        type=Path,
        required=required,
        help="Path to a per-video work directory (the folder that contains metadata.json)",
    )


def _add_detect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-len", type=float, default=60.0, help="Max clip length in seconds (capped at 60; default 60 encodes as 59.95)")
    parser.add_argument("--count", type=int, default=8, help="How many clips to pick (default: 8)")
    parser.add_argument(
        "--timestamps",
        default="",
        help="Comma-separated user timestamps (highest priority), e.g. 00:04:14,01:32:16",
    )
    parser.add_argument(
        "--keywords",
        default="",
        help="Comma-separated transcript keywords (defaults to a general highlight list)",
    )
    parser.add_argument("--skip-transcript", action="store_true", help="Do not run faster-whisper")
    parser.add_argument("--whisper-model", default="small", help="faster-whisper model name (default: small)")
    parser.add_argument("--min-separation", type=float, default=90.0, help="Minimum seconds between auto-detected clips")


def _add_render_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--font", help="Path to a .ttf font for the title overlay")
    parser.add_argument("--fps", type=int, default=30, help="Output frame rate (default: 30; use 60 for high-fps gameplay)")
    parser.add_argument("--crf", type=int, default=19, help="x264 CRF (default: 19)")
    parser.add_argument("--preset", default="medium", help="x264 preset (default: medium)")


def cmd_run(args: argparse.Namespace) -> int:
    require_bin("ffmpeg")
    work, _moments, _renders, manifest = run_project(
        args.source,
        Path(args.out),
        video_id=args.video_id,
        count=args.count,
        max_len=args.max_len,
        user_timestamps=parse_timestamp_list(args.timestamps),
        keywords=_keywords(args.keywords),
        skip_transcript=args.skip_transcript,
        whisper_model=args.whisper_model,
        min_separation=args.min_separation,
        skip_1080=args.skip_1080,
        write_comments=not args.no_comments,
        font=args.font,
        fps=args.fps,
        crf=args.crf,
        preset=args.preset,
    )
    _print_summary(work, manifest)
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    work = ingest_project(
        args.source,
        Path(args.out),
        video_id=args.video_id,
        write_comments=not args.no_comments,
    )
    print(f"Work directory: {work.base}")
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    work = _work_from_path(args.work_dir)
    clip_len = clamp_clip_duration(args.max_len)
    moments = analyze_project(
        work,
        count=args.count,
        max_len=clip_len,
        user_timestamps=parse_timestamp_list(args.timestamps),
        keywords=_keywords(args.keywords),
        skip_transcript=args.skip_transcript,
        whisper_model=args.whisper_model,
        min_separation=args.min_separation,
    )
    print(f"Wrote {len(moments)} moment(s) to {work.candidates}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    require_bin("ffmpeg")
    if args.input:
        return _render_single(args)
    if not args.work_dir:
        raise ClipForgeError("Pass --work-dir, or --input for single-clip mode")
    work = _work_from_path(args.work_dir)
    from clipforge.util import read_json

    if not work.candidates.is_file():
        raise ClipForgeError(f"No {work.candidates.name}; run detect first")
    data = read_json(work.candidates)
    moments = data.get("selected") or []
    if not moments:
        raise ClipForgeError("No selected moments in highlight_candidates.json")
    _renders, manifest = render_project(
        work,
        moments,
        font=args.font,
        fps=args.fps,
        crf=args.crf,
        preset=args.preset,
    )
    _print_summary(work, manifest)
    return 0


def _render_single(args: argparse.Namespace) -> int:
    src = Path(args.input)
    if not src.is_file():
        raise ClipForgeError(f"Input file not found: {src}")
    duration = clamp_clip_duration(args.duration if args.duration <= 60 else 60)
    # Allow short test clips under the 59.95 default.
    if args.duration <= 60:
        duration = min(float(args.duration), 59.95)
    start = parse_timestamp(args.start)
    if args.output:
        out = Path(args.output)
    else:
        out_dir = Path(args.output_dir) if args.output_dir else Path("shorts")
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{slugify(args.title, fallback='clip')}.mp4"
    thumb = out.with_suffix(".jpg")
    rec = render_clip(
        src,
        out,
        start=start,
        duration=duration,
        title=args.title,
        thumb=thumb,
        font=args.font,
        fps=args.fps,
        crf=args.crf,
        preset=args.preset,
    )
    print(f"Wrote {rec['path']} ({rec['width']}x{rec['height']}, {rec['duration']:.2f}s, ok={rec['ok']})")
    if rec.get("thumb"):
        print(f"Thumbnail: {rec['thumb']}")
    return 0 if rec["ok"] else 1


def cmd_upload(args: argparse.Namespace) -> int:
    work = _work_from_path(args.work_dir)
    print(QUOTA_NOTE)
    secret = resolve_client_secret(args.client_secret)
    ids = [p.strip() for p in (args.clips or "").split(",") if p.strip()]
    results = upload_from_work(
        work,
        client_secret=secret,
        token_path=Path(args.token),
        privacy=args.privacy,
        category_id=args.category_id,
        clip_ids=ids or None,
    )
    for rec in results:
        print(f"{rec.get('clip_id')}: {rec.get('youtube_url') or rec}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError as exc:
        raise ClipForgeError(
            "Web dependencies are not installed. Run: pip install -e '.[web]'"
        ) from exc
    if not args.no_worker:
        from clipforge.worker import run_worker

        threading.Thread(
            target=run_worker,
            args=("all",),
            daemon=True,
            name="clipforge-worker",
        ).start()
    uvicorn.run(
        "clipforge.api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from clipforge.worker import run_worker

    run_worker(args.queue)
    return 0


def cmd_create_admin(args: argparse.Namespace) -> int:
    import getpass

    from sqlalchemy import select

    from clipforge.config import Settings
    from clipforge.db import Database
    from clipforge.models import User
    from clipforge.security import hash_password

    settings = Settings()
    settings.ensure_dirs()
    db = Database(settings.resolved_database_url)
    db.create_all()
    email = args.email.strip().lower()
    password = args.password or getpass.getpass("Password: ")
    if len(password) < 10:
        raise ClipForgeError("Password must be at least 10 characters")
    with db.session() as session:
        user = session.execute(select(User).where(User.email == email)).scalars().first()
        if user is None:
            session.add(User(email=email, password_hash=hash_password(password), role="admin"))
            action = "created"
        else:
            user.role = "admin"
            user.password_hash = hash_password(password)
            user.is_active = True
            action = "updated"
        session.commit()
    print(f"Admin {action}: {email}")
    return 0


def _work_from_path(path: Path) -> WorkDir:
    path = Path(path)
    if path.name == "metadata.json":
        path = path.parent
    if not path.is_dir():
        raise ClipForgeError(f"Work directory not found: {path}")
    return WorkDir(path.parent, path.name)


def _keywords(value: str) -> list[str] | None:
    if not value:
        return None
    return [part.strip() for part in value.split(",") if part.strip()] or None


def _print_summary(work: WorkDir, manifest: dict) -> None:
    clips = manifest.get("clips") or []
    print(f"Work directory: {work.base}")
    print(f"Manifest: {work.manifest}")
    print(f"Clips: {len(clips)}")
    for clip in clips:
        print(
            f"  {clip.get('id')}: {clip.get('src_start')} "
            f"({clip.get('length_s')}s) {clip.get('file')}"
        )
        if clip.get("suggested_title"):
            print(f"      title: {clip['suggested_title']}")


def _setup_logging(*, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")
    # Keep third-party loggers quieter unless we asked for debug.
    if not verbose:
        for name in ("googleapiclient", "urllib3", "httpx"):
            logging.getLogger(name).setLevel(logging.WARNING)


if __name__ == "__main__":
    raise SystemExit(main())
