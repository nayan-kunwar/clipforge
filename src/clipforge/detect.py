"""Find and rank highlight moments from comments, audio peaks, transcripts, and user times."""

from __future__ import annotations

import logging
import math
import re
import subprocess
from collections import defaultdict
from typing import Any, Iterable

import numpy as np

from clipforge.util import (
    ClipForgeError,
    WorkDir,
    duration_of,
    format_timestamp,
    run,
    slugify,
    write_json,
)

log = logging.getLogger("clipforge")

# General highlight cues; gaming streams are a common use case, not the only one.
DEFAULT_KEYWORDS = (
    "wasted",
    "countdown",
    "crash",
    "chase",
    "police",
    "funny",
    "reaction",
    "fail",
    "rage",
    "laugh",
    "highlight",
    "moment",
    "legendary",
    "best",
    "peak",
    "intro",
    "ending",
    "clutch",
    "headshot",
    "killed",
    "death",
    "giveaway",
    "no way",
    "oh my god",
    "oh no",
    "lets go",
    "let's go",
)

COMMENT_TS_RE = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?![\d])")

USER_SCORE = 1000.0
AUDIO_SR = 8000


def detect_moments(
    work: WorkDir,
    *,
    count: int = 8,
    max_len: float = 59.95,
    user_timestamps: list[float] | None = None,
    keywords: Iterable[str] | None = None,
    skip_transcript: bool = False,
    whisper_model: str = "small",
    min_separation: float = 90.0,
    ignore_start: float | None = None,
) -> list[dict[str, Any]]:
    """Merge all signals and return up to `count` ranked moments."""
    if count < 1:
        raise ClipForgeError("--count must be at least 1")
    meta = work.load_metadata()
    duration = float(meta.get("duration_s") or 0)
    if duration <= 0:
        try:
            duration = duration_of(work.analysis_video())
        except ClipForgeError:
            duration = duration_of(work.audio_path()) if work.audio.is_file() else 0
    if duration <= 0:
        raise ClipForgeError("Could not determine source duration")

    clip_len = min(max_len, duration)
    keywords = tuple(keywords) if keywords is not None else DEFAULT_KEYWORDS
    user_timestamps = user_timestamps or []

    if ignore_start is None:
        ignore_start = 90.0 if duration > 5 * 60 else 0.0
    ignore_start = min(ignore_start, max(0.0, duration * 0.05))

    candidates: list[dict[str, Any]] = []

    for ts in user_timestamps:
        if ts < 0 or ts >= duration:
            log.warning("Skipping out-of-range timestamp %s (duration %.1fs)", ts, duration)
            continue
        start = min(ts, max(0.0, duration - clip_len))
        candidates.append(_candidate(
            start=start,
            peak=ts,
            duration=min(clip_len, duration - start),
            score=USER_SCORE,
            source="user",
            why=f"User-provided timestamp {format_timestamp(ts)}",
        ))

    comment_cands = parse_comment_timestamps(work.load_comments(), duration, clip_len)
    candidates.extend(comment_cands)
    log.info("Comment timestamp clusters: %s", len(comment_cands))

    audio_cands: list[dict[str, Any]] = []
    if work.audio.is_file() or work.video360.is_file():
        audio_path = work.audio if work.audio.is_file() else work.analysis_video()
        audio_cands = analyze_audio_peaks(
            audio_path,
            clip_len=clip_len,
            ignore_start=ignore_start,
        )
        candidates.extend(audio_cands)
        log.info("Audio peak windows: %s", len(audio_cands))
    else:
        log.warning("No audio available; skipping loudness analysis")

    if not skip_transcript:
        try:
            transcript_cands = transcribe_cues(
                work,
                seed_windows=_windows_for_transcript(candidates, duration, clip_len),
                keywords=keywords,
                clip_len=clip_len,
                model_name=whisper_model,
            )
            candidates.extend(transcript_cands)
            log.info("Transcript cue windows: %s", len(transcript_cands))
        except ClipForgeError as exc:
            log.warning("Transcript step skipped: %s", exc)

    ranked = merge_and_rank(
        candidates,
        count=count,
        clip_len=clip_len,
        duration=duration,
        min_separation=min_separation,
    )
    write_json(
        work.candidates,
        {
            "duration_s": duration,
            "count_requested": count,
            "selected": ranked,
            "raw_candidate_count": len(candidates),
        },
    )
    log.info("Selected %s moment(s)", len(ranked))
    for i, moment in enumerate(ranked, 1):
        log.info(
            "  %s %s (%ss) score=%.1f sources=%s — %s",
            i,
            format_timestamp(moment["start"]),
            int(round(moment["duration"])),
            moment["score"],
            ",".join(moment["sources"]),
            moment["title"],
        )
    return ranked


