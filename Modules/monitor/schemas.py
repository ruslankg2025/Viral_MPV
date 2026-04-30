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
    interval_min: int = Field(default=60, ge=5, le=10080)
    # per-source override лимита постов за Apify run (IG/TT).
    # None → используется plan.max_results_limit.
    max_results_limit: int | None = Field(default=None, ge=1, le=200)


class SourcePatch(BaseModel):
    priority: int | None = None
    interval_min: int | None = Field(default=None, ge=5, le=10080)
    tags: list[str] | None = None
    is_active: bool | None = None
    niche_slug: str | None = None
    max_results_limit: int | None = Field(default=None, ge=1, le=200)
    is_self: bool | None = None


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
    full_name: str | None = None
    followers_count: int | None = None
    posts_count: int | None = None
    avatar_url: str | None = None
    is_verified: bool | None = None
    is_private: bool | None = None
    business_category: str | None = None
    profile_fetched_at: str | None = None
    is_self: bool = False
    # Vitality — классификация «здоровья» автора, считается на лету
    vitality: Literal["active", "slow", "silent", "empty", "broken"] = "active"
    last_video_age_days: float | None = None
    # Автор «только что вернулся» — последний рилс <2 дней при предыдущем >7
    just_resumed: bool = False


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
    # V13: analyze-pipeline state (заполняется orchestrator-ом через PATCH)
    sha256: str | None = None
    orchestrator_run_id: str | None = None
    script_id: str | None = None
    analysis_done_at: str | None = None


class VideoAnalysisPatch(BaseModel):
    """V13 PATCH /videos/{id}/analysis — orchestrator пишет результат после
    успешного pipeline (или промежуточно: только run_id перед завершением)."""
    orchestrator_run_id: str | None = None
    script_id: str | None = None
    sha256: str | None = None
    analysis_done_at: str | None = None


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
    velocity: float | None = None       # views/hour с момента публикации
    is_rising: bool = False             # velocity ускоряется (last 3 snapshots)
    niche_percentile: float | None = None  # [0..1] позиция среди своей ниши; None при < 10 видео в нише
    computed_at: str
    author_full_name: str | None = None
    author_avatar_url: str | None = None
    author_is_verified: bool | None = None
    author_followers_count: int | None = None


# ---------------- Watchlist ----------------

WatchlistStatus = Literal["active", "hit", "miss", "stalled", "closed"]


class WatchlistItem(TrendingItem):
    """Видео «на мониторинге» — TrendingItem + baseline/статус + дельта день-к-дню."""
    watchlist_id: int
    added_at: str
    expires_at: str
    status: WatchlistStatus
    reason: str
    initial_views: int
    initial_velocity: float | None = None
    views_yesterday: int | None = None       # snap ≥24ч назад
    delta_24h_abs: int | None = None
    delta_24h_pct: float | None = None        # (current - yesterday) / yesterday
    days_on_watch: float = 0.0                # плавающее: (now - added_at) в днях
    ttl_days_total: float = 0.0               # (expires - added) в днях
    graduated_at: str | None = None
    hit_reason: str | None = None
    # Sparkline: компактная серия [(timestamp_ms, views), ...] для отрисовки
    # mini-графика прямо на карточке Monitor. Считается из metric_snapshots
    # за весь срок мониторинга, до 12 точек.
    views_series: list[list[float]] = []


class WatchlistRunResponse(BaseModel):
    added: int
    graduated: int
    expired: int
    candidates_seen: int


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
    min_interval_min: int | None = Field(default=None, ge=5, le=10080)
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


# ---------------- Analytics: heatmap / ER trend / hashtags ----------------


class HeatmapCell(BaseModel):
    dow: int      # 0=воскресенье..6=суббота (UTC, strftime %w)
    hour: int     # 0..23 UTC
    posts: int
    avg_velocity: float | None = None
    avg_views: float | None = None
    avg_er: float | None = None


class PostingHeatmapResponse(BaseModel):
    source_id: str
    handle: str
    days: int
    cells: list[HeatmapCell]


class ErTrendBucket(BaseModel):
    period_start: str   # ISO date (YYYY-MM-DD), начало недели/дня
    avg_er: float
    posts_in_period: int


class ErTrendResponse(BaseModel):
    source_id: str
    handle: str
    days: int
    granularity: Literal["week", "day"]
    buckets: list[ErTrendBucket]


class HashtagSummary(BaseModel):
    tag: str
    posts_count: int
    authors_using: int
    avg_views: float | None = None
    avg_er: float | None = None
    posts_last_week: int
    prev_week: int
    weekly_growth: float | None = None  # (this - prev) / prev


class HashtagSummaryResponse(BaseModel):
    account_id: str
    days: int
    sort: Literal["count", "growth", "avg_views", "avg_er"]
    items: list[HashtagSummary]


class HashtagVideoItem(BaseModel):
    video_id: str
    source_id: str
    platform: Platform
    url: str
    title: str | None
    description: str | None
    thumbnail_url: str | None
    duration_sec: int | None
    published_at: str | None
    niche_slug: str | None
    handle: str
    channel_name: str | None
    is_self: bool = False
    current_views: int | None = None
    current_likes: int | None = None
    current_comments: int | None = None
    engagement_rate: float | None = None
    velocity: float | None = None


class HashtagVideosResponse(BaseModel):
    tag: str
    account_id: str
    days: int
    items: list[HashtagVideoItem]


# ---------------- Analyze stub ----------------

class AnalyzePayloadResponse(BaseModel):
    video_id: str
    file_path: str | None  # null, пока нет downloader
    source_url: str
    title: str | None
    hints: dict[str, Any]
