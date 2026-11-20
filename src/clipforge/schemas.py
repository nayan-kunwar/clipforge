"""Pydantic request models for the web API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, EmailStr, Field


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class InviteAcceptIn(BaseModel):
    token: str
    password: str
    email: EmailStr | None = None


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


class ProjectCreateIn(BaseModel):
    youtube_url: str | None = None
    upload_id: str | None = None
    title: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class MomentIn(BaseModel):
    id: str | None = None
    start: float
    end: float | None = None
    peak: float | None = None
    title: str | None = None
    source: str | None = None
    score: float | None = None
    why: str | None = None
    enabled: bool = True


class MomentsPatchIn(BaseModel):
    moments: list[MomentIn] = Field(default_factory=list)


class RenderIn(BaseModel):
    moment_ids: list[str] | None = None
    fps: int = 30
    crf: int = 19
    preset: str = "veryfast"


class UploadInitIn(BaseModel):
    filename: str
    size: int
    content_type: str | None = None
    sha256: str | None = None


class ClipPatchIn(BaseModel):
    overlay: str | None = None
    suggested_title: str | None = None
    suggested_description: str | None = None
    tags: list[str] | None = None


class UploadClipsIn(BaseModel):
    clip_ids: list[str] = Field(default_factory=list)
    privacy: str = "private"


class InviteCreateIn(BaseModel):
    email: EmailStr | None = None
    expires_days: int = 7


class AdminUserPatchIn(BaseModel):
    is_active: bool | None = None
    quota_bytes: int | None = None
    upload_daily_cap: int | None = None


class AdminSettingsPatchIn(BaseModel):
    max_upload_bytes: int | None = None
    max_source_seconds: float | None = None
    user_storage_bytes: int | None = None
    upload_daily_global: int | None = None
    upload_daily_per_user: int | None = None


__all__ = [
    "AdminSettingsPatchIn",
    "AdminUserPatchIn",
    "ChangePasswordIn",
    "ClipPatchIn",
    "ForgotPasswordIn",
    "InviteAcceptIn",
    "InviteCreateIn",
    "LoginIn",
    "MomentIn",
    "MomentsPatchIn",
    "ProjectCreateIn",
    "RenderIn",
    "ResetPasswordIn",
    "UploadClipsIn",
    "UploadInitIn",
]
