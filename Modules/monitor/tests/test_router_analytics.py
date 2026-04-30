"""Тесты аналитических эндпоинтов monitor/router.py:
- profile-snapshots, reel-stats (включая top_hashtags)
- posting-heatmap, er-trend (week|day)
- /hashtags (search/sort/limit), /hashtags/{tag}/videos drill-down
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
from state import state


USER_HEADERS = {"X-Token": "test-user-token"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_DIR", str(tmp_path))
    monkeypatch.setenv("MONITOR_FAKE_FETCH", "true")
    from config import get_settings
    get_settings.cache_clear()
    with TestClient(main.app) as c:
        yield c


def _utc_iso(days_ago: float = 0, *, hour: int = 12, minute: int = 0) -> str:
    """ISO timestamp относительно сейчас. days_ago=2 → позавчера."""
    t = datetime.now(timezone.utc) - timedelta(days=days_ago)
    t = t.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return t.isoformat()


def _create_source(client: TestClient, *, handle: str = "viral_user") -> str:
    """Создаёт source напрямую через store, минуя POST /sources с его background-crawl-ом
    (в FAKE-режиме API триггерит auto-crawl, который заваливает таблицу 3 левыми видео)."""
    src = state.store.create_source(
        account_id="acc1", platform="youtube",
        channel_url=f"https://youtube.com/@{handle}",
        external_id=handle,
    )
    return src.id


def _seed_video(store, source_id: str, *, ext_id: str, published_days_ago: float,
                description: str | None = None, duration_sec: int = 30,
                views: int = 1000, likes: int = 50, comments: int = 5,
                snapshot_hours_ago: float = 24):
    """Создаёт видео и снапшот метрик (для агрегации velocity/ER)."""
    v, _ = store.upsert_video(
        source_id=source_id, platform="youtube",
        external_id=ext_id,
        url=f"https://youtube.com/shorts/{ext_id}",
        title=f"Reel {ext_id}",
        description=description,
        duration_sec=duration_sec,
        published_at=_utc_iso(days_ago=published_days_ago),
    )
    captured = (datetime.now(timezone.utc) - timedelta(hours=snapshot_hours_ago)).isoformat()
    store.insert_snapshot(
        video_id=v.id, views=views, likes=likes, comments=comments,
        captured_at=captured,
    )
    return v.id


# ============================================================
# /sources/{id}/profile-snapshots
# ============================================================

def test_profile_snapshots_404_for_unknown_source(client):
    r = client.get("/monitor/sources/00000000-0000-0000-0000-000000000000/profile-snapshots",
                   headers=USER_HEADERS)
    assert r.status_code == 404
    assert r.json()["detail"] == "source_not_found"


def test_profile_snapshots_empty_when_no_data(client):
    src = _create_source(client)
    r = client.get(f"/monitor/sources/{src}/profile-snapshots?days=30", headers=USER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["source_id"] == src
    assert body["days"] == 30
    assert body["snapshots"] == []


def test_profile_snapshots_filters_by_days_window(client):
    src = _create_source(client)
    # Заталкиваем 5 снимков в разные даты через прямой SQL
    today = datetime.now(timezone.utc).date()
    with state.store._conn() as c:
        for i, (followers, posts) in enumerate([(1000, 50), (1100, 51), (1200, 52), (1500, 53), (1800, 54)]):
            d = (today - timedelta(days=i * 7)).isoformat()  # 0, -7, -14, -21, -28
            c.execute(
                "INSERT INTO profile_snapshots (source_id, captured_date, followers_count, posts_count) VALUES (?, ?, ?, ?)",
                (src, d, followers, posts),
            )

    # 30 дн window → все 5 включены, ASC по дате
    r = client.get(f"/monitor/sources/{src}/profile-snapshots?days=30", headers=USER_HEADERS)
    assert r.status_code == 200
    snaps = r.json()["snapshots"]
    assert len(snaps) == 5
    assert snaps[0]["followers"] == 1800  # самый ранний — 28 дней назад
    assert snaps[-1]["followers"] == 1000  # сегодня

    # 10 дн → только последний (today)
    r = client.get(f"/monitor/sources/{src}/profile-snapshots?days=10", headers=USER_HEADERS)
    snaps = r.json()["snapshots"]
    assert len(snaps) == 2  # сегодня и -7 дней
    assert snaps[-1]["followers"] == 1000


def test_profile_snapshots_validates_days_range(client):
    src = _create_source(client)
    r = client.get(f"/monitor/sources/{src}/profile-snapshots?days=0", headers=USER_HEADERS)
    assert r.status_code == 422
    r = client.get(f"/monitor/sources/{src}/profile-snapshots?days=400", headers=USER_HEADERS)
    assert r.status_code == 422


# ============================================================
# /sources/{id}/reel-stats
# ============================================================

def test_reel_stats_404_for_unknown_source(client):
    r = client.get("/monitor/sources/00000000-0000-0000-0000-000000000000/reel-stats",
                   headers=USER_HEADERS)
    assert r.status_code == 404


def test_reel_stats_empty_returns_zeros(client):
    src = _create_source(client)
    r = client.get(f"/monitor/sources/{src}/reel-stats?days=30", headers=USER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["posts"] == 0
    assert body["rows"] == []


def test_reel_stats_aggregates_from_videos(client):
    src = _create_source(client)
    # 3 видео за последние 30 дней с разными метриками
    _seed_video(state.store, src, ext_id="r1", published_days_ago=2,
                duration_sec=30, views=10_000, likes=500, comments=50)
    _seed_video(state.store, src, ext_id="r2", published_days_ago=10,
                duration_sec=45, views=20_000, likes=1000, comments=100)
    _seed_video(state.store, src, ext_id="r3", published_days_ago=15,
                duration_sec=60, views=5_000, likes=200, comments=20)

    r = client.get(f"/monitor/sources/{src}/reel-stats?days=30", headers=USER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["posts"] == 3
    assert body["avg_views"] == int((10_000 + 20_000 + 5_000) / 3)
    assert body["avg_duration_sec"] == int((30 + 45 + 60) / 3)
    assert body["avg_er"] is not None and body["avg_er"] > 0
    assert body["posts_per_week"] == 0.7  # 3 / (30/7) ≈ 0.70
    assert len(body["rows"]) == 3


def test_reel_stats_top_hashtags_extracts_from_descriptions(client):
    """Хэштеги парсятся из description через #(\\w+) regex."""
    src = _create_source(client)
    _seed_video(state.store, src, ext_id="r1", published_days_ago=2,
                description="Привет #продуктивность #саморазвитие", views=10_000)
    _seed_video(state.store, src, ext_id="r2", published_days_ago=5,
                description="Утренние ритуалы #продуктивность #утро", views=20_000)
    _seed_video(state.store, src, ext_id="r3", published_days_ago=8,
                description="Без тегов вообще", views=5_000)

    r = client.get(f"/monitor/sources/{src}/reel-stats?days=30", headers=USER_HEADERS)
    body = r.json()
    tags = body["top_hashtags"]
    assert len(tags) == 3
    # Сортировка по убыванию count
    assert tags[0]["tag"] == "продуктивность"
    assert tags[0]["count"] == 2
    # avg_views корректный (для продуктивность: видео r1 и r2)
    assert tags[0]["avg_views"] == int((10_000 + 20_000) / 2)
    # Дедуп per-row: тег в description дважды считается один раз
    saved = {t["tag"]: t["count"] for t in tags}
    assert saved["саморазвитие"] == 1
    assert saved["утро"] == 1


