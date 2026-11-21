"""Tests for yt-dlp proxy/cookie options and secret redaction."""

from __future__ import annotations

import subprocess

import pytest

from clipforge import ingest
from clipforge.util import ClipForgeError, redact_command, redact_credentials


def test_proxy_and_cookie_options_are_inserted_before_url(monkeypatch) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append([str(item) for item in cmd])
        return subprocess.CompletedProcess([str(item) for item in cmd], 0, stdout="{}", stderr="")

    monkeypatch.setattr(ingest, "run", fake_run)
    ingest._yt_dlp_with_client(
        ["yt-dlp", "-J", "https://www.youtube.com/watch?v=abc12345678"],
        "tv_embedded",
        capture=True,
        proxy="http://user:pass@proxy.example:8080",
        cookies_file="/data/secrets/youtube-cookies.txt",
    )

    cmd = captured[0]
    assert cmd[-1].startswith("https://")
    assert "--proxy" in cmd
    assert cmd[cmd.index("--proxy") + 1] == "http://user:pass@proxy.example:8080"
    assert "--cookies" in cmd
    assert cmd[cmd.index("--cookies") + 1] == "/data/secrets/youtube-cookies.txt"
    assert "--extractor-args" in cmd


def test_redact_command_hides_proxy_credentials() -> None:
    cmd = [
        "yt-dlp",
        "--proxy",
        "http://user:secret@proxy.example:8080",
        "--cookies",
        "/tmp/cookies.txt",
        "https://example.com",
    ]
    redacted = redact_command(cmd)
    joined = " ".join(redacted)
    assert "secret" not in joined
    assert "user" not in joined
    assert "<redacted>" in joined
    assert redacted[-1] == "https://example.com"


def test_redact_credentials_in_errors() -> None:
    text = "proxy failed: http://user:secret@proxy.example:8080/path"
    redacted = redact_credentials(text)
    assert "secret" not in redacted
    assert "user" not in redacted
    assert "<redacted>@" in redacted


def test_invalid_proxy_scheme_is_rejected() -> None:
    with pytest.raises(ClipForgeError):
        ingest._normalize_proxy("ftp://proxy.example:21")


def test_missing_cookies_file_is_rejected() -> None:
    with pytest.raises(ClipForgeError):
        ingest._normalize_cookies_file("/definitely/missing/cookies.txt")


def test_bot_check_maps_to_actionable_error(monkeypatch) -> None:
    def fake_client(*args, **kwargs):
        raise ClipForgeError("ERROR: Sign in to confirm you're not a bot")

    monkeypatch.setattr(ingest, "_yt_dlp_with_client", fake_client)
    with pytest.raises(ClipForgeError) as caught:
        ingest._yt_dlp_with_fallback(["yt-dlp", "-J", "https://example.com"])
    assert "residential proxy" in str(caught.value)
    assert "upload the video file" in str(caught.value)
