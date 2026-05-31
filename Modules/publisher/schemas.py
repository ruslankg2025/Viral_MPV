"""Pydantic-схемы publisher. Форма publication зафиксирована контрактом."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Площадки. dzen добавится в Phase 2 (web-automation), сейчас — без него.
Platform = Literal["vk_video", "vk_clips", "telegram", "youtube_shorts", "pinterest"]
Status = Literal["dry_run", "scheduled", "publishing", "published", "failed"]


class PublicationOut(BaseModel):
    id: str
    platform: Platform
    external_id: str | None = None
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    status: Status
    scheduled_at: str | None = None
    published_at: str | None = None
    error_message: str | None = None


class PublishReq(BaseModel):
    video_path: str
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    platforms: list[Platform] = Field(default_factory=lambda: ["vk_video"])
    dry_run: bool | None = None  # None → берём DEFAULT_DRY_RUN из env


class ScheduleReq(PublishReq):
    scheduled_at: str  # iso-время, когда публиковать


class UploadResp(BaseModel):
    video_path: str  # путь к сохранённому файлу под MEDIA_DIR (читает publisher)
    file_name: str | None = None


class PublishResp(BaseModel):
    publication_ids: list[str]
    status: str
    # Phase 4 anti-spam: мягкие предупреждения (rate-cap / content-cooldown).
    # Не ломает существующий контракт — поле опциональное, по умолчанию пустое.
    warnings: list[str] = Field(default_factory=list)


class RetryResp(BaseModel):
    status: str
