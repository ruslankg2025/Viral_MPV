"""FastAPI routes: /monitor/* (user) и /monitor/admin/* (admin)."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status


def _hours_since(published_at: str | None) -> float | None:
    """ISO8601 → hours since published (None если не парсится)."""
    if not published_at:
        return None
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return round(delta.total_seconds() / 3600.0, 2)
    except (ValueError, TypeError):
        return None

import profile_client
from auth import require_admin_token, require_token
from config import get_settings
from crawler import orchestrate_crawl
from platforms.youtube import YouTubeSource
from schemas import (
    AnalyzePayloadResponse,
    ApifyUsageEntry,
    ApifyUsageResponse,
    CrawlLogEntry,
    HealthResponse,
    MetricSnapshot,
    PlanLimitsResponse,
    PlanLimitsUpdate,
    PlatformInfo,
    QuotaResponse,
    SchedulerJobInfo,
    SchedulerStateResponse,
    SourceCreate,
    SourcePatch,
    SourceResponse,
    TrendingItem,
    VideoDetailResponse,
    VideoResponse,
)
from state import state

YOUTUBE_DAILY_LIMIT = 10000


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _source_to_response(row) -> SourceResponse:
    return SourceResponse(
        id=row.id,
        account_id=row.account_id,
        platform=row.platform,
        channel_url=row.channel_url,
        external_id=row.external_id,
        channel_name=row.channel_name,
        niche_slug=row.niche_slug,
        tags=row.tags,
        priority=row.priority,
        interval_min=row.interval_min,
        is_active=row.is_active,
        profile_validated=row.profile_validated,
        last_error=row.last_error,
        added_at=row.added_at,
        last_crawled_at=row.last_crawled_at,
        max_results_limit=row.max_results_limit,
    )


def _video_to_response(row) -> VideoResponse:
    return VideoResponse(
        id=row.id,
        source_id=row.source_id,
        platform=row.platform,
        external_id=row.external_id,
        url=row.url,
        title=row.title,
        description=row.description,
        thumbnail_url=row.thumbnail_url,
        duration_sec=row.duration_sec,
        published_at=row.published_at,
        first_seen_at=row.first_seen_at,
        is_short=row.is_short,
    )


# ------------------------------------------------------------------ #
# Routers
# ------------------------------------------------------------------ #

router = APIRouter(prefix="/monitor", tags=["monitor"], dependencies=[Depends(require_token)])
admin_router = APIRouter(
    prefix="/monitor/admin",
    tags=["monitor-admin"],
    dependencies=[Depends(require_admin_token)],
)
public_router = APIRouter(prefix="/monitor", tags=["monitor-public"])


# ---------------- Public: health ----------------

@public_router.get("/healthz", response_model=HealthResponse)
async def healthz():
    settings = get_settings()
    store = state.store
    scheduler = state.scheduler

    active_sources = store.count_active_sources() if store else 0
    pending_crawls = store.count_running_crawls() if store else 0
    last_crawl_at = store.last_crawl_time() if store else None
    quota_used = store.get_quota() if store else 0
    quota_percent = (quota_used / YOUTUBE_DAILY_LIMIT) * 100

    return HealthResponse(
        status="ok",
        fake_mode=settings.effective_fake_mode,
        active_sources=active_sources,
        scheduler_running=scheduler.running if scheduler else False,
        youtube_quota_used_percent=round(quota_percent, 2),
        pending_crawls=pending_crawls,
        last_crawl_at=last_crawl_at,
    )


# ---------------- Sources ----------------

@router.get("/sources", response_model=list[SourceResponse])
async def list_sources(account_id: str | None = Query(default=None)):
    rows = state.store.list_sources(account_id=account_id)
    return [_source_to_response(r) for r in rows]


@router.post("/sources", response_model=SourceResponse, status_code=201)
async def create_source(body: SourceCreate):
    settings = get_settings()
    store = state.store
    plan = store.get_plan()

    # 0. Plan cap: max_sources_total
    if store.count_sources_total() >= plan.max_sources_total:
        raise HTTPException(
            409,
            detail=f"plan_limit_reached: max_sources_total={plan.max_sources_total}",
        )

    # 1. Resolve channel через platform
    platform = state.platforms.get(body.platform)
    if platform is None:
        raise HTTPException(400, detail=f"platform_not_configured: {body.platform}")

    try:
        channel = await platform.resolve_channel(body.channel_url)
    except Exception as e:
        raise HTTPException(400, detail=f"resolve_failed: {type(e).__name__}: {e}")

    # 2. Validate account в profile (non-blocking)
    profile_valid = await profile_client.validate_account(
        settings.profile_base_url, settings.profile_token, body.account_id
    )

    # 3. Check duplicate
    existing = store.list_sources(account_id=body.account_id)
    for s in existing:
        if s.platform == body.platform and s.external_id == channel.external_id:
            raise HTTPException(409, detail=f"source_already_exists: {s.id}")

    # 4. Clamp interval_min к plan.min_interval_min (floor).
    effective_interval = max(body.interval_min, plan.min_interval_min)

    # 5. Create
    try:
        row = store.create_source(
            account_id=body.account_id,
            platform=body.platform,
            channel_url=body.channel_url,
            external_id=channel.external_id,
            channel_name=channel.channel_name,
            niche_slug=body.niche_slug,
            tags=body.tags,
            priority=body.priority,
            interval_min=effective_interval,
            profile_validated=profile_valid,
            max_results_limit=body.max_results_limit,
        )
    except Exception as e:
        raise HTTPException(400, detail=f"create_failed: {e}")

    # 6. Schedule
    if state.scheduler and state.scheduler.running:
        state.scheduler.add_source_job(row.id, row.interval_min)

    return _source_to_response(row)


@router.get("/sources/{source_id}", response_model=SourceResponse)
async def get_source(source_id: str):
    row = state.store.get_source(source_id)
    if row is None:
        raise HTTPException(404, detail="source_not_found")
    return _source_to_response(row)


@router.patch("/sources/{source_id}", response_model=SourceResponse)
async def patch_source(source_id: str, body: SourcePatch):
    existing = state.store.get_source(source_id)
    if existing is None:
        raise HTTPException(404, detail="source_not_found")

    # Clamp interval_min к plan.min_interval_min.
    interval_min = body.interval_min
    if interval_min is not None:
        plan = state.store.get_plan()
        interval_min = max(interval_min, plan.min_interval_min)

    updated = state.store.update_source(
        source_id,
        priority=body.priority,
        interval_min=interval_min,
        tags=body.tags,
        is_active=body.is_active,
        niche_slug=body.niche_slug,
        max_results_limit=body.max_results_limit,
    )

    # Обновить scheduler, если изменился интервал или is_active
    if state.scheduler and state.scheduler.running and updated is not None:
        if updated.is_active:
            state.scheduler.add_source_job(updated.id, updated.interval_min)
        else:
            state.scheduler.remove_source_job(updated.id)

    return _source_to_response(updated)  # type: ignore[arg-type]


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: str):
    existing = state.store.get_source(source_id)
    if existing is None:
        raise HTTPException(404, detail="source_not_found")
    state.store.delete_source(source_id)
    if state.scheduler and state.scheduler.running:
        state.scheduler.remove_source_job(source_id)


@router.post("/sources/{source_id}/crawl", status_code=202)
async def trigger_crawl(source_id: str):
    source = state.store.get_source(source_id)
    if source is None:
        raise HTTPException(404, detail="source_not_found")

    platform = state.platforms.get(source.platform)
    if platform is None:
        raise HTTPException(400, detail=f"platform_not_configured: {source.platform}")

    settings = get_settings()
    result = await orchestrate_crawl(
        source,
        platform,
        state.store,
        zscore_threshold=settings.trending_zscore_threshold,
        growth_threshold=settings.trending_growth_threshold,
        min_views=settings.trending_min_views,
    )
    return {"status": result.status, "videos_new": result.videos_new, "videos_updated": result.videos_updated}


# ---------------- Videos ----------------

@router.get("/videos", response_model=list[VideoResponse])
async def list_videos(source_id: str = Query(...), limit: int = Query(default=50, le=500)):
    rows = state.store.list_videos(source_id, limit=limit)
    return [_video_to_response(r) for r in rows]


@router.get("/videos/recent", response_model=list[TrendingItem])
async def list_recent_videos(
    account_id: str = Query(...),
    limit: int = Query(default=50, le=200),
    days: int | None = Query(default=None, ge=1, le=365),
):
    """Все недавние видео аккаунта (по всем source) с latest trending-score если есть.
    Используется Monitor-табом когда нужно показать всё, а не только is_trending=1.
    Сортировка по published_at DESC.

    days: опциональный фильтр по возрасту публикации (1-365). None → без ограничения.
    """
    rows = state.store.list_recent_videos_for_account(account_id, limit=limit, days=days)
    items: list[TrendingItem] = []
    for video, trending, source in rows:
        latest = state.store.latest_snapshot(video.id)
        items.append(_trending_item(video, trending, source, latest))
    return items


@router.get("/videos/{video_id}", response_model=VideoDetailResponse)
async def get_video(video_id: str):
    row = state.store.get_video(video_id)
    if row is None:
        raise HTTPException(404, detail="video_not_found")
    snapshots = state.store.list_snapshots(video_id, limit=100)
    latest = snapshots[0] if snapshots else None
    return VideoDetailResponse(
        **_video_to_response(row).model_dump(),
        snapshots=[
            MetricSnapshot(
                captured_at=s.captured_at,
                views=s.views,
                likes=s.likes,
                comments=s.comments,
                engagement_rate=s.engagement_rate,
            )
            for s in snapshots
        ],
        current_views=latest.views if latest else 0,
        current_likes=latest.likes if latest else 0,
        current_comments=latest.comments if latest else 0,
    )


@router.get("/videos/{video_id}/metrics", response_model=list[MetricSnapshot])
async def get_video_metrics(video_id: str, limit: int = Query(default=500, le=2000)):
    row = state.store.get_video(video_id)
    if row is None:
        raise HTTPException(404, detail="video_not_found")
    snaps = state.store.list_snapshots(video_id, limit=limit)
    return [
        MetricSnapshot(
            captured_at=s.captured_at,
            views=s.views,
            likes=s.likes,
            comments=s.comments,
            engagement_rate=s.engagement_rate,
        )
        for s in snaps
    ]


@router.post("/videos/{video_id}/analyze", response_model=AnalyzePayloadResponse)
async def analyze_video(video_id: str):
    """Stub для будущего оркестратора. Возвращает payload для processor."""
    row = state.store.get_video(video_id)
    if row is None:
        raise HTTPException(404, detail="video_not_found")
    return AnalyzePayloadResponse(
        video_id=row.id,
        file_path=None,  # нет downloader ещё
        source_url=row.url,
        title=row.title,
        hints={"platform": row.platform, "duration_sec": row.duration_sec},
    )


# ---------------- Trending ----------------

def _trending_item(video, trending, source, latest) -> TrendingItem:
    niche_slug = source.niche_slug if source else None
    velocity = trending.velocity if trending else None
    pct: float | None = None
    if niche_slug and velocity is not None and velocity > 0:
        pct = state.store.compute_niche_velocity_percentile(niche_slug, velocity)
    return TrendingItem(
        video_id=video.id,
        external_id=video.external_id,
        title=video.title,
        url=video.url,
        platform=video.platform,
        channel_name=source.channel_name if source else None,
        channel_external_id=source.external_id if source else "",
        niche_slug=niche_slug,
        thumbnail_url=video.thumbnail_url,
        published_at=video.published_at,
        hours_since_published=_hours_since(video.published_at),
        current_views=latest.views if latest else 0,
        current_likes=latest.likes if latest else 0,
        current_comments=latest.comments if latest else 0,
        zscore_24h=trending.zscore_24h if trending else None,
        growth_rate_24h=trending.growth_rate_24h if trending else None,
        is_trending=trending.is_trending if trending else False,
        velocity=velocity,
        is_rising=trending.is_rising if trending else False,
        niche_percentile=pct,
        computed_at=(trending.computed_at if trending else video.first_seen_at),
    )


@router.get("/trending", response_model=list[TrendingItem])
async def list_trending(
    account_id: str = Query(...),
    window: str = Query(default="24h"),
    limit: int = Query(default=20, le=100),
):
    rows = state.store.list_trending_for_account(account_id, limit=limit)
    items: list[TrendingItem] = []
    for video, trending, source in rows:
        latest = state.store.latest_snapshot(video.id)
        items.append(_trending_item(video, trending, source, latest))
    return items


@router.get("/trending/{video_id}", response_model=TrendingItem)
async def get_trending_detail(video_id: str):
    video = state.store.get_video(video_id)
    if video is None:
        raise HTTPException(404, detail="video_not_found")
    trending = state.store.latest_trending(video_id)
    if trending is None:
        raise HTTPException(404, detail="trending_not_computed")
    source = state.store.get_source(video.source_id)
    latest = state.store.latest_snapshot(video_id)
    return _trending_item(video, trending, source, latest)


# ---------------- Crawl log ----------------

@router.get("/crawl-log", response_model=list[CrawlLogEntry])
async def list_crawl_log(source_id: str | None = Query(default=None), limit: int = Query(default=50, le=500)):
    rows = state.store.list_crawl_log(source_id=source_id, limit=limit)
    return [
        CrawlLogEntry(
            id=r.id,
            source_id=r.source_id,
            started_at=r.started_at,
            finished_at=r.finished_at,
            status=r.status,
            videos_new=r.videos_new,
            videos_updated=r.videos_updated,
            error=r.error,
        )
        for r in rows
    ]


# ------------------------------------------------------------------ #
# Admin
# ------------------------------------------------------------------ #

@admin_router.get("/platforms", response_model=list[PlatformInfo])
async def admin_platforms():
    settings = get_settings()
    return [
        PlatformInfo(
            name="youtube",
            configured=bool(settings.youtube_api_key) or settings.monitor_fake_fetch,
            fake_mode=settings.fake_mode_for("youtube"),
        ),
        PlatformInfo(
            name="instagram",
            configured=bool(settings.apify_token) or settings.monitor_fake_fetch,
            fake_mode=settings.fake_mode_for("instagram"),
        ),
        PlatformInfo(
            name="tiktok",
            configured=bool(settings.apify_token) or settings.monitor_fake_fetch,
            fake_mode=settings.fake_mode_for("tiktok"),
        ),
    ]


@admin_router.post("/platforms/youtube/test")
async def admin_platforms_youtube_test():
    platform = state.platforms.get("youtube")
    if platform is None:
        raise HTTPException(503, detail="youtube_not_initialized")
    # Тестовый resolve — для fake возвращает fixture
    try:
        info = await platform.resolve_channel("https://www.youtube.com/@test")
        return {"ok": True, "resolved": info.external_id, "name": info.channel_name}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@admin_router.get("/platforms/youtube/quota", response_model=QuotaResponse)
async def admin_youtube_quota():
    from storage import _today_pt
    date = _today_pt()
    used = state.store.get_quota(date=date)
    return QuotaResponse(
        date=date,
        units_used=used,
        limit=YOUTUBE_DAILY_LIMIT,
        percent=round((used / YOUTUBE_DAILY_LIMIT) * 100, 2),
    )


@admin_router.get("/platforms/apify/usage", response_model=ApifyUsageResponse)
async def admin_apify_usage():
    from storage import _today_pt
    date = _today_pt()
    rows = state.store.get_apify_usage(date=date)
    return ApifyUsageResponse(
        date=date,
        entries=[ApifyUsageEntry(platform=p, runs=r, items=i) for (p, r, i) in rows],
    )


@admin_router.get("/scheduler", response_model=SchedulerStateResponse)
async def admin_scheduler_state():
    scheduler = state.scheduler
    if scheduler is None:
        return SchedulerStateResponse(running=False, jobs=[])
    jobs = [SchedulerJobInfo(**j) for j in scheduler.list_jobs()]
    return SchedulerStateResponse(running=scheduler.running, jobs=jobs)


@admin_router.post("/scheduler/reload")
async def admin_scheduler_reload():
    if state.scheduler is None:
        raise HTTPException(503, detail="scheduler_not_initialized")
    sources = state.store.list_sources(active_only=True)
    count = state.scheduler.reload_from_sources(sources)
    return {"reloaded": count}


# ---------------- Plan / tariff ----------------

def _plan_response(plan) -> PlanLimitsResponse:
    return PlanLimitsResponse(
        plan_name=plan.plan_name,
        max_sources_total=plan.max_sources_total,
        min_interval_min=plan.min_interval_min,
        max_results_limit=plan.max_results_limit,
        crawl_anchor_utc=plan.crawl_anchor_utc,
        updated_at=plan.updated_at,
        sources_used=state.store.count_sources_total(),
    )


@admin_router.get("/plan", response_model=PlanLimitsResponse)
async def admin_get_plan():
    plan = state.store.get_plan()
    return _plan_response(plan)


@admin_router.put("/plan", response_model=PlanLimitsResponse)
async def admin_update_plan(body: PlanLimitsUpdate):
    store = state.store
    old = store.get_plan()
    new = store.update_plan(
        plan_name=body.plan_name,
        max_sources_total=body.max_sources_total,
        min_interval_min=body.min_interval_min,
        max_results_limit=body.max_results_limit,
        crawl_anchor_utc=body.crawl_anchor_utc,
    )

    # 1. Если max_results_limit изменился — переинициализируем Apify-платформы
    #    (YouTube не зависит от этого лимита).
    if new.max_results_limit != old.max_results_limit:
        from platforms.instagram import InstagramSource
        from platforms.tiktok import TikTokSource

        settings = get_settings()

        def _apify_usage(platform: str, items: int) -> None:
            if state.store is not None:
                state.store.record_apify_run(platform, items)

        state.platforms["instagram"] = InstagramSource(
            apify_token=settings.apify_token,
            actor_id=settings.apify_instagram_actor,
            fake_mode=settings.fake_mode_for("instagram"),
            results_limit=new.max_results_limit,
            timeout_sec=settings.apify_timeout_sec,
            usage_counter=_apify_usage,
        )
        state.platforms["tiktok"] = TikTokSource(
            apify_token=settings.apify_token,
            actor_id=settings.apify_tiktok_actor,
            fake_mode=settings.fake_mode_for("tiktok"),
            results_limit=new.max_results_limit,
            timeout_sec=settings.apify_timeout_sec,
            usage_counter=_apify_usage,
        )

    # 2. Если anchor или min_interval_min изменился — перезаливаем scheduler.
    anchor_changed = new.crawl_anchor_utc != old.crawl_anchor_utc
    min_interval_changed = new.min_interval_min != old.min_interval_min
    if (anchor_changed or min_interval_changed) and state.scheduler is not None:
        if anchor_changed:
            state.scheduler.set_anchor(new.crawl_anchor_utc)
        # Для клэмпа существующих источников к новому полу:
        if min_interval_changed:
            for s in store.list_sources():
                if s.interval_min < new.min_interval_min:
                    store.update_source(s.id, interval_min=new.min_interval_min)
        sources = store.list_sources(active_only=True)
        state.scheduler.reload_from_sources(sources)

    return _plan_response(new)
