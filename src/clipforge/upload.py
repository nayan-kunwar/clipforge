"""Upload rendered clips to YouTube as Private via the Data API.

This module is optional. Install extras with: pip install 'clipforge[upload]'

You must supply your own OAuth client secret (Desktop / installed app) from a
Google Cloud project with the YouTube Data API enabled. Never commit
client_secret.json or token.json.

Scope: https://www.googleapis.com/auth/youtube.upload  (minimum, upload only)

Default privacy is Private. Unverified API projects are often limited to
Private uploads until Google reviews the project. The default quota is 10,000
units/day; a video insert costs about 1,600 units, so roughly 6 uploads/day.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from clipforge.util import ClipForgeError, WorkDir, read_json, write_json

log = logging.getLogger("clipforge")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
DEFAULT_PRIVACY = "private"
QUOTA_NOTE = (
    "YouTube Data API default quota is 10,000 units/day. Each upload costs about "
    "1,600 units (~6 uploads/day). Unverified projects may be restricted to Private."
)
VALID_PRIVACY = {"private", "unlisted", "public"}


def upload_from_work(
    work: WorkDir,
    *,
    client_secret: Path,
    token_path: Path | None = None,
    privacy: str = DEFAULT_PRIVACY,
    category_id: str = "22",
    clip_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Upload clips listed in CLIPS_MANIFEST.json and write video IDs back."""
    if not work.manifest.is_file():
        raise ClipForgeError(f"No manifest at {work.manifest}. Render clips first.")
    manifest = read_json(work.manifest)
    clips = manifest.get("clips") or []
    if clip_ids:
        wanted = set(clip_ids)
        clips = [c for c in clips if c.get("id") in wanted]
        missing = wanted - {c.get("id") for c in clips}
        if missing:
            raise ClipForgeError(f"Unknown clip id(s): {', '.join(sorted(missing))}")
    if not clips:
        raise ClipForgeError("Manifest contains no clips to upload")

    youtube = build_client(client_secret, token_path or Path("token.json"))
    results: list[dict[str, Any]] = []
    log.info(QUOTA_NOTE)
    for clip in clips:
        path = clip.get("file")
        if not path or not Path(path).is_file():
            raise ClipForgeError(f"Clip file missing for {clip.get('id')}: {path}")
        result = upload_video(
            youtube,
            Path(path),
            title=clip.get("suggested_title") or clip.get("overlay") or clip.get("id"),
            description=clip.get("suggested_description") or "",
            tags=clip.get("tags") or [],
            privacy=privacy,
            category_id=category_id,
        )
        clip["youtube_id"] = result.get("id")
        clip["youtube_url"] = (
            f"https://www.youtube.com/watch?v={result['id']}" if result.get("id") else None
        )
        clip["privacy"] = privacy
        results.append({**result, "clip_id": clip.get("id"), "file": path})
        log.info("Uploaded %s -> %s (%s)", clip.get("id"), clip.get("youtube_url"), privacy)

    manifest["clips"] = _merge_clip_updates(read_json(work.manifest).get("clips") or [], clips)
    manifest["not_uploaded"] = not all(c.get("youtube_id") for c in manifest["clips"])
    write_json(work.manifest, manifest)
    return results


def upload_video(
    youtube: Any,
    path: Path,
    *,
    title: str,
    description: str,
    tags: list[str] | None = None,
    privacy: str = DEFAULT_PRIVACY,
    category_id: str = "22",
    on_progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    privacy = (privacy or DEFAULT_PRIVACY).lower()
    if privacy not in VALID_PRIVACY:
        raise ClipForgeError(f"privacy must be one of {sorted(VALID_PRIVACY)}")
    if privacy != "private":
        log.warning(
            "Requested privacy=%s. Unverified API projects may still be forced to Private.",
            privacy,
        )
    body = {
        "snippet": {
            "title": title[:100],
            "description": description or "",
            "tags": tags or [],
            "categoryId": str(category_id),
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(path), mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info("  upload progress %.0f%%", status.progress() * 100)
            if on_progress is not None:
                on_progress(float(status.progress()))
    return response


def build_client(client_secret: Path, token_path: Path) -> Any:
    """Authorize with youtube.upload only and return a YouTube API client."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ClipForgeError(
            "YouTube upload extras are not installed. Run: pip install 'clipforge[upload]'"
        ) from exc

    client_secret = Path(client_secret)
    token_path = Path(token_path)
    if not client_secret.is_file():
        raise ClipForgeError(
            f"OAuth client secret not found: {client_secret}\n"
            "Create a Desktop OAuth client in Google Cloud, enable the YouTube Data API, "
            "and pass --client-secret /path/to/client_secret.json"
        )

    creds = None
    if token_path.is_file():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
            try:
                creds = flow.run_local_server(port=0, open_browser=True)
            except Exception as exc:  # noqa: BLE001 — fall back with a clear message
                raise ClipForgeError(
                    "OAuth browser flow failed. Run `clipforge upload` on a machine where "
                    f"you can sign in to Google, then reuse the saved token. ({exc})"
                ) from exc
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        log.info("Saved OAuth token to %s (keep this file private)", token_path)

    return build("youtube", "v3", credentials=creds)


def resolve_client_secret(explicit: str | None) -> Path:
    import os

    if explicit:
        return Path(explicit)
    env = os.environ.get("CLIPFORGE_CLIENT_SECRET")
    if env:
        return Path(env)
    for candidate in (Path("client_secret.json"), Path.cwd() / "client_secret.json"):
        if candidate.is_file():
            return candidate
    raise ClipForgeError(
        "No OAuth client secret given. Pass --client-secret, set CLIPFORGE_CLIENT_SECRET, "
        "or place client_secret.json in the current directory."
    )


def _merge_clip_updates(original: list[dict[str, Any]], updated: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {c.get("id"): c for c in updated if c.get("id")}
    merged = []
    for clip in original:
        rec = by_id.get(clip.get("id"))
        merged.append(rec if rec is not None else clip)
    return merged
