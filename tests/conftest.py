"""Shared pytest configuration."""

from __future__ import annotations

import shutil

import pytest

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for media tests",
)
