"""Fetcher worker — фоновый сбор метрик из publisher + платформ.

Логика:
- Раз в час: берём активные публикации за 30д из publisher НАПРЯМУЮ
  (GET http://publisher:8000/publisher/publications?limit=1000, X-Worker-Token).
  Если publisher недоступен — лог + mock-фикстуры. Для каждой опубликованной
  публикации с external_id тянем метрики VK-адаптером (или mock-метрики, если
  VK_TOKEN нет) и пишем строку в platform_metrics.
- Раз в день в 00:00 МСК: daily snapshot — агрегируем последние метрики по
  платформам в daily_snapshots.

Ядро (`run_hourly_cycle`, `run_daily_snapshot`) — обычные async-функции без
зависимости от FastAPI, чтобы их можно было дёргать из тестов на фикстурах.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

from config import Settings
from platforms.base import PlatformAdapter
from platforms.vk import VKAdapter, VKError
from publisher_client import (
    VK_PUBLISHER_PLATFORMS,
    PublisherClient,
    mock_publications,
    mock_vk_metrics,
)
from storage import METRIC_COLUMNS, AnalyticsStore


log = structlog.get_logger()

# МСК = UTC+3 (без DST).
MSK = timezone(timedelta(hours=3))

# Статусы публикаций, по которым стоит собирать метрики.
ACTIVE_STATUSES = {"published", "publishing"}

ACTIVE_WINDOW_DAYS = 30


def _resolve_adapter(platform: str, adapters: dict[str, PlatformAdapter]) -> PlatformAdapter | None:
    """publisher-платформа → analytics-адаптер."""
    if platform in VK_PUBLISHER_PLATFORMS:
        return adapters.get("vk")
    return adapters.get(platform)


def _is_recent(published_at: str | None, *, now: datetime) -> bool:
    """Опубликовано в окне последних ACTIVE_WINDOW_DAYS дней."""
    if not published_at:
        return False
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= now - timedelta(days=ACTIVE_WINDOW_DAYS)


def select_active_publications(
    pubs: list[dict[str, Any]], *, now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Фильтр: опубликованные, с external_id, за последние 30д."""
    now = now or datetime.now(timezone.utc)
    out = []
    for p in pubs:
        if p.get("status") not in ACTIVE_STATUSES:
            continue
        if not p.get("external_id"):
            continue
        if not _is_recent(p.get("published_at"), now=now):
            continue
        out.append(p)
    return out