def test_reel_stats_top_hashtags_limit_50(client):
    """Должно вернуться максимум 50 тегов даже если в description больше."""
    src = _create_source(client)
    # Один пост с 60 уникальными тегами
    big_desc = " ".join(f"#tag{i}" for i in range(60))
    _seed_video(state.store, src, ext_id="bigtag", published_days_ago=3,
                description=big_desc, views=1000)
    r = client.get(f"/monitor/sources/{src}/reel-stats?days=30", headers=USER_HEADERS)
    body = r.json()
    assert len(body["top_hashtags"]) == 50


def test_reel_stats_excludes_videos_outside_window(client):
    src = _create_source(client)
    _seed_video(state.store, src, ext_id="recent", published_days_ago=5, views=1000)
    _seed_video(state.store, src, ext_id="old", published_days_ago=100, views=2000)

    r = client.get(f"/monitor/sources/{src}/reel-stats?days=30", headers=USER_HEADERS)
    body = r.json()
    assert body["posts"] == 1
    assert body["rows"][0]["video_id"]  # только recent попал


# ============================================================
# /sources/{id}/posting-heatmap
# ============================================================

def test_posting_heatmap_404_for_unknown_source(client):
    r = client.get("/monitor/sources/00000000-0000-0000-0000-000000000000/posting-heatmap",
                   headers=USER_HEADERS)
    assert r.status_code == 404