def parse_comment_timestamps(
    comments: list[dict[str, Any]],
    duration: float,
    clip_len: float,
) -> list[dict[str, Any]]:
    bins: dict[int, dict[str, Any]] = defaultdict(lambda: {"count": 0, "likes": 0, "times": []})
    for comment in comments:
        text = _comment_text(comment)
        likes = _comment_likes(comment)
        for match in COMMENT_TS_RE.finditer(text):
            hours = int(match.group(1) or 0)
            minutes = int(match.group(2))
            seconds = int(match.group(3))
            if minutes > 59 or seconds > 59:
                continue
            t = hours * 3600 + minutes * 60 + seconds
            if duration > 0 and t > duration:
                continue
            bucket = (t // 60) * 60
            bins[bucket]["count"] += 1
            bins[bucket]["likes"] += likes
            bins[bucket]["times"].append(t)

    out: list[dict[str, Any]] = []
    for bucket, info in bins.items():
        peak = int(np.median(info["times"])) if info["times"] else int(bucket)
        start = _start_around(peak, clip_len, duration, lead=8)
        score = 10.0 * info["count"] + 0.1 * info["likes"]
        out.append(_candidate(
            start=start,
            peak=peak,
            duration=min(clip_len, max(0.0, duration - start) or clip_len),
            score=score,
            source="comments",
            why=f"{info['count']} comment timestamp(s) near {format_timestamp(bucket)}",
            extra={"comment_count": info["count"], "comment_likes": info["likes"]},
        ))
    return out


def analyze_audio_peaks(
    audio_path,
    *,
    clip_len: float,
    ignore_start: float = 0.0,
    ignore_end_pad: float = 30.0,
) -> list[dict[str, Any]]:
    """Rank highlight windows from 1-second RMS, peak, and transient energy."""
    rms_db, peak_db, transient = _audio_features(audio_path)
    n = len(rms_db)
    if n < 3:
        log.warning("Audio is too short for peak analysis (%ss)", n)
        return []

    def moving_avg(x: np.ndarray, k: int) -> np.ndarray:
        k = max(1, int(k))
        kernel = np.ones(k, dtype=np.float32) / k
        return np.convolve(x, kernel, mode="same")

    def robust_z(x: np.ndarray) -> np.ndarray:
        med = np.median(x)
        mad = np.median(np.abs(x - med)) + 1e-6
        return (x - med) / (1.4826 * mad)

    score = 0.65 * robust_z(moving_avg(rms_db, 3)) + 0.35 * robust_z(moving_avg(transient, 2))
    energy = moving_avg(score, 8)
    start_i = min(int(ignore_start), max(0, n - 2))
    end_i = max(start_i + 1, n - int(ignore_end_pad) if n > ignore_end_pad + start_i + 5 else n)
    band = energy[start_i:end_i]
    if band.size == 0:
        return []
    threshold = float(np.percentile(band, 92))

    peaks: list[int] = []
    i = start_i
    while i < end_i:
        lo = max(start_i, i - 8)
        hi = min(end_i, i + 9)
        if energy[i] >= energy[lo:hi].max() and energy[i] >= threshold:
            peaks.append(i)
            i += 20
        else:
            i += 1

    windows: list[dict[str, Any]] = []
    for peak in peaks:
        start, end = _expand_window(energy, peak, start_i, end_i, threshold, n, clip_len)
        sl = energy[start:end]
        if sl.size == 0:
            continue
        win_score = float(np.percentile(sl, 80) + 0.35 * sl.max())
        windows.append(_candidate(
            start=float(start),
            peak=float(peak),
            duration=float(end - start),
            score=max(win_score, 0.1),
            source="audio",
            why=f"Audio energy peak at {format_timestamp(peak)}",
            extra={
                "max_energy": float(sl.max()),
                "mean_rms_db": float(np.mean(rms_db[start:end])),
                "max_peak_db": float(np.max(peak_db[start:end])),
            },
        ))
    return windows


def transcribe_cues(
    work: WorkDir,
    *,
    seed_windows: list[tuple[float, float]],
    keywords: Iterable[str],
    clip_len: float,
    model_name: str = "small",
) -> list[dict[str, Any]]:
    """Transcribe candidate windows (or a short full file) and score keyword hits."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ClipForgeError(
            "faster-whisper is not installed. Run: pip install 'clipforge[transcribe]' "
            "or pass --skip-transcript"
        ) from exc

    audio = work.audio_path()
    duration = duration_of(audio)
    keywords = [k.strip().lower() for k in keywords if k.strip()]
    if not keywords:
        return []

    windows = seed_windows
    if duration <= 15 * 60:
        windows = [(0.0, duration)]
    elif not windows:
        log.info("No seed windows for transcription on a long file; skipping")
        return []

    log.info("Loading faster-whisper model %s (CPU int8)", model_name)
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    all_segments: list[dict[str, Any]] = []
    tmpdir = work.base / "transcript_clips"
    tmpdir.mkdir(parents=True, exist_ok=True)

    for start, end in _merge_spans(windows, pad=2.0, limit=duration):
        length = max(0.1, end - start)
        snippet = tmpdir / f"win_{int(start)}_{int(end)}.wav"
        log.info("Transcribing %s–%s", format_timestamp(start), format_timestamp(end))
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
            "-i", str(audio),
            "-ac", "1", "-ar", "16000",
            str(snippet),
        ], check=True, capture=True)
        segments, info = model.transcribe(
            str(snippet), language=None, vad_filter=True, beam_size=3
        )
        lang = getattr(info, "language", None)
        for seg in segments:
            all_segments.append({
                "start": round(start + float(seg.start), 2),
                "end": round(start + float(seg.end), 2),
                "text": (seg.text or "").strip(),
                "lang": lang,
            })
        try:
            snippet.unlink(missing_ok=True)
        except OSError:
            pass

    work.transcript.write_text(
        __import__("json").dumps({"segments": all_segments}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    patterns = [(kw, re.compile(r"\b" + re.escape(kw) + r"\b", re.I)) for kw in keywords]
    hits: list[dict[str, Any]] = []
    for seg in all_segments:
        text = seg["text"]
        matched = [kw for kw, pat in patterns if pat.search(text)]
        if not matched:
            continue
        peak = float(seg["start"])
        start = _start_around(peak, clip_len, duration, lead=8)
        hits.append(_candidate(
            start=start,
            peak=peak,
            duration=_clip_duration(start, clip_len, duration),
            score=5.0 * len(matched) + (15.0 if any(len(m) > 6 for m in matched) else 0.0),
            source="transcript",
            why=f"Transcript cue ({', '.join(matched)}) at {format_timestamp(peak)}: {text[:120]}",
            extra={"keywords": matched, "text": text[:300]},
            title=_title_from_keywords(matched),
        ))
    return hits



def merge_and_rank(
    candidates: list[dict[str, Any]],
    *,
    count: int,
    clip_len: float,
    duration: float,
    min_separation: float,
) -> list[dict[str, Any]]:
    if not candidates:
        if duration > 0:
            log.warning("No signals found; falling back to a clip at the start")
            candidates = [_candidate(
                start=0.0,
                peak=0.0,
                duration=min(clip_len, duration),
                score=0.0,
                source="fallback",
                why="No highlight signals; used the beginning of the video",
            )]
        else:
            return []

    # Cluster candidates whose peaks (or starts) are close, summing scores.
    remaining = sorted(candidates, key=lambda c: -c["score"])
    clusters: list[dict[str, Any]] = []
    used = [False] * len(remaining)
    merge_gap = min(45.0, max(15.0, clip_len * 0.75))
    for i, cand in enumerate(remaining):
        if used[i]:
            continue
        group = [cand]
        used[i] = True
        for j, other in enumerate(remaining):
            if used[j]:
                continue
            if abs(other["peak"] - cand["peak"]) <= merge_gap or abs(other["start"] - cand["start"]) <= merge_gap:
                group.append(other)
                used[j] = True
        clusters.append(_merge_group(group, clip_len, duration))

    clusters.sort(key=lambda c: (-c["score"], c["start"]))
    selected: list[dict[str, Any]] = []
    # Always keep user-provided moments, even if they are close to each other.
    for cluster in clusters:
        is_user = "user" in cluster["sources"]
        if not is_user:
            if any(
                abs(cluster["start"] - s["start"]) < min_separation
                or abs(cluster["peak"] - s["peak"]) < min_separation
                for s in selected
            ):
                continue
        cluster["id"] = _moment_id(len(selected) + 1, cluster)
        cluster["duration"] = min(clip_len, max(1.0, cluster["duration"]), max(0.1, duration - cluster["start"]))
        if cluster["duration"] < 1:
            continue
        selected.append(cluster)
        if len(selected) >= count:
            break

    selected.sort(key=lambda c: c["start"])
    # Re-number in timeline order so filenames follow the source.
    for i, moment in enumerate(selected, 1):
        moment["id"] = _moment_id(i, moment)
    return selected


def _merge_group(group: list[dict[str, Any]], clip_len: float, duration: float) -> dict[str, Any]:
    # Prefer a user timestamp as the start; otherwise start around the best peak.
    user = next((c for c in group if c["source"] == "user"), None)
    best = max(group, key=lambda c: c["score"])
    peak = user["peak"] if user else best["peak"]
    start = user["start"] if user else _start_around(peak, clip_len, duration, lead=8)
    sources = []
    for cand in group:
        if cand["source"] not in sources:
            sources.append(cand["source"])
    why_parts = [c["why"] for c in group if c.get("why")]
    title = next((c["title"] for c in group if c.get("title") and c["source"] in {"transcript", "user"}), None)
    if not title:
        title = _title_from_sources(sources, group)
    return {
        "start": float(start),
        "peak": float(peak),
        "duration": _clip_duration(start, clip_len, duration),
        "score": float(sum(c["score"] for c in group)),
        "sources": sources,
        "why": " | ".join(why_parts[:4]),
        "title": title,
        "keywords": sorted({kw for c in group for kw in c.get("keywords") or []}),
    }


def _candidate(
    *,
    start: float,
    peak: float,
    duration: float,
    score: float,
    source: str,
    why: str,
    extra: dict[str, Any] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    rec = {
        "start": float(max(0.0, start)),
        "peak": float(max(0.0, peak)),
        "duration": float(duration),
        "score": float(score),
        "source": source,
        "why": why,
        "title": title or _title_from_sources([source], []),
        "keywords": (extra or {}).get("keywords") or [],
    }
    if extra:
        rec.update({k: v for k, v in extra.items() if k != "keywords"})
    return rec



def _clip_duration(start: float, clip_len: float, duration: float) -> float:
    if duration <= 0:
        return float(clip_len)
    return float(min(clip_len, max(0.1, duration - start)))


def _start_around(peak: float, clip_len: float, duration: float, lead: float = 8.0) -> float:
    start = peak - lead
    start = max(0.0, start)
    if duration > 0:
        start = min(start, max(0.0, duration - clip_len))
    return start


def _expand_window(
    energy: np.ndarray,
    peak: int,
    lo: int,
    hi: int,
    threshold: float,
    n: int,
    clip_len: float,
) -> tuple[int, int]:
    peak_e = energy[peak]
    start = peak
    while start > lo and (peak - start) < 15:
        if energy[start] < 0.45 * peak_e and energy[start] < threshold:
            break
        start -= 1
    start = max(0, start - 3)
    end = peak
    cap = int(min(55, clip_len))
    while end < hi and (end - start) < cap:
        if end > peak + 8 and energy[end] < 0.40 * peak_e and energy[end] < threshold:
            nxt = energy[end:min(end + 4, n)]
            if nxt.size and nxt.mean() < 0.40 * peak_e:
                break
        end += 1
    end = min(n, end + 2)
    length = end - start
    target = int(round(clip_len))
    if length < min(30, target) and target > length:
        need = min(target, n) - length
        left = min(need // 2 + need % 2, start)
        start = max(0, start - left)
        end = min(n, start + min(target, n))
    if end - start > target >= 1:
        pref = max(start, min(peak - 12, end - target))
        start = int(pref)
        end = int(start + target)
    return int(start), int(end)


def _audio_features(audio_path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-v",
        "error",
        "-i",
        str(audio_path),
        "-ac",
        "1",
        "-ar",
        str(AUDIO_SR),
        "-f",
        "s16le",
        "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    rms_db: list[float] = []
    peak_db: list[float] = []
    transient: list[float] = []
    win = AUDIO_SR  # 1 second
    buf = b""
    while True:
        chunk = proc.stdout.read(win * 2 * 8)
        if not chunk:
            break
        buf += chunk
        while len(buf) >= win * 2:
            frame = buf[: win * 2]
            buf = buf[win * 2 :]
            samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(samples * samples) + 1e-12))
            peak = float(np.max(np.abs(samples)) + 1e-12)
            tr = float(np.mean(np.abs(np.diff(samples)))) if len(samples) > 1 else 0.0
            rms_db.append(20 * math.log10(rms))
            peak_db.append(20 * math.log10(peak))
            transient.append(tr)
    proc.stdout.close()
    stderr = proc.stderr.read() if proc.stderr else b""
    ret = proc.wait()
    if ret != 0:
        err = stderr.decode("utf-8", errors="replace")[-500:]
        raise ClipForgeError(f"ffmpeg audio decode failed: {err}")
    return (
        np.array(rms_db, dtype=np.float32),
        np.array(peak_db, dtype=np.float32),
        np.array(transient, dtype=np.float32),
    )


def _comment_text(comment: dict[str, Any]) -> str:
    return str(
        comment.get("text")
        or comment.get("textOriginal")
        or comment.get("comment")
        or ""
    )


def _comment_likes(comment: dict[str, Any]) -> int:
    try:
        return int(comment.get("like_count") or comment.get("likeCount") or comment.get("votes") or 0)
    except (TypeError, ValueError):
        return 0


def _windows_for_transcript(
    candidates: list[dict[str, Any]],
    duration: float,
    clip_len: float,
) -> list[tuple[float, float]]:
    windows = []
    for cand in sorted(candidates, key=lambda c: -c["score"])[:24]:
        start = max(0.0, cand["start"] - 2)
        end = min(duration, cand["start"] + cand["duration"] + 2)
        if end > start:
            windows.append((start, end))
    return windows


def _merge_spans(
    windows: list[tuple[float, float]],
    *,
    pad: float,
    limit: float,
) -> list[tuple[float, float]]:
    spans = []
    for start, end in windows:
        spans.append((max(0.0, start - pad), min(limit, end + pad) if limit else end + pad))
    spans.sort()
    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 2:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _title_from_keywords(keywords: list[str]) -> str:
    if not keywords:
        return "HIGHLIGHT"
    # Prefer a punchy single-word cue when we have one.
    best = max(keywords, key=len)
    return best.upper()


def _title_from_sources(sources: list[str], group: list[dict[str, Any]]) -> str:
    for cand in group:
        if cand.get("title") and cand["title"] != "HIGHLIGHT":
            return cand["title"]
    if "transcript" in sources:
        return "HIGHLIGHT"
    if "comments" in sources:
        return "CHAT PICK"
    if "audio" in sources:
        return "LOUD MOMENT"
    if "user" in sources:
        return "CLIP"
    return "HIGHLIGHT"


def _moment_id(index: int, moment: dict[str, Any]) -> str:
    return f"{index:02d}_{slugify(moment.get('title') or 'highlight')}"
