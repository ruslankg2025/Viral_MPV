"""Интеграция fetcher: publisher API → fetch метрик → запись в DB.

Покрывает три сценария:
- publisher отвечает по REST (mock HTTP) + VK-адаптер с mock HTTP → реальный путь;
- publisher недоступен → fallback на mock-фикстуры публикаций;
- daily snapshot агрегирует записанные метрики.
"""
import httpx
import pytest

from config import Settings
from fetcher import (
    build_adapters,
    run_daily_snapshot,
    run_hourly_cycle,
    select_active_publications,
)
from platforms.vk import VKAdapter
from publisher_client import PublisherClient
from storage import AnalyticsStore


@pytest.fixture
def store(tmp_path):
    return AnalyticsStore(tmp_path / "analytics.db")


@pytest.fixture
def settings():
    return Settings(analytics_use_mock=False, enable_fetcher=False)


def _vk_client_factory(metrics_views=12345):
    """httpx.AsyncClient, отвечающий и за publisher, и за VK по URL."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "publisher/publications" in url:
            assert request.headers.get("X-Worker-Token") == "tkn"
            return httpx.Response(200, json={"items": [
                {
                    "id": "pubA", "platform": "vk_video", "external_id": "-1_10",
                    "title": "A", "description": "", "tags": [],
                    "status": "published", "scheduled_at": None,
                    "published_at": "2026-05-25T00:00:00+00:00", "error_message": None,
                },
                {
                    "id": "pubB", "platform": "vk_clips", "external_id": "-1_20",
                    "title": "B", "description": "", "tags": [],
                    "status": "published", "scheduled_at": None,
                    "published_at": "2026-05-26T00:00:00+00:00", "error_message": None,
                },
                {   # без external_id — должен быть пропущен
                    "id": "pubC", "platform": "vk_video", "external_id": None,
                    "title": "C", "description": "", "tags": [],
                    "status": "scheduled", "scheduled_at": None,
                    "published_at": None, "error_message": None,
                },
            ]})
        if "video.get" in url:
            return httpx.Response(200, json={"response": {"count": 1, "items": [{
                "views": metrics_views, "likes": {"count": 10},
                "comments": {"count": 2}, "reposts": {"count": 1},
            }]}})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class _Patched(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    return _Patched


@pytest.mark.asyncio
async def test_hourly_cycle_publisher_and_vk(store, settings, monkeypatch):
    # Фиксируем "сейчас" близко к published_at публикаций (30д окно).
    from datetime import datetime, timezone
    now = datetime(2026, 5, 31, tzinfo=timezone.utc)

    monkeypatch.setattr(httpx, "AsyncClient", _vk_client_factory())
    client = PublisherClient(base_url="http://publisher:8000", token="tkn")
    adapters = build_adapters(settings, vk_token="vk-token")

    summary = await run_hourly_cycle(
        store=store, adapters=adapters, client=client,
        use_mock=False, allow_metric_mock=False, now=now,
    )
    assert summary["used_mock"] is False
    assert summary["publications"] == 3
    assert summary["active"] == 2  # pubC отброшен (no external_id)
    assert summary["written"] == 2

    rows = store.fetch_metrics()
    assert len(rows) == 2
    assert {r["platform"] for r in rows} == {"vk"}
    assert all(r["views"] == 12345 for r in rows)
    assert {r["publication_id"] for r in rows} == {"pubA", "pubB"}


@pytest.mark.asyncio
async def test_hourly_cycle_publisher_down_falls_back_to_mock(store, settings, monkeypatch):
    """Publisher недоступен → mock-фикстуры публикаций + mock VK-метрики."""
    def handler(request):
        raise httpx.ConnectError("refused")

    transport = httpx.MockTransport(handler)

    class _Patched(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _Patched)

    from datetime import datetime, timezone
    now = datetime(2026, 5, 25, tzinfo=timezone.utc)  # окно покрывает mock-публикации

    client = PublisherClient(base_url="http://publisher:8000", token="tkn")
    # vk_token=None → mock-метрики разрешены
    adapters = build_adapters(settings, vk_token=None)

    summary = await run_hourly_cycle(
        store=store, adapters=adapters, client=client,
        use_mock=False, allow_metric_mock=True, now=now,
    )
    assert summary["used_mock"] is True
    # mock-фикстуры: 2 published vk + 1 scheduled без external_id
    assert summary["active"] == 2
    assert summary["written"] == 2
    rows = store.fetch_metrics()
    assert len(rows) == 2
    assert all(r["views"] > 0 for r in rows)  # детерминированные mock-метрики


@pytest.mark.asyncio
async def test_use_mock_flag_skips_publisher(store, settings):
    """ANALYTICS_USE_MOCK=1 → publisher не дёргается вообще."""
    from datetime import datetime, timezone
    now = datetime(2026, 5, 25, tzinfo=timezone.utc)
    adapters = build_adapters(settings, vk_token=None)
    summary = await run_hourly_cycle(
        store=store, adapters=adapters, client=None,
        use_mock=True, allow_metric_mock=True, now=now,
    )
    assert summary["used_mock"] is True
    assert summary["written"] == 2


@pytest.mark.asyncio
async def test_daily_snapshot_aggregates(store):
    store.insert_metrics(publication_id="p1", platform="vk", views=100, reach=80, click_through_to_external=3)
    store.insert_metrics(publication_id="p2", platform="vk", views=200, reach=150, click_through_to_external=7)
    from datetime import date
    written = await run_daily_snapshot(store=store, snapshot_date=date(2026, 5, 30))
    assert len(written) == 1
    vk = written[0]
    assert vk["platform"] == "vk"
    assert vk["total_views"] == 300
    assert vk["total_reach"] == 230
    assert vk["click_through"] == 10
    assert vk["publications_count"] == 2
    snaps = store.list_snapshots(platform="vk")
    assert snaps[0]["total_views"] == 300


def test_select_active_publications_window():
    from datetime import datetime, timezone
    now = datetime(2026, 5, 31, tzinfo=timezone.utc)
    pubs = [
        {"id": "recent", "platform": "vk_video", "external_id": "-1_1",
         "status": "published", "published_at": "2026-05-20T00:00:00+00:00"},
        {"id": "old", "platform": "vk_video", "external_id": "-1_2",
         "status": "published", "published_at": "2026-01-01T00:00:00+00:00"},
        {"id": "noext", "platform": "vk_video", "external_id": None,
         "status": "published", "published_at": "2026-05-20T00:00:00+00:00"},
        {"id": "draft", "platform": "vk_video", "external_id": "-1_3",
         "status": "dry_run", "published_at": "2026-05-20T00:00:00+00:00"},
    ]
    active = select_active_publications(pubs, now=now)
    assert [p["id"] for p in active] == ["recent"]