def test_posting_heatmap_returns_cells_for_published_videos(client):
    src = _create_source(client)
    # Несколько видео в разные часы
    for i in range(5):
        _seed_video(
            state.store, src,
            ext_id=f"hm{i}", published_days_ago=i,
            views=1000 * (i + 1),
        )
    r = client.get(f"/monitor/sources/{src}/posting-heatmap?days=30", headers=USER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["source_id"] == src
    assert "cells" in body
    # Каждая ячейка имеет dow, hour, posts, avg_velocity
    if body["cells"]:
        c = body["cells"][0]
        assert "dow" in c and "hour" in c and "posts" in c


# ============================================================
# /sources/{id}/er-trend
# ============================================================

def test_er_trend_default_granularity_is_week(client):
    src = _create_source(client)
    _seed_video(state.store, src, ext_id="t1", published_days_ago=10,
                views=10_000, likes=500, comments=50)
    r = client.get(f"/monitor/sources/{src}/er-trend?days=30", headers=USER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["granularity"] == "week"
    # Хотя бы один bucket за неделю с активностью
    assert len(body["buckets"]) >= 1


def test_er_trend_granularity_day_supported(client):
    src = _create_source(client)
    _seed_video(state.store, src, ext_id="d1", published_days_ago=3,
                views=10_000, likes=500, comments=50)
    r = client.get(f"/monitor/sources/{src}/er-trend?days=30&granularity=day",
                   headers=USER_HEADERS)
    assert r.status_code == 200
    assert r.json()["granularity"] == "day"


def test_er_trend_invalid_granularity_returns_422(client):
    src = _create_source(client)
    r = client.get(f"/monitor/sources/{src}/er-trend?days=30&granularity=hour",
                   headers=USER_HEADERS)
    assert r.status_code == 422


def test_er_trend_404_for_unknown_source(client):
    r = client.get("/monitor/sources/00000000-0000-0000-0000-000000000000/er-trend",
                   headers=USER_HEADERS)
    assert r.status_code == 404


def test_er_trend_validates_days_min(client):
    src = _create_source(client)
    # ge=7 на этом эндпоинте
    r = client.get(f"/monitor/sources/{src}/er-trend?days=3", headers=USER_HEADERS)
    assert r.status_code == 422


# ============================================================
# /hashtags  (cross-account aggregation)
# ============================================================

def test_hashtags_endpoint_requires_account_id(client):
    r = client.get("/monitor/hashtags", headers=USER_HEADERS)
    assert r.status_code == 422  # missing query param


def test_hashtags_endpoint_returns_aggregated_tags(client):
    src = _create_source(client)
    _seed_video(state.store, src, ext_id="h1", published_days_ago=2,
                description="Утро #morning #coffee #productivity", views=5_000)
    _seed_video(state.store, src, ext_id="h2", published_days_ago=5,
                description="Вечер #morning #productivity", views=8_000)

    r = client.get("/monitor/hashtags?account_id=acc1&days=30", headers=USER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["account_id"] == "acc1"
    items = body["items"]
    by_tag = {it["tag"]: it for it in items}
    # morning встречается в обоих видео → count=2
    assert by_tag["morning"]["posts_count"] == 2
    assert by_tag["productivity"]["posts_count"] == 2
    assert by_tag["coffee"]["posts_count"] == 1


def test_hashtags_endpoint_filters_by_q(client):
    src = _create_source(client)
    _seed_video(state.store, src, ext_id="h1", published_days_ago=1,
                description="#fitness #yoga #meditation", views=1000)

    r = client.get("/monitor/hashtags?account_id=acc1&days=30&q=med", headers=USER_HEADERS)
    body = r.json()
    tags = [it["tag"] for it in body["items"]]
    assert tags == ["meditation"]


def test_hashtags_endpoint_invalid_sort_returns_422(client):
    r = client.get("/monitor/hashtags?account_id=acc1&sort=bogus", headers=USER_HEADERS)
    assert r.status_code == 422


def test_hashtags_endpoint_limit_clamped(client):
    src = _create_source(client)
    big = " ".join(f"#bulk{i}" for i in range(120))
    _seed_video(state.store, src, ext_id="big", published_days_ago=1,
                description=big, views=100)

    r = client.get("/monitor/hashtags?account_id=acc1&days=30&limit=5", headers=USER_HEADERS)
    body = r.json()
    assert len(body["items"]) == 5

    # Лимит >200 → 422
    r = client.get("/monitor/hashtags?account_id=acc1&limit=999", headers=USER_HEADERS)
    assert r.status_code == 422


# ============================================================
# /hashtags/{tag}/videos
# ============================================================

def test_hashtag_videos_drill_down(client):
    src = _create_source(client)
    _seed_video(state.store, src, ext_id="r1", published_days_ago=2,
                description="#cooking #recipe", views=10_000)
    _seed_video(state.store, src, ext_id="r2", published_days_ago=4,
                description="#cooking #fast", views=5_000)
    _seed_video(state.store, src, ext_id="r3", published_days_ago=6,
                description="#nothing", views=1_000)

    r = client.get("/monitor/hashtags/cooking/videos?account_id=acc1&days=30",
                   headers=USER_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["tag"] == "cooking"
    urls = {item["url"] for item in body["items"]}
    assert "https://youtube.com/shorts/r1" in urls
    assert "https://youtube.com/shorts/r2" in urls
    assert "https://youtube.com/shorts/r3" not in urls


def test_hashtag_videos_handles_leading_hash(client):
    """Тег c # в URL должен трактоваться как без него (lower + lstrip)."""
    src = _create_source(client)
    _seed_video(state.store, src, ext_id="r1", published_days_ago=1,
                description="#productivity", views=1000)
    r = client.get("/monitor/hashtags/PRODUCTIVITY/videos?account_id=acc1",
                   headers=USER_HEADERS)
    assert r.status_code == 200
    assert r.json()["tag"] == "productivity"
    assert len(r.json()["items"]) == 1
