"""Application settings loaded from the environment and ``.env``."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLIPFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    public_base_url: str = "http://localhost:8000"
    secret_key: str = "dev-secret-change-me"
    token_encryption_key: str = ""
    data_dir: Path = Path("data")
    database_url: str | None = None
    web_dist: Path = Path("web/dist")

    session_cookie_secure: bool = False
    session_days: int = 30
    max_invited_users: int = 5
    max_upload_bytes: int = 4 * 1024**3
    max_source_seconds: float = 7200.0
    user_storage_bytes: int = 15 * 1024**3
    upload_chunk_bytes: int = 8 * 1024 * 1024
    upload_daily_global: int = 5
    upload_daily_per_user: int = 2
    default_skip_transcript: bool = True
    default_whisper_model: str = "base"
    default_ffmpeg_preset: str = "veryfast"
    ytdlp_proxy: str = ""
    ytdlp_cookies_file: str = ""
    media_worker_concurrency: int = 1
    upload_worker_concurrency: int = 2

    brevo_api_key: str = ""
    brevo_sender_email: str = "no-reply@nayankunwar.com"
    brevo_sender_name: str = "ClipForge"
    brevo_timeout_seconds: float = 15.0

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""

    admin_email: str = ""
    admin_initial_password: str = ""
    allowed_hosts: str = "*"

    job_event_retention_days: int = 30
    upload_ttl_hours: int = 24
    log_level: str = "INFO"

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "clipforge.db"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.sqlite_path.as_posix()}"

    @property
    def resolved_google_redirect_uri(self) -> str:
        if self.google_redirect_uri:
            return self.google_redirect_uri
        return f"{self.public_base_url.rstrip('/')}/api/v1/oauth/google/callback"

    @property
    def allowed_host_list(self) -> list[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()] or ["*"]

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.data_dir / "uploads", self.data_dir / "users"):
            path.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()


__all__ = ["Settings", "get_settings"]
