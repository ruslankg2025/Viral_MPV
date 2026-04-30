from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from analytics.vitality import compute_vitality, should_crawl_now
from analytics.watchlist import select_daily_topn
from config import get_settings
from crawler import init_semaphore, orchestrate_crawl
from logging_setup import get_logger, setup_logging
from platforms.instagram import InstagramSource
from platforms.tiktok import TikTokSource
from platforms.youtube import YouTubeSource
from router import admin_router, public_router, router
from scheduler import SchedulerWrapper
from state import state
from storage import MonitorStore

setup_logging()
log = get_logger()


async def _crawl_callback(source_id: str) -> None:
    """Вызывается scheduler-ом для каждого тика."""
    store = state.store
    if store is None:
        return
    source = store.get_source(source_id)
    if source is None or not source.is_active:
        return
    platform = state.platforms.get(source.platform)
    if platform is None:
        return

    # Smart cooldown: для slow/silent/broken/empty авторов реально crawl-им
    # реже чем scheduler нас будит — экономим Apify-кредиты.
    vitality, _age = compute_vitality(source, store)
    if not should_crawl_now(source, vitality):
        log.info("crawl_skipped_cooldown", source_id=source_id, vitality=vitality)
        return

    settings = get_settings()
    result = await orchestrate_crawl(
        source,
        platform,
        store,
        zscore_threshold=settings.trending_zscore_threshold,
        growth_threshold=settings.trending_growth_threshold,
        min_views=settings.trending_min_views,
    )
    # После каждого автоматического crawl сразу обновляем watchlist для
    # этого источника — иначе свежие рилсы лежат в videos но не попадают
    # в «Мои авторы» до утреннего daily-watchlist cron (раз в сутки),
    # из-за чего новые посты «исчезают» с экрана на 8-20 часов.
    if settings.watchlist_enabled and result.status == "ok":
        try:
            select_daily_topn(
                store,
                top_n=settings.watchlist_top_n,
                ttl_days=settings.watchlist_ttl_days,
                freshness_hours=settings.watchlist_freshness_hours,
                min_age_hours=settings.watchlist_min_age_hours,
                velocity_hi=settings.watchlist_graduate_velocity,
                delta_pct=settings.watchlist_graduate_delta_pct,
                source_id=source_id,
            )
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()

    state.settings = settings
    state.store = MonitorStore(settings.db_dir / "monitor.db")

    # Cleanup stale crawls
    n = state.store.mark_stale_crawls_as_failed()
    if n:
        log.info("stale_crawls_marked", count=n)

    # Load active plan (tariff limits)
    plan = state.store.get_plan()
    log.info(
        "plan_loaded",
        name=plan.plan_name,
        max_sources=plan.max_sources_total,
        min_interval_min=plan.min_interval_min,
        max_results_limit=plan.max_results_limit,
        anchor_utc=plan.crawl_anchor_utc,
    )

    # Init semaphore for crawler
    init_semaphore(settings.crawl_max_concurrent)

    # Init YouTube platform
    state.platforms["youtube"] = YouTubeSource(
        api_key=settings.youtube_api_key,
        fake_mode=settings.fake_mode_for("youtube"),
        quota_counter=lambda units: state.store.increment_quota(units) if state.store else None,
    )

    # Init Apify-based platforms (Instagram, TikTok) с plan-based results_limit.
    # usage_counter пишет в apify_usage для наблюдаемости.
    def _apify_usage(platform: str, items: int, *, actor_kind: str = "reel") -> None:
        if state.store is not None:
            state.store.record_apify_run(platform, items, actor_kind=actor_kind)

    state.platforms["instagram"] = InstagramSource(
        apify_token=settings.apify_token,
        actor_id=settings.apify_instagram_actor,
        profile_actor_id=settings.apify_instagram_profile_actor,
        fake_mode=settings.fake_mode_for("instagram"),
        results_limit=plan.max_results_limit,
        timeout_sec=settings.apify_timeout_sec,
        usage_counter=_apify_usage,
    )
    state.platforms["tiktok"] = TikTokSource(
        apify_token=settings.apify_token,
        actor_id=settings.apify_tiktok_actor,
        fake_mode=settings.fake_mode_for("tiktok"),
        results_limit=plan.max_results_limit,
        timeout_sec=settings.apify_timeout_sec,
        usage_counter=_apify_usage,
    )

    # Init scheduler с anchor из plan + reload jobs
    state.scheduler = SchedulerWrapper(
        crawl_callback=_crawl_callback,
        crawl_anchor_utc=plan.crawl_anchor_utc,
    )
    state.scheduler.start()
    sources = state.store.list_sources(active_only=True)
    state.scheduler.reload_from_sources(sources)

    if settings.watchlist_enabled:
        async def _watchlist_callback() -> None:
            store = state.store
            if store is None:
                return
            s = get_settings()
            try:
                result = select_daily_topn(
                    store,
                    top_n=s.watchlist_top_n,
                    ttl_days=s.watchlist_ttl_days,
                    freshness_hours=s.watchlist_freshness_hours,
                    min_age_hours=s.watchlist_min_age_hours,
                    velocity_hi=s.watchlist_graduate_velocity,
                    delta_pct=s.watchlist_graduate_delta_pct,
                )
                log.info(
                    "watchlist_job_done",
                    added=result.added,
                    graduated=result.graduated,
                    expired=result.expired,
                    candidates=result.candidates_seen,
                )
            except Exception as exc:
                log.error("watchlist_job_error", error=str(exc))

        state.watchlist_callback = _watchlist_callback
        state.scheduler.add_watchlist_job(
            _watchlist_callback,
            run_at_utc=settings.watchlist_daily_run_utc,
        )

        async def _daily_snapshot_callback() -> None:
            try:
                n = state.store.snapshot_all_active_profiles()
                log.info("daily_snapshot_done", rows=n)
            except Exception as exc:
                log.error("daily_snapshot_error", error=str(exc))

        state.scheduler.add_daily_snapshot_job(
            _daily_snapshot_callback,
            run_at_utc="07:00",
        )

        # Bootstrap: при старте сразу делаем snapshot за сегодня — чтобы
        # после деплоя в Аналитике сразу была одна точка на графике, не
        # ждать первого крон-тика.
        try:
            n = state.store.snapshot_all_active_profiles()
            log.info("startup_snapshot", rows=n)
        except Exception as exc:
            log.warning("startup_snapshot_error", error=str(exc))

        # Bootstrap watchlist: разобрать backlog свежих видео сразу при
        # старте. До фикса _crawl_callback свежие посты лежали в videos
        # без записи в watchlist — этот вызов заберёт их.
        try:
            sel = select_daily_topn(
                state.store,
                top_n=settings.watchlist_top_n,
                ttl_days=settings.watchlist_ttl_days,
                freshness_hours=settings.watchlist_freshness_hours,
                min_age_hours=settings.watchlist_min_age_hours,
                velocity_hi=settings.watchlist_graduate_velocity,
                delta_pct=settings.watchlist_graduate_delta_pct,
            )
            log.info("startup_watchlist", added=sel.added, candidates=sel.candidates_seen)
        except Exception as exc:
            log.warning("startup_watchlist_error", error=str(exc))

    log.info(
        "monitor_startup",
        db_dir=str(settings.db_dir),
        fake_mode=settings.effective_fake_mode,
        active_sources=len(sources),
        watchlist_enabled=settings.watchlist_enabled,
    )

    try:
        yield
    finally:
        if state.scheduler:
            state.scheduler.stop()
        log.info("monitor_shutdown")


app = FastAPI(title="viral-monitor", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(public_router)
app.include_router(router)
app.include_router(admin_router)
