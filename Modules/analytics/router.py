"""FastAPI router для analytics-сервиса (cross-platform analytics).

Все роуты с префиксом /analytics. Агрегации — на pandas (aggregations.py).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status

import aggregations as agg
from auth import require_worker_token
from fetcher import build_adapters, run_hourly_cycle
from state import state


log = structlog.get_logger()

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _store():
    if state.store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "store_not_ready")
    return state.store


def _settings():
    if state.settings is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "settings_not_ready")
    return state.settings


# Healthz объявлен на уровне app (main.py) ДО include_router.


# ────────────────────────────────────────────────────────────────────
# Read / aggregations
# ────────────────────────────────────────────────────────────────────

@router.get("/cross-platform")
async def cross_platform(period: str | None = Query(default="30d")) -> dict[str, Any]:
    since = agg.period_to_since(period)
    rows = _store().latest_per_publication(since=since)
    return {"period": period, **agg.cross_platform(rows)}


@router.get("/platform/{platform}")
async def platform_view(
    platform: str, period: str | None = Query(default="30d"),
) -> dict[str, Any]:
    since = agg.period_to_since(period)
    rows = _store().latest_per_publication(platform=platform, since=since)
    return {"period": period, **agg.platform_summary(rows, platform)}


@router.get("/publication/{publication_id}")
async def publication_view(publication_id: str) -> dict[str, Any]:
    rows = _store().fetch_metrics(publication_id=publication_id)
    if not rows:
        raise HTTPException(404, "publication_not_found")
    return {"publication_id": publication_id, **agg.publication_timeseries(rows)}


@router.get("/ab")
async def ab_view(
    ids: str = Query(..., description="comma-separated publication ids"),
) -> dict[str, Any]:
    pub_ids = [i.strip() for i in ids.split(",") if i.strip()]
    if not pub_ids:
        raise HTTPException(400, "no_ids")
    store = _store()
    rows_by_id = {pid: store.fetch_metrics(publication_id=pid) for pid in pub_ids}
    return agg.ab_compare(rows_by_id)


@router.get("/top")
async def top_view(
    metric: str = Query(default="views"),
    period: str | None = Query(default="30d"),
    limit: int = Query(default=10, ge=1, le=100),
) -> dict[str, Any]:
    since = agg.period_to_since(period)
    rows = _store().latest_per_publication(since=since)
    return {
        "metric": metric,
        "period": period,
        "limit": limit,
        "items": agg.top_publications(rows, metric=metric, limit=limit),
    }


# ────────────────────────────────────────────────────────────────────
# Manual refresh (требует worker-token)
# ────────────────────────────────────────────────────────────────────

@router.post("/refresh-now", dependencies=[Depends(require_worker_token)])
async def refresh_now() -> dict[str, Any]:
    """Принудительный hourly-проход (publisher → fetch → DB) вне расписания."""
    store = _store()
    settings = _settings()
    vk_token = state.vk_token
    adapters = build_adapters(settings, vk_token)
    client = state.publisher_client
    summary = await run_hourly_cycle(
        store=store,
        adapters=adapters,
        client=client,
        use_mock=settings.analytics_use_mock,
        allow_metric_mock=not bool(vk_token),
        now=datetime.now(timezone.utc),
    )
    return {"refreshed": True, **summary}
