"""Unit-тесты для watchlist: селектор top-N, TTL-экспайр, graduation."""
from datetime import datetime, timedelta, timezone

import pytest

from analytics.watchlist import evaluate_watchlist, select_daily_topn
from storage import MonitorStore


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _make_source(store: MonitorStore, account: str = "acc1", handle: str = "blogger1"):
    return store.create_source(
        account_id=account,
        platform="instagram",
        channel_url=f"https://instagram.com/{handle}/",
        external_id=handle,
        channel_name=handle,
        niche_slug="money",
    )


def _seed_video(
    store: MonitorStore,
    source_id: str,
    ext_id: str,
    hours_ago: float,
    views: int,
    velocity: float | None,
) -> str:
    """Добавить видео с published_at=hours_ago назад + snapshot + trending."""
    published = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    v, _ = store.upsert_video(
        source_id=source_id,
        platform="instagram",
        external_id=ext_id,
        url=f"https://instagram.com/p/{ext_id}/",
        published_at=_iso(published),
        is_short=True,
    )
    store.insert_snapshot(
        video_id=v.id, views=views, likes=views // 20, comments=views // 100
    )
    store.upsert_trending(
        video_id=v.id,
        zscore_24h=1.0,
        growth_rate_24h=0.5,
        is_trending=velocity is not None and velocity >= 1000,
        velocity=velocity,
        is_rising=False,
    )
    return v.id


def test_select_daily_topn_picks_top_velocity(store: MonitorStore):
    src = _make_source(store)
    # 7 видео с разной velocity, возраст 6ч (≥ min_age_hours=2)
    ids = []
    for i in range(7):
        ids.append(_seed_video(store, src.id, f"v{i}", hours_ago=6, views=1000 + i * 500, velocity=500.0 + i * 200))

    result = select_daily_topn(store, top_n=5, ttl_days=3)
    assert result.added == 5
    assert result.candidates_seen == 7

    wl_rows = store.list_watchlist("acc1", status="active")
    assert len(wl_rows) == 5
    # Проверяем что это именно топ-5 по velocity (v6..v2)
    selected = {r[0].external_id for r in wl_rows}
    assert selected == {"v6", "v5", "v4", "v3", "v2"}


def test_select_daily_topn_rejects_too_fresh(store: MonitorStore):
    """Видео младше min_age_hours не должны попасть — анти-шум."""
    src = _make_source(store)
    _seed_video(store, src.id, "fresh", hours_ago=0.5, views=10000, velocity=20000.0)
    _seed_video(store, src.id, "mature", hours_ago=5, views=500, velocity=100.0)

    result = select_daily_topn(store, top_n=5, ttl_days=3, min_age_hours=2.0)
    assert result.added == 1
    wl = store.list_watchlist("acc1", status="active")
    assert len(wl) == 1
    assert wl[0][0].external_id == "mature"


def test_select_daily_topn_idempotent(store: MonitorStore):
    """Повторный вызов в тот же день не должен создавать дубли."""
    src = _make_source(store)
    _seed_video(store, src.id, "v1", hours_ago=6, views=1000, velocity=500.0)

    r1 = select_daily_topn(store, top_n=5, ttl_days=3)
    r2 = select_daily_topn(store, top_n=5, ttl_days=3)
    assert r1.added == 1
    assert r2.added == 0  # уже active

    wl = store.list_watchlist("acc1", status="active")
    assert len(wl) == 1


def test_graduate_on_delta_pct(store: MonitorStore):
    """Если views выросли ≥ initial × (1 + delta_pct), ставим hit."""
    src = _make_source(store)
    vid = _seed_video(store, src.id, "v1", hours_ago=6, views=1000, velocity=500.0)
    # initial_views = 1000
    select_daily_topn(store, top_n=5, ttl_days=3, velocity_hi=1e9, delta_pct=2.0)

    # Симулируем рост до 3500 (1000 * (1 + 2.0) = 3000)
    store.insert_snapshot(video_id=vid, views=3500, likes=100, comments=50)
    graduated, expired = evaluate_watchlist(
        store, velocity_hi=1e9, delta_pct=2.0,
    )
    assert graduated == 1
    assert expired == 0

    wl = store.list_watchlist("acc1", status=None)
    assert len(wl) == 1
    assert wl[0][1].status == "hit"
    assert wl[0][1].hit_reason == "delta_pct"
    assert wl[0][1].graduated_at is not None


