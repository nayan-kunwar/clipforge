"""Tests for timestamp parsing and shared helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from clipforge.util import (
    ClipForgeError,
    clamp_clip_duration,
    escape_filter_path,
    parse_timestamp,
    parse_timestamp_list,
    slugify,
)


def test_parse_timestamp_variants() -> None:
    assert parse_timestamp("90") == 90.0
    assert parse_timestamp("1:30") == 90.0
    assert parse_timestamp("00:01:30") == 90.0
    assert parse_timestamp(12.5) == 12.5


def test_parse_timestamp_list() -> None:
    assert parse_timestamp_list("00:00:10, 1:05, 90") == [10.0, 65.0, 90.0]
    assert parse_timestamp_list("") == []


def test_parse_timestamp_rejects_invalid_values() -> None:
    with pytest.raises(ClipForgeError):
        parse_timestamp("1:99")
    with pytest.raises(ClipForgeError):
        parse_timestamp("not-a-time")
    with pytest.raises(ClipForgeError):
        parse_timestamp(-1)


def test_clamp_clip_duration() -> None:
    assert clamp_clip_duration(30) == 30.0
    assert clamp_clip_duration(59.95) == 59.95
    assert clamp_clip_duration(60) == 59.95
    with pytest.raises(ClipForgeError):
        clamp_clip_duration(75)


@pytest.mark.parametrize("value", [0, -1])
def test_clamp_clip_duration_rejects_non_positive(value: float) -> None:
    with pytest.raises(ClipForgeError):
        clamp_clip_duration(value)


def test_slugify() -> None:
    assert slugify("Test Clip!") == "test_clip"
    assert slugify("") == "clip"
    assert slugify("a" * 60, max_len=10) == "a" * 10


def test_escape_filter_path_quotes_and_escapes_windows_paths() -> None:
    escaped = escape_filter_path(Path(r"C:\Windows\Fonts\arialbd.ttf"))
    assert escaped == "'C\\:/Windows/Fonts/arialbd.ttf'"


def test_escape_filter_path_keeps_posix_paths() -> None:
    escaped = escape_filter_path("/usr/share/fonts/DejaVuSans-Bold.ttf")
    assert escaped == "'/usr/share/fonts/DejaVuSans-Bold.ttf'"
