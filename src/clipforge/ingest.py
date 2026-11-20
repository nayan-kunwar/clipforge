"""Ingest a YouTube URL or local file: metadata, comments, analysis media, segment pulls."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Iterable, Sequence

from clipforge.audio_features import ensure_audio_features
from clipforge.util import (
    ClipForgeError,
    WorkDir,
    duration_of,
    extract_video_id,
    is_url,
    require_bin,
    run,
    slugify,
    write_json,
)

log = logging.getLogger("clipforge")

# Prefer the default client; tv_embedded often gets past YouTube bot checks.
PLAYER_CLIENTS = (None, "tv_embedded")

ANALYSIS_FORMAT = (
    "bv*[height<=360][ext=mp4]+ba[ext=m4a]/"
    "bv*[height<=360]+ba/"
    "b[height<=360]/"
    "best[height<=360]/"
    "best"
)
SEGMENT_FORMAT = (
    "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/"
    "bv*[height<=1080]+ba/"
    "b[height<=1080]/"
    "best"
)


def ingest(
    source: str,
    work_root: Path,
    *,
    video_id: str | None = None,
    write_comments: bool = True,
) -> WorkDir:
    """Download or import a source into a standard work directory."""
    require_bin("ffmpeg")
    source = str(source).strip()
    if Path(source).is_file() and not is_url(source):
        return _ingest_local(Path(source), work_root, video_id=video_id)
    if is_url(source) or extract_video_id(source):
        require_bin("yt-dlp")
        return _ingest_url(source, work_root, video_id=video_id, write_comments=write_comments)
    raise ClipForgeError(
        f"Source is not a local file or a recognized URL: {source}"
    )


def download_segments(
    url: str,
    work: WorkDir,
    windows: Sequence[tuple[float, float]],
    *,
    pad_before: float = 3.0,
    pad_after: float = 5.0,
) -> list[dict[str, Any]]:
    """Download 1080p only for selected windows using yt-dlp --download-sections."""
    require_bin("yt-dlp")
    work.ensure()
    meta = work.load_metadata()
    duration = float(meta.get("duration_s") or 0)
    url = url or meta.get("webpage_url") or meta.get("url") or ""
    if not url:
        raise ClipForgeError("No URL available to download 1080p segments")

    prepared: list[dict[str, Any]] = []
    section_args: list[str] = []
    for start, end in windows:
        padded_start = max(0.0, start - pad_before)
        padded_end = end + pad_after
        if duration > 0:
            padded_end = min(duration, padded_end)
        if padded_end <= padded_start:
            continue
        section = f"*{padded_start:.3f}-{padded_end:.3f}"
        section_args += ["--download-sections", section]
        prepared.append({
            "start": start,
            "end": end,
            "pad_start": padded_start,
            "pad_end": padded_end,
            "section": section,
        })
    if not prepared:
        return []

    out_tmpl = str(work.src1080 / "seg_%(section_start)s-%(section_end)s.%(ext)s")
    before = _existing_media(work.src1080)
    cmd = [
        "yt-dlp",
        "-f",
        SEGMENT_FORMAT,
        "--merge-output-format",
        "mp4",
        "--force-keyframes-at-cuts",
        "-o",
        out_tmpl,
        "--no-warnings",
        *section_args,
        url,
    ]
    _yt_dlp_with_fallback(cmd)
    after = _existing_media(work.src1080)
    new_files = [p for p in after if p not in before] or after
    log.info("Downloaded %s 1080p segment(s) into %s", len(new_files), work.src1080)

    # Attach the best matching file to each window (by closest pad_start).
    files_with_start: list[tuple[float, Path]] = []
    for path in new_files:
        guess = _section_start_from_name(path)
        files_with_start.append((guess, path))

    for item in prepared:
        match = min(
            files_with_start,
            key=lambda pair: abs(pair[0] - item["pad_start"]) if pair[0] is not None else 10**9,
            default=None,
        )
        if match and match[0] is not None and abs(match[0] - item["pad_start"]) < 5:
            item["path"] = str(match[1])
        elif len(new_files) == 1:
            item["path"] = str(new_files[0])
        else:
            item["path"] = None
    return prepared


def _ingest_local(path: Path, work_root: Path, video_id: str | None = None) -> WorkDir:
    path = path.resolve()
    vid = video_id or slugify(path.stem, fallback="local")
    work = WorkDir(work_root, vid).ensure()
    dest = work.video360
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    try:
        dest.symlink_to(path)
    except OSError:
        log.info("Copying local file into the work directory (symlink failed)")
        shutil.copy2(path, dest)
    _extract_audio(dest, work.audio)
    _prepare_audio_features(work, force=True)
    dur = duration_of(dest)
    write_json(
        work.metadata,
        {
            "id": vid,
            "video_id": vid,
            "title": path.stem,
            "channel": "",
            "channel_id": "",
            "webpage_url": "",
            "source_path": str(path),
            "source_type": "local",
            "duration_s": dur,
            "duration": dur,
        },
    )
    if not work.comments.is_file():
        write_json(work.comments, [])
    log.info("Imported local file %s -> %s (%.1fs)", path, work.base, dur)
    return work


def _ingest_url(
    url: str,
    work_root: Path,
    *,
    video_id: str | None = None,
    write_comments: bool = True,
) -> WorkDir:
    vid = video_id or extract_video_id(url)
    meta = _fetch_metadata(url, write_comments=write_comments)
    vid = str(meta.get("id") or vid or "video")
    work = WorkDir(work_root, vid).ensure()
    comments = meta.pop("comments", None) or []
    meta["video_id"] = vid
    meta["duration_s"] = _coerce_duration(meta)
    meta["source_type"] = "youtube"
    meta["webpage_url"] = meta.get("webpage_url") or meta.get("original_url") or url
    write_json(work.metadata, meta)
    write_json(work.comments, comments)
    log.info(
        "Metadata: %s — %s (%s, %.0fs, %s comments)",
        vid,
        meta.get("title"),
        meta.get("channel") or meta.get("uploader"),
        meta.get("duration_s") or 0,
        len(comments),
    )
    _download_analysis_media(url, work)
    return work


def _fetch_metadata(url: str, *, write_comments: bool) -> dict[str, Any]:
    cmd = ["yt-dlp", "-J", "--no-warnings", "--no-playlist"]
    if write_comments:
        cmd.append("--write-comments")
    cmd.append(url)
    last_error: Exception | None = None
    for client in PLAYER_CLIENTS:
        try:
            result = _yt_dlp_with_client(cmd, client, capture=True)
            data = _loads_json(result.stdout)
            if data:
                return data
        except (ClipForgeError, ValueError) as exc:
            last_error = exc
            log.warning("Metadata fetch failed (%s): %s", client or "default", exc)
    raise ClipForgeError(f"Could not fetch video metadata: {last_error}")


def _download_analysis_media(url: str, work: WorkDir) -> None:
    if work.video360.is_file() and work.video360.stat().st_size > 1000:
        log.info("Reusing existing analysis video %s", work.video360)
    else:
        cmd = [
            "yt-dlp",
            "-f",
            ANALYSIS_FORMAT,
            "--merge-output-format",
            "mp4",
            "-o",
            str(work.base / "video360.%(ext)s"),
            "--no-warnings",
            "--no-playlist",
            url,
        ]
        _yt_dlp_with_fallback(cmd)
        produced = _first_existing(
            work.video360,
            *work.base.glob("video360.*"),
        )
        if produced is None:
            raise ClipForgeError("yt-dlp finished but no analysis video was written")
        if produced != work.video360:
            # Normalize name/extension when merge didn't produce .mp4.
            if produced.suffix.lower() != ".mp4":
                run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(produced),
                    "-c", "copy",
                    str(work.video360),
                ])
            else:
                produced.replace(work.video360)
    if not work.audio.is_file() or work.audio.stat().st_size < 1000:
        _extract_audio(work.analysis_video(), work.audio)
    _prepare_audio_features(work)
    log.info("Analysis media ready: %s, %s", work.video360, work.audio)


def _prepare_audio_features(work: WorkDir, *, force: bool = False) -> None:
    """Cache waveform and detection features once the audio track exists."""
    try:
        features = ensure_audio_features(work, force=force)
    except ClipForgeError as exc:
        log.warning("Audio feature extraction failed: %s", exc)
        return
    if features is not None:
        log.info(
            "Audio features ready: %.1fs, %d waveform buckets",
            features.duration_s,
            len(features.waveform),
        )


def _extract_audio(video: Path, dest: Path) -> None:
    # Re-encode to AAC so the filename .m4a is always valid, even if the
    # source audio is Opus/WebM.
    run([
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-ar",
        "44100",
        str(dest),
    ])


def _yt_dlp_with_fallback(cmd: list[str]) -> None:
    last_error: Exception | None = None
    for client in PLAYER_CLIENTS:
        try:
            _yt_dlp_with_client(cmd, client, capture=True)
            return
        except ClipForgeError as exc:
            last_error = exc
            log.warning("yt-dlp failed (%s client): %s", client or "default", exc)
    raise ClipForgeError(f"yt-dlp failed with all player clients: {last_error}")


def _yt_dlp_with_client(
    cmd: list[str],
    client: str | None,
    *,
    capture: bool,
):
    full = list(cmd)
    if client:
        # Insert extractor args before the URL (last argument).
        full = full[:-1] + ["--extractor-args", f"youtube:player_client={client}"] + full[-1:]
    log.info("yt-dlp (%s client)", client or "default")
    return run(full, check=True, capture=capture)


def _coerce_duration(meta: dict[str, Any]) -> float:
    value = meta.get("duration")
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _loads_json(text: str) -> dict[str, Any]:
    import json

    text = (text or "").strip()
    if not text:
        raise ValueError("empty yt-dlp JSON")
    return json.loads(text)


def _existing_media(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}
    )


def _first_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def _section_start_from_name(path: Path) -> float | None:
    # yt-dlp names: seg_182.0-248.0.mp4 or similar
    stem = path.stem
    if stem.startswith("seg_"):
        stem = stem[4:]
    left = stem.split("-")[0]
    try:
        return float(left)
    except ValueError:
        return None


def windows_from_moments(
    moments: Iterable[dict[str, Any]],
) -> list[tuple[float, float]]:
    windows = []
    for moment in moments:
        start = float(moment["start"])
        end = start + float(moment["duration"])
        windows.append((start, end))
    return windows