def test_graduate_on_velocity_hi(store: MonitorStore):
    src = _make_source(store)
    vid = _seed_video(store, src.id, "v1", hours_ago=6, views=1000, velocity=500.0)
    select_daily_topn(store, top_n=5, ttl_days=3, velocity_hi=5000.0, delta_pct=1e9)

    # Поднять velocity до 6000
    store.insert_snapshot(video_id=vid, views=30000, likes=500, comments=50)
    store.upsert_trending(
        video_id=vid, zscore_24h=3.0, growth_rate_24h=2.0, is_trending=True,
        velocity=6000.0,
    )
    graduated, _ = evaluate_watchlist(store, velocity_hi=5000.0, delta_pct=1e9)
    assert graduated == 1
    wl = store.list_watchlist("acc1", status="hit")
    assert len(wl) == 1
    assert wl[0][1].hit_reason == "velocity_hi"


def test_expire_ttl_to_miss_and_stalled(store: MonitorStore):
    """TTL истёк: views <1.2×initial → miss; иначе → stalled."""
    src = _make_source(store)
    vid_miss = _seed_video(store, src.id, "miss1", hours_ago=6, views=1000, velocity=500.0)
    vid_stalled = _seed_video(store, src.id, "stall1", hours_ago=6, views=1000, velocity=500.0)

    # Вручную добавим записи с expires_at в прошлом
    now = datetime.now(timezone.utc)
    past = (now - timedelta(days=1)).isoformat()
    with store._conn() as c:
        c.execute(
            "INSERT INTO watchlist (video_id, source_id, added_at, expires_at, "
            "initial_views, initial_velocity, reason, status) VALUES (?,?,?,?,?,?,?, 'active')",
            (vid_miss, src.id, past, past, 1000, 500.0, "daily_topn"),
        )
        c.execute(
            "INSERT INTO watchlist (video_id, source_id, added_at, expires_at, "
            "initial_views, initial_velocity, reason, status) VALUES (?,?,?,?,?,?,?, 'active')",
            (vid_stalled, src.id, past, past, 1000, 500.0, "daily_topn"),
        )

    # miss: views всё ещё 1000 → ратио 1.0 < 1.2 → miss
    # stalled: поднимем до 1500 → ратио 1.5 ≥ 1.2, но < delta_pct=2.0 → stalled
    store.insert_snapshot(video_id=vid_stalled, views=1500, likes=20, comments=5)

    graduated, expired = evaluate_watchlist(store, velocity_hi=1e9, delta_pct=2.0)
    assert graduated == 0
    assert expired == 2

    statuses = {r[0].external_id: r[1].status for r in store.list_watchlist("acc1", status=None)}
    assert statuses["miss1"] == "miss"
    assert statuses["stall1"] == "stalled"


def test_watchlist_respects_per_source_topn(store: MonitorStore):
    """N считается per-source, а не глобально."""
    src_a = _make_source(store, handle="blogger_a")
    src_b = _make_source(store, handle="blogger_b")
    for i in range(5):
        _seed_video(store, src_a.id, f"a{i}", hours_ago=6, views=1000 + i, velocity=500.0 + i)
    for i in range(5):
        _seed_video(store, src_b.id, f"b{i}", hours_ago=6, views=1000 + i, velocity=500.0 + i)

    result = select_daily_topn(store, top_n=3, ttl_days=3)
    assert result.added == 6  # 3 per source × 2 sources

    wl = store.list_watchlist("acc1", status="active")
    by_src = {}
    for video, _w, source, _s, _t in wl:
        by_src.setdefault(source.external_id, []).append(video.external_id)
    assert len(by_src["blogger_a"]) == 3
    assert len(by_src["blogger_b"]) == 3


def test_mark_closed_manual(store: MonitorStore):
    src = _make_source(store)
    _seed_video(store, src.id, "v1", hours_ago=6, views=1000, velocity=500.0)
    select_daily_topn(store, top_n=5, ttl_days=3)

    wl_before = store.list_watchlist("acc1", status="active")
    wid = wl_before[0][1].id

    store.mark_watchlist_status(wid, status="closed", closed=True)
    wl_active_after = store.list_watchlist("acc1", status="active")
    assert len(wl_active_after) == 0

    row = store.get_watchlist(wid)
    assert row is not None
    assert row.status == "closed"
    assert row.closed_at is not None
