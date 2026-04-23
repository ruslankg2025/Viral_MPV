"""
Crawler — оркестрация обхода одного источника.

Поток:
1. Начать crawl_log (status='running').
2. platform.fetch_new_videos(channel, known) → добавить в БД.
3. platform.fetch_metrics(все видео < 30 дней) → insert_snapshot.
4. Для свежих видео (< 7 дней) → compute_trending → upsert_trending_scores.
5. Завершить crawl_log (status='ok' или 'failed').
6. Обновить source.last_crawled_at.

Concurrency: через asyncio.Semaphore, лимит из settings.crawl_max_concurrent.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from analytics.trending import compute_trending
from logging_setup import get_logger
from platforms.base import (
    ChannelInfo,
    ChannelNotFound,
    MetricsSource,
    PlatformError,
    QuotaExhausted,
)
from storage import MonitorStore, SourceRow

log = get_logger("monitor.crawler")

_semaphore: asyncio.Semaphore | None = None


def init_semaphore(max_concurrent: int) -> None:
    global _semaphore
    _semaphore = asyncio.Semaphore(max_concurrent)


@dataclass
class CrawlResult:
    status: str
    videos_new: int = 0
    videos_updated: int = 0
    error: str | None = None


async def orchestrate_crawl(
    source: SourceRow,
    platform: MetricsSource,
    store: MonitorStore,
    *,
    zscore_threshold: float = 2.0,
    growth_threshold: float = 0.5,
    min_views: int = 100,
) -> CrawlResult:
    """Один проход по источнику."""
    if _semaphore is not None:
        await _semaphore.acquire()
    try:
        return await _do_crawl(
            source,
            platform,
            store,
            zscore_threshold=zscore_threshold,
            growth_threshold=growth_threshold,
            min_views=min_views,
        )
    finally:
        if _semaphore is not None:
            _semaphore.release()


async def _do_crawl(
    source: SourceRow,
    platform: MetricsSource,
    store: MonitorStore,
    *,
    zscore_threshold: float,
    growth_threshold: float,
    min_views: int,
) -> CrawlResult:
    log_id = store.start_crawl(source.id)
    log.info("crawl_started", source_id=source.id, platform=source.platform)

    try:
        # 1. Воссоздаём ChannelInfo из источника. Для YouTube нужен uploads_playlist_id
        # (UC→UU). Для IG/TT — достаточно external_id (handle).
        if source.platform == "youtube":
            extra = {"uploads_playlist_id": source.external_id.replace("UC", "UU", 1)}
        else:
            extra = {}
        channel = ChannelInfo(
            external_id=source.external_id,
            channel_name=source.channel_name or "",
            extra=extra,
        )

        # 2. fetch_new_videos — пер-source override лимита постов (Apify cost control)
        known_videos = store.list_videos(source.id, limit=500)
        known_ids = {v.external_id for v in known_videos}
        new_videos_meta = await platform.fetch_new_videos(
            channel, known_ids, results_limit=source.max_results_limit,
        )

        videos_new = 0
        for vm in new_videos_meta:
            _, is_new = store.upsert_video(
                source_id=source.id,
                platform=source.platform,
                external_id=vm.external_id,
                url=vm.url,
                title=vm.title,
                description=vm.description,
                thumbnail_url=vm.thumbnail_url,
                duration_sec=vm.duration_sec,
                published_at=vm.published_at,
                is_short=vm.is_short,
            )
            if is_new:
                videos_new += 1

        # 3. fetch_metrics для всех видео последних 30 дней
        cutoff_30d = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        recent_videos = store.list_recent_videos(source.id, since=cutoff_30d)
        recent_external_ids = [v.external_id for v in recent_videos]

        metrics = await platform.fetch_metrics(recent_external_ids)
        external_to_video = {v.external_id: v for v in recent_videos}

        for m in metrics:
            v = external_to_video.get(m.external_id)
            if v is None:
                continue
            store.insert_snapshot(
                video_id=v.id,
                views=m.views,
                likes=m.likes,
                comments=m.comments,
            )
            # Платформа может вернуть duration позже, чем мы узнали о видео
            # (YouTube: /videos возвращает contentDetails, /playlistItems — нет).
            if m.duration_sec is not None and m.duration_sec != v.duration_sec:
                store.update_video_duration(v.id, m.duration_sec, m.is_short)

        videos_updated = len(metrics)

        # 4. Trending для свежих (< 48 часов) — IG/TT виралы пикуют за 24-72ч,
        #    7-дневное окно размывало сигнал. Лента за 48ч = «идея в моменте».
        now_utc = datetime.now(timezone.utc)
        cutoff_48h = (now_utc - timedelta(hours=48)).isoformat()
        fresh_videos = [v for v in recent_videos if (v.published_at or "") >= cutoff_48h]

        # Baseline канала: views всех recent_videos за 30 дней (исключаем каждое текущее)
        latest_views_by_video: dict[str, int] = {}
        for v in recent_videos:
            snap = store.latest_snapshot(v.id)
            if snap:
                latest_views_by_video[v.id] = snap.views

        for fv in fresh_videos:
            current_views = latest_views_by_video.get(fv.id, 0)
            # views_24h_ago: первый snapshot, которому ≥ 24ч. Независимо от
            # частоты crawl'ов (ранее брали snaps[1] = ~6ч назад — баг).
            snap_24h = store.get_snapshot_at_least_hours_ago(fv.id, 24)
            views_24h_ago: int | None = snap_24h.views if snap_24h else None

            # hours_since_published — для velocity
            hours_since: float | None = None
            if fv.published_at:
                try:
                    pub_dt = datetime.fromisoformat(fv.published_at.replace("Z", "+00:00"))
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                    hours_since = max(0.0, (now_utc - pub_dt).total_seconds() / 3600.0)
                except (ValueError, TypeError):
                    hours_since = None

            # Последние 3 snapshot в возрастающем порядке — для is_rising
            last3 = store.list_snapshots(fv.id, limit=3)
            recent_asc = [s.views for s in reversed(last3)]  # oldest → newest

            # baseline — остальные видео канала
            baseline = [
                latest_views_by_video[v.id]
                for v in recent_videos
                if v.id != fv.id and v.id in latest_views_by_video
            ]
            result = compute_trending(
                current_views=current_views,
                views_24h_ago=views_24h_ago,
                channel_baseline_views=baseline,
                hours_since_published=hours_since,
                recent_snapshots_ascending=recent_asc,
                zscore_threshold=zscore_threshold,
                growth_threshold=growth_threshold,
                min_views=min_views,
            )
            store.upsert_trending(
                video_id=fv.id,
                zscore_24h=result.zscore_24h,
                growth_rate_24h=result.growth_rate_24h,
                is_trending=result.is_trending,
                velocity=result.velocity,
                is_rising=result.is_rising,
            )

        # 5. Завершение
        store.finish_crawl(
            log_id, status="ok", videos_new=videos_new, videos_updated=videos_updated
        )
        store.update_source(
            source.id,
            last_crawled_at=datetime.now(timezone.utc).isoformat(),
            last_error="",  # clear error
        )
        log.info(
            "crawl_finished",
            source_id=source.id,
            videos_new=videos_new,
            videos_updated=videos_updated,
        )
        return CrawlResult(status="ok", videos_new=videos_new, videos_updated=videos_updated)

    except QuotaExhausted as e:
        store.finish_crawl(log_id, status="failed", error=f"quota_exhausted: {e}")
        store.update_source(source.id, last_error="quota_exhausted")
        log.warning("crawl_quota_exhausted", source_id=source.id)
        return CrawlResult(status="failed", error="quota_exhausted")

    except ChannelNotFound as e:
        store.finish_crawl(log_id, status="failed", error=f"channel_not_found: {e}")
        store.update_source(source.id, is_active=False, last_error=f"channel_not_found: {e}")
        log.warning("crawl_channel_not_found", source_id=source.id, error=str(e))
        return CrawlResult(status="failed", error="channel_not_found")

    except PlatformError as e:
        store.finish_crawl(log_id, status="failed", error=str(e))
        store.update_source(source.id, last_error=str(e)[:500])
        log.error("crawl_platform_error", source_id=source.id, error=str(e))
        return CrawlResult(status="failed", error=str(e))

    except Exception as e:
        store.finish_crawl(log_id, status="failed", error=f"unexpected: {type(e).__name__}: {e}")
        store.update_source(source.id, last_error=f"unexpected: {e}"[:500])
        log.exception("crawl_unexpected_error", source_id=source.id)
        return CrawlResult(status="failed", error=f"unexpected: {e}")
