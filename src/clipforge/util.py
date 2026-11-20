"""Shared helpers: timestamps, subprocess, fonts, media probe, work directories."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("clipforge")

YOUTUBE_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|live/|shorts/|embed/|v/))([A-Za-z0-9_-]{11})"
)
BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

FONT_CANDIDATES = [
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
    Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    Path("/Library/Fonts/Arial Bold.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
]


class ClipForgeError(Exception):
    """User-facing error with a clear message."""


def parse_timestamp(value: str | int | float) -> float:
    """Parse a timestamp like 00:04:14, 4:14, or raw seconds into seconds."""
    if isinstance(value, (int, float)):
        if value < 0:
            raise ClipForgeError(f"Timestamp cannot be negative: {value}")
        return float(value)
    text = str(value).strip()
    if not text:
        raise ClipForgeError("Empty timestamp")
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    parts = text.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError as exc:
        raise ClipForgeError(f"Invalid timestamp: {value!r}") from exc
    if any(n < 0 for n in nums):
        raise ClipForgeError(f"Invalid timestamp: {value!r}")
    if len(parts) == 3:
        hours, minutes, seconds = nums
    elif len(parts) == 2:
        hours, minutes, seconds = 0.0, nums[0], nums[1]
    else:
        raise ClipForgeError(f"Invalid timestamp: {value!r}")
    if minutes >= 60 or seconds >= 60:
        raise ClipForgeError(f"Invalid timestamp: {value!r}")
    return hours * 3600 + minutes * 60 + seconds


def parse_timestamp_list(value: str | None) -> list[float]:
    if not value:
        return []
    return [parse_timestamp(part.strip()) for part in value.split(",") if part.strip()]


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    total = max(0, int(round(float(seconds))))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def clamp_clip_duration(max_len: float) -> float:
    """Keep clips at or under 60s; default/60s requests encode as 59.95s."""
    if max_len <= 0:
        raise ClipForgeError("--max-len must be greater than 0")
    if max_len > 60:
        raise ClipForgeError("--max-len cannot be greater than 60 seconds")
    if max_len >= 59.95:
        return 59.95
    return float(max_len)


def slugify(text: str, fallback: str = "clip", max_len: int = 40) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_").lower()
    return (text[:max_len] or fallback)


def extract_video_id(url_or_id: str) -> str | None:
    text = (url_or_id or "").strip()
    match = YOUTUBE_ID_RE.search(text)
    if match:
        return match.group(1)
    if BARE_ID_RE.fullmatch(text):
        return text
    return None


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def require_bin(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ClipForgeError(
            f"{name} is not installed or not on PATH. See the README for setup instructions."
        )
    return path


def run(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    cwd: Path | str | None = None,
) -> subprocess.CompletedProcess:
    log.debug("$ %s", " ".join(str(c) for c in cmd))
    result = subprocess.run(
        [str(c) for c in cmd],
        check=False,
        cwd=str(cwd) if cwd else None,
        capture_output=capture,
        text=capture,
    )
    if check and result.returncode != 0:
        stderr = ""
        if capture:
            stderr = (result.stderr or result.stdout or "").strip()
        raise ClipForgeError(
            f"Command failed ({result.returncode}): {' '.join(str(c) for c in cmd[:8])}"
            + (f"\n{stderr[-2000:]}" if stderr else "")
        )
    return result


def find_font(explicit: str | None = None) -> str:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise ClipForgeError(f"Font file not found: {explicit}")
        return str(path)
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    raise ClipForgeError(
        "No bold sans-serif font found. Install fonts-liberation or pass --font /path/to/font.ttf"
    )


def probe(path: Path | str) -> dict[str, Any]:
    require_bin("ffprobe")
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,bit_rate:stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels,bit_rate",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ClipForgeError(f"ffprobe failed for {path}: {result.stderr.strip()}")
    return json.loads(result.stdout or "{}")


def duration_of(path: Path | str) -> float:
    info = probe(path)
    return float((info.get("format") or {}).get("duration") or 0)


def streams_by_type(info: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for stream in info.get("streams") or []:
        kind = stream.get("codec_type")
        if kind and kind not in out:
            out[kind] = stream
    return out


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("'", r"\'")
        .replace(":", r"\:")
        .replace("%", r"\%")
    )


def escape_filter_path(path: str | Path) -> str:
    """Quote and escape a path for use as an ffmpeg filtergraph option value (e.g. fontfile)."""
    text = str(path).replace("\\", "/").replace("'", r"\'").replace(":", r"\:")
    return f"'{text}'"


@dataclass
class WorkDir:
    """Standard per-video layout under an output root."""

    root: Path
    video_id: str

    def ensure(self) -> "WorkDir":
        for path in (self.base, self.src1080, self.shorts, self.thumbs):
            path.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def base(self) -> Path:
        return self.root / self.video_id

    @property
    def metadata(self) -> Path:
        return self.base / "metadata.json"

    @property
    def comments(self) -> Path:
        return self.base / "comments.json"

    @property
    def video360(self) -> Path:
        return self.base / "video360.mp4"

    @property
    def audio(self) -> Path:
        return self.base / "audio.m4a"

    @property
    def transcript(self) -> Path:
        return self.base / "transcript.json"

    @property
    def candidates(self) -> Path:
        return self.base / "highlight_candidates.json"

    @property
    def src1080(self) -> Path:
        return self.base / "src1080"

    @property
    def shorts(self) -> Path:
        return self.base / "shorts"

    @property
    def thumbs(self) -> Path:
        return self.base / "thumbs"

    @property
    def manifest(self) -> Path:
        return self.base / "CLIPS_MANIFEST.json"

    def analysis_video(self) -> Path:
        """Best available full-length source for analysis or fallback renders."""
        if self.video360.is_file():
            return self.video360
        meta = self.load_metadata()
        source = meta.get("source_path")
        if source and Path(source).is_file():
            return Path(source)
        raise ClipForgeError(f"No analysis video found in {self.base}")

    def audio_path(self) -> Path:
        if self.audio.is_file():
            return self.audio
        raise ClipForgeError(f"No analysis audio found at {self.audio}")

    def load_metadata(self) -> dict[str, Any]:
        if not self.metadata.is_file():
            raise ClipForgeError(f"Missing metadata.json in {self.base}. Run ingest first.")
        return read_json(self.metadata)

    def load_comments(self) -> list[dict[str, Any]]:
        if not self.comments.is_file():
            return []
        data = read_json(self.comments)
        if isinstance(data, list):
            return data
        return data.get("comments") or []
