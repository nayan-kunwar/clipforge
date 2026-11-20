"""Build and write CLIPS_MANIFEST.json for rendered Shorts."""

from __future__ import annotations

from typing import Any

from clipforge.util import WorkDir, format_timestamp, write_json

DEFAULT_TAGS = ["Shorts"]


def build_manifest(
    work: WorkDir,
    moments: list[dict[str, Any]],
    renders: list[dict[str, Any]],
    *,
    extra_tags: list[str] | None = None,
) -> dict[str, Any]:
    meta = work.load_metadata()
    channel = meta.get("channel") or meta.get("uploader") or ""
    url = meta.get("webpage_url") or meta.get("url") or ""
    video_id = meta.get("video_id") or meta.get("id") or work.video_id
    title = meta.get("title") or work.video_id
    tags_base = list(extra_tags or [])
    if channel and channel not in tags_base:
        tags_base.append(channel)

    by_id = {r.get("id"): r for r in renders if r.get("id")}
    # Fall back to zip-by-index if ids were not stamped yet.
    clips: list[dict[str, Any]] = []
    for index, moment in enumerate(moments):
        rec = by_id.get(moment["id"])
        if rec is None and index < len(renders):
            rec = renders[index]
        rec = rec or {}
        start = float(moment["start"])
        start_label = format_timestamp(start)
        clip_title = moment.get("title") or rec.get("title_overlay") or "HIGHLIGHT"
        suggested_title = _suggested_title(clip_title, channel, title)
        suggested_description = _suggested_description(
            clip_title=clip_title,
            channel=channel,
            url=url,
            start_label=start_label,
            source_title=title,
        )
        tags = list(DEFAULT_TAGS)
        for tag in tags_base + list(moment.get("keywords") or []):
            if tag and tag not in tags:
                tags.append(tag)
        clips.append({
            "id": moment.get("id") or rec.get("id"),
            "file": rec.get("path"),
            "thumb": rec.get("thumb"),
            "src_start": rec.get("src_start") or start_label,
            "src_start_sec": start,
            "length_s": rec.get("duration") or moment.get("duration"),
            "overlay": clip_title,
            "why": moment.get("why") or rec.get("why") or "",
            "sources": moment.get("sources") or rec.get("sources") or [],
            "score": moment.get("score"),
            "suggested_title": suggested_title,
            "suggested_description": suggested_description,
            "tags": tags,
            "ok": rec.get("ok"),
        })

    manifest = {
        "video_id": video_id,
        "title": title,
        "channel": channel,
        "channel_id": meta.get("channel_id") or "",
        "webpage_url": url,
        "source_type": meta.get("source_type") or "",
        "duration_s": meta.get("duration_s") or meta.get("duration"),
        "clips": clips,
        "not_uploaded": True,
    }
    return manifest


def write_manifest(work: WorkDir, manifest: dict[str, Any]) -> None:
    write_json(work.manifest, manifest)


def _suggested_title(clip_title: str, channel: str, source_title: str) -> str:
    if channel:
        return f"{clip_title} | {channel}"
    if source_title:
        return f"{clip_title} | {source_title}"
    return clip_title


def _suggested_description(
    *,
    clip_title: str,
    channel: str,
    url: str,
    start_label: str,
    source_title: str,
) -> str:
    lines = [clip_title, ""]
    if url:
        lines.append(f"Clip from {channel or 'the original video'}: {url} at {start_label}")
    elif source_title:
        lines.append(f"Clip from {source_title} at {start_label}")
    else:
        lines.append(f"Starts at {start_label}")
    if channel:
        lines.append(f"Credit: {channel}")
    lines.append("")
    lines.append("#Shorts")
    return "\n".join(lines)
