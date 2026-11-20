"""Render vertical 1080x1920 Shorts with a blurred background and title overlay."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from clipforge.util import (
    ClipForgeError,
    WorkDir,
    duration_of,
    escape_drawtext,
    find_font,
    format_timestamp,
    probe,
    run,
    streams_by_type,
)

log = logging.getLogger("clipforge")

WIDTH = 1080
HEIGHT = 1920
TITLE_SECONDS = 3.5


def render_moments(
    work: WorkDir,
    moments: list[dict[str, Any]],
    *,
    font: str | None = None,
    fps: int = 30,
    crf: int = 19,
    preset: str = "medium",
    src_files: list[dict[str, Any]] | None = None,
    fallback_source: Path | None = None,
    title_seconds: float = TITLE_SECONDS,
) -> list[dict[str, Any]]:
    """Render each moment into work/shorts and return per-clip records."""
    work.ensure()
    font_path = find_font(font)
    source_default = fallback_source or _default_source(work)
    records: list[dict[str, Any]] = []
    for moment in moments:
        src, ss = _resolve_source(moment, src_files, source_default)
        out = work.shorts / f"{moment['id']}.mp4"
        thumb = work.thumbs / f"{moment['id']}.jpg"
        rec = render_clip(
            src,
            out,
            start=ss,
            duration=float(moment["duration"]),
            title=str(moment.get("title") or "HIGHLIGHT"),
            thumb=thumb,
            font=font_path,
            fps=fps,
            crf=crf,
            preset=preset,
            title_seconds=title_seconds,
        )
        rec["id"] = moment["id"]
        rec["src_start"] = format_timestamp(moment["start"])
        rec["src_start_sec"] = float(moment["start"])
        rec["title_overlay"] = moment.get("title") or "HIGHLIGHT"
        rec["why"] = moment.get("why")
        rec["score"] = moment.get("score")
        rec["sources"] = moment.get("sources")
        records.append(rec)
    return records


def render_clip(
    src: Path,
    out: Path,
    *,
    start: float = 0.0,
    duration: float = 59.95,
    title: str = "HIGHLIGHT",
    thumb: Path | None = None,
    thumb_t: float | None = None,
    font: str | None = None,
    fps: int = 30,
    crf: int = 19,
    preset: str = "medium",
    title_seconds: float = TITLE_SECONDS,
) -> dict[str, Any]:
    """Cut and reframe a single clip as a vertical Short."""
    if not Path(src).is_file():
        raise ClipForgeError(f"Render source not found: {src}")
    src_duration = duration_of(src)
    if start < 0:
        start = 0.0
    if start >= src_duration:
        raise ClipForgeError(
            f"Clip start {start:.2f}s is past the end of {src} ({src_duration:.2f}s)"
        )
    dur = min(float(duration), max(0.1, src_duration - start), 59.95)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    font_path = find_font(font)
    title_text = escape_drawtext(title or "HIGHLIGHT")

    # Blurred 9:16 background; foreground scaled to width so a side facecam stays in frame.
    filter_complex = (
        "[0:v]split=2[bg][fg];"
        f"[bg]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},gblur=sigma=28,eq=brightness=-0.08[bg2];"
        f"[fg]scale={WIDTH}:-2[fg2];"
        "[bg2][fg2]overlay=(W-w)/2:(H-h)/2:format=auto,format=yuv420p,"
        f"drawtext=fontfile={font_path}:text='{title_text}':"
        "fontsize=78:fontcolor=white:borderw=6:bordercolor=black:"
        f"x=(w-text_w)/2:y=150:enable='lt(t,{title_seconds})'[vout]"
    )
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{dur:.3f}",
        "-i",
        str(src),
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-profile:v",
        "high",
        "-level",
        "4.2",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-t",
        f"{dur:.3f}",
        "-movflags",
        "+faststart",
        str(out),
    ]
    log.info("Rendering %s (ss=%.2f dur=%.2f) from %s", out.name, start, dur, Path(src).name)
    t0 = time.time()
    run(cmd, check=True, capture=True)
    log.info("  encoded in %.1fs -> %s", time.time() - t0, out)

    if thumb is not None:
        thumb = Path(thumb)
        thumb.parent.mkdir(parents=True, exist_ok=True)
        tt = thumb_t if thumb_t is not None else min(max(dur * 0.35, 0.2), max(dur - 0.2, 0))
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{tt:.3f}",
                "-i",
                str(out),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(thumb),
            ],
            check=True,
            capture=True,
        )
    return _verify(out, thumb, requested=dur)


def _verify(out: Path, thumb: Path | None, requested: float) -> dict[str, Any]:
    info = probe(out)
    fmt = info.get("format") or {}
    streams = streams_by_type(info)
    video = streams.get("video") or {}
    audio = streams.get("audio") or {}
    duration = float(fmt.get("duration") or 0)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    ok = (
        0.5 <= duration <= 60.05
        and width == WIDTH
        and height == HEIGHT
        and bool(video.get("codec_name"))
    )
    if not ok:
        log.warning(
            "Verification warning for %s: dur=%.3f %sx%s v=%s a=%s",
            out,
            duration,
            width,
            height,
            video.get("codec_name"),
            audio.get("codec_name"),
        )
    rec = {
        "path": str(out),
        "thumb": str(thumb) if thumb is not None else None,
        "duration": duration,
        "requested_duration": requested,
        "width": width,
        "height": height,
        "fps": video.get("r_frame_rate"),
        "vcodec": video.get("codec_name"),
        "acodec": audio.get("codec_name"),
        "size": int(fmt.get("size") or 0),
        "ok": ok,
    }
    log.info(
        "  verify dur=%.3fs %sx%s v=%s a=%s ok=%s",
        duration,
        width,
        height,
        video.get("codec_name"),
        audio.get("codec_name"),
        ok,
    )
    return rec


def _default_source(work: WorkDir) -> Path:
    return work.analysis_video()


def _resolve_source(
    moment: dict[str, Any],
    src_files: list[dict[str, Any]] | None,
    fallback: Path,
) -> tuple[Path, float]:
    """Return (source_file, start_offset_in_that_file)."""
    start = float(moment["start"])
    if src_files:
        for item in src_files:
            path = item.get("path")
            pad_start = item.get("pad_start")
            pad_end = item.get("pad_end")
            if not path or pad_start is None:
                continue
            if Path(path).is_file() and pad_start - 0.5 <= start < (pad_end or pad_start + 1e9):
                return Path(path), max(0.0, start - float(pad_start))
    return fallback, start