async def load_publications(
    client: PublisherClient | None, *, use_mock: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """Возвращает (publications, used_mock).

    use_mock=True или недоступный publisher → mock-фикстуры.
    """
    if use_mock or client is None:
        log.info("analytics_publisher_mock", reason="configured" if use_mock else "no_client")
        return mock_publications(), True
    try:
        pubs = await client.list_publications(limit=1000)
        log.info("analytics_publisher_ok", count=len(pubs))
        return pubs, False
    except (httpx.HTTPError, RuntimeError, ValueError) as e:
        log.warning("analytics_publisher_unavailable_fallback_mock", error=str(e))
        return mock_publications(), True


async def fetch_one(
    pub: dict[str, Any], adapters: dict[str, PlatformAdapter], *, allow_mock: bool,
) -> dict[str, Any] | None:
    """Тянет метрики одной публикации. Возвращает kwargs для insert_metrics
    (с platform/publication_id) или None, если адаптера/данных нет."""
    platform = pub.get("platform") or ""
    external_id = pub.get("external_id")
    adapter = _resolve_adapter(platform, adapters)
    if adapter is None or not external_id:
        return None

    analytics_platform = "vk" if platform in VK_PUBLISHER_PLATFORMS else platform

    try:
        metrics = await adapter.fetch_metrics(external_id)
        kwargs = metrics.as_metric_kwargs()
    except (VKError, NotImplementedError) as e:
        if not allow_mock:
            log.warning("analytics_fetch_failed", platform=platform, external_id=external_id, error=str(e))
            return None
        # mock-метрики только для VK (Phase 1).
        if analytics_platform != "vk":
            log.warning("analytics_no_mock_for_platform", platform=platform)
            return None
        log.info("analytics_fetch_mock", external_id=external_id, reason=str(e))
        kwargs = mock_vk_metrics(external_id)

    return {
        "publication_id": pub["id"],
        "platform": analytics_platform,
        **{k: int(kwargs.get(k, 0)) for k in METRIC_COLUMNS},
    }


async def run_hourly_cycle(
    *,
    store: AnalyticsStore,
    adapters: dict[str, PlatformAdapter],
    client: PublisherClient | None,
    use_mock: bool,
    allow_metric_mock: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Один проход: publisher → fetch метрик → запись в БД. Возвращает сводку."""
    now = now or datetime.now(timezone.utc)
    pubs, used_mock = await load_publications(client, use_mock=use_mock)
    active = select_active_publications(pubs, now=now)
    written = 0
    for pub in active:
        row = await fetch_one(pub, adapters, allow_mock=allow_metric_mock or used_mock)
        if row is None:
            continue
        store.insert_metrics(**row)
        written += 1
    log.info(
        "analytics_hourly_cycle",
        publications=len(pubs), active=len(active), written=written, used_mock=used_mock,
    )
    return {
        "publications": len(pubs),
        "active": len(active),
        "written": written,
        "used_mock": used_mock,
    }


async def run_daily_snapshot(
    *, store: AnalyticsStore, snapshot_date: date | None = None,
) -> list[dict[str, Any]]:
    """Агрегирует последние метрики по платформам в daily_snapshots."""
    snapshot_date = snapshot_date or datetime.now(MSK).date()
    rows = store.latest_per_publication()
    by_platform: dict[str, dict[str, int]] = {}
    for r in rows:
        agg = by_platform.setdefault(
            r["platform"],
            {"total_views": 0, "total_reach": 0, "click_through": 0, "publications_count": 0},
        )
        agg["total_views"] += int(r.get("views") or 0)
        agg["total_reach"] += int(r.get("reach") or 0)
        agg["click_through"] += int(r.get("click_through_to_external") or 0)
        agg["publications_count"] += 1

    written = []
    for platform, agg in by_platform.items():
        store.insert_snapshot(
            date=snapshot_date.isoformat(),
            platform=platform,
            total_views=agg["total_views"],
            total_reach=agg["total_reach"],
            new_followers=0,  # publisher пока не отдаёт прирост подписчиков
            click_through=agg["click_through"],
            publications_count=agg["publications_count"],
        )
        written.append({"platform": platform, **agg})
    log.info("analytics_daily_snapshot", date=snapshot_date.isoformat(), platforms=len(written))
    return written


def build_adapters(settings: Settings, vk_token: str | None) -> dict[str, PlatformAdapter]:
    """Собирает словарь адаптеров. Phase 1 — только VK реально работает."""
    return {"vk": VKAdapter(token=vk_token)}


async def fetcher_loop(
    *,
    store: AnalyticsStore,
    adapters: dict[str, PlatformAdapter],
    client: PublisherClient | None,
    settings: Settings,
    vk_token: str | None,
) -> None:
    """Бесконечный фоновый цикл (запускается как asyncio task в lifespan).

    Каждый fetch_interval_seconds — hourly cycle. Раз в сутки в 00:00 МСК —
    daily snapshot (проверяем каждые snapshot_check_seconds). Mock-метрики
    разрешены, если нет VK_TOKEN.
    """
    allow_metric_mock = not bool(vk_token)
    last_snapshot_day: date | None = None
    last_fetch = 0.0
    log.info("analytics_fetcher_started", interval=settings.fetch_interval_seconds, mock=settings.analytics_use_mock)

    # Первый проход сразу при старте.
    try:
        await run_hourly_cycle(
            store=store, adapters=adapters, client=client,
            use_mock=settings.analytics_use_mock, allow_metric_mock=allow_metric_mock,
        )
    except Exception as e:  # noqa: BLE001
        log.error("analytics_hourly_cycle_error", error=str(e))
    loop = asyncio.get_event_loop()
    last_fetch = loop.time()

    while True:
        try:
            await asyncio.sleep(settings.snapshot_check_seconds)
            now_msk = datetime.now(MSK)
            # Daily snapshot в 00:00 МСК (раз в сутки).
            if now_msk.hour == 0 and last_snapshot_day != now_msk.date():
                try:
                    await run_daily_snapshot(store=store, snapshot_date=now_msk.date())
                    last_snapshot_day = now_msk.date()
                except Exception as e:  # noqa: BLE001
                    log.error("analytics_daily_snapshot_error", error=str(e))
            # Hourly cycle по интервалу.
            if loop.time() - last_fetch >= settings.fetch_interval_seconds:
                try:
                    await run_hourly_cycle(
                        store=store, adapters=adapters, client=client,
                        use_mock=settings.analytics_use_mock,
                        allow_metric_mock=allow_metric_mock,
                    )
                except Exception as e:  # noqa: BLE001
                    log.error("analytics_hourly_cycle_error", error=str(e))
                last_fetch = loop.time()
        except asyncio.CancelledError:
            log.info("analytics_fetcher_stopped")
            raise
