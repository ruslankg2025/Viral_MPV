from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Platform = Literal["youtube", "instagram", "tiktok", "vk"]
CrawlStatus = Literal["running", "ok", "failed"]


# ---------------- Source ----------------

class SourceCreate(BaseModel):
    account_id: str = Field(min_length=1)
    platform: Platform = "youtube"
    channel_url: str = Field(min_length=1)
    niche_slug: str | None = None
    tags: list[str] = Field(default_factory=list)
    priority: int = 100
    interval_min: int = Field(default=60, ge=5, le=1440)
    # per-source override лимита постов за Apify run (IG/TT).
    # None → используется plan.max_results_limit.
    max_results_limit: int | None = Field(default=None, ge=1, le=200)


class SourcePatch(BaseModel):
    priority: int | None = None
    interval_min: int | None = Field(default=None, ge=5, le=1440)
    tags: list[str] | None = None
    is_active: bool | None = None
    niche_slug: str | None = None
    max_results_limit: int | None = Field(default=None, ge=1, le=200)


class SourceResponse(BaseModel):
    id: str
    account_id: str
    platform: Platform
    channel_url: str
    external_id: str
    channel_name: str | None
    niche_slug: str | None
    tags: list[str]
    priority: int
    interval_min: int
    is_active: bool
    profile_validated: bool
    last_error: str | None
    added_at: str
    last_crawled_at: str | None
    max_results_limit: int | None = None


# ---------------- Video ----------------

class VideoResponse(BaseModel):
    id: str
    source_id: str
    platform: Platform
    external_id: str
    url: str
    title: str | None
    description: str | None
    thumbnail_url: str | None
    duration_sec: int | None
    published_at: str | None
    first_seen_at: str
    is_short: bool = False


class MetricSnapshot(BaseModel):
    captured_at: str
    views: int
    likes: int
    comments: int
    engagement_rate: float | None


class VideoDetailResponse(VideoResponse):
    snapshots: list[MetricSnapshot]
    current_views: int = 0
    current_likes: int = 0
    current_comments: int = 0


# ---------------- Trending ----------------

class TrendingItem(BaseModel):
    video_id: str
    external_id: str
    title: str | None
    url: str
    platform: Platform
    channel_name: str | None
    channel_external_id: str
    niche_slug: str | None
    thumbnail_url: str | None = None
    published_at: str | None
    hours_since_published: float | None = None
    current_views: int
    current_likes: int = 0
    current_comments: int = 0
    zscore_24h: float | None
    growth_rate_24h: float | None
    is_trending: bool
    computed_at: str


# ---------------- Crawl log ----------------

class CrawlLogEntry(BaseModel):
    id: int
    source_id: str
    started_at: str
    finished_at: str | None
    status: CrawlStatus
    videos_new: int
    videos_updated: int
    error: str | None


# ---------------- Admin ----------------

class QuotaResponse(BaseModel):
    date: str
    units_used: int
    limit: int = 10000
    percent: float


class PlatformInfo(BaseModel):
    name: str
    configured: bool
    fake_mode: bool


class ApifyUsageEntry(BaseModel):
    platform: str
    runs: int
    items: int


class ApifyUsageResponse(BaseModel):
    date: str
    entries: list[ApifyUsageEntry]


# ---------------- Plan / tariff ----------------

class PlanLimitsResponse(BaseModel):
    plan_name: str
    max_sources_total: int
    min_interval_min: int
    max_results_limit: int
    crawl_anchor_utc: str
    updated_at: str
    # Динамические метрики
    sources_used: int


class PlanLimitsUpdate(BaseModel):
    plan_name: str | None = None
    max_sources_total: int | None = Field(default=None, ge=1, le=100000)
    min_interval_min: int | None = Field(default=None, ge=5, le=1440)
    max_results_limit: int | None = Field(default=None, ge=1, le=500)
    crawl_anchor_utc: str | None = Field(
        default=None, pattern=r"^\d{2}:\d{2}$"
    )


class SchedulerJobInfo(BaseModel):
    id: str
    next_run_time: str | None
    trigger: str


class SchedulerStateResponse(BaseModel):
    running: bool
    jobs: list[SchedulerJobInfo]


class HealthResponse(BaseModel):
    status: str = "ok"
    fake_mode: bool
    active_sources: int
    scheduler_running: bool
    youtube_quota_used_percent: float
    pending_crawls: int
    last_crawl_at: str | None


# ---------------- Analyze stub ----------------

class AnalyzePayloadResponse(BaseModel):
    video_id: str
    file_path: str | None  # null, пока нет downloader
    source_url: str
    title: str | None
    hints: dict[str, Any]
