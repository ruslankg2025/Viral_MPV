from datetime import datetime, timedelta, timezone

import pytest

from storage import MonitorStore, MIGRATIONS


def test_migration_sets_user_version(store: MonitorStore):
    with store._conn() as c:
        v = c.execute("PRAGMA user_version").fetchone()[0]
    assert v == max(MIGRATIONS)


def test_create_and_get_source(store: MonitorStore):
    s = store.create_source(
        account_id="acc1",
        platform="youtube",
        channel_url="https://youtube.com/@mrbeast",
        external_id="UC123",
        channel_name="MrBeast",
        niche_slug="entertainment",
        tags=["viral", "top"],
        priority=200,
        interval_min=30,
    )
    assert s.id
    assert s.account_id == "acc1"
    assert s.external_id == "UC123"
    assert s.tags == ["viral", "top"]
    assert s.is_active is True
    assert s.profile_validated is False

    fetched = store.get_source(s.id)
    assert fetched is not None
    assert fetched.channel_name == "MrBeast"


def test_list_sources_filters_by_account(store: MonitorStore):
    store.create_source(account_id="a1", platform="youtube", channel_url="u1", external_id="e1")
    store.create_source(account_id="a1", platform="youtube", channel_url="u2", external_id="e2")
    store.create_source(account_id="a2", platform="youtube", channel_url="u3", external_id="e3")

    assert len(store.list_sources(account_id="a1")) == 2
    assert len(store.list_sources(account_id="a2")) == 1
    assert len(store.list_sources()) == 3


def test_unique_source_constraint(store: MonitorStore):
    store.create_source(account_id="a1", platform="youtube", channel_url="u1", external_id="e1")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        store.create_source(account_id="a1", platform="youtube", channel_url="u2", external_id="e1")


def test_update_source(store: MonitorStore):
    s = store.create_source(account_id="a", platform="youtube", channel_url="u", external_id="e")
    updated = store.update_source(
        s.id,
        priority=500,
        interval_min=15,
        tags=["new"],
        is_active=False,
        last_error="rate_limited",
    )
    assert updated is not None
    assert updated.priority == 500
    assert updated.interval_min == 15
    assert updated.tags == ["new"]
    assert updated.is_active is False
    assert updated.last_error == "rate_limited"


def test_delete_source_cascades(store: MonitorStore):
    s = store.create_source(account_id="a", platform="youtube", channel_url="u", external_id="e")
    v, _ = store.upsert_video(
        source_id=s.id, platform="youtube", external_id="vid1", url="https://y/vid1"
    )
    store.insert_snapshot(video_id=v.id, views=100, likes=10, comments=2)
    store.upsert_trending(
        video_id=v.id, zscore_24h=1.0, growth_rate_24h=0.5, is_trending=False
    )
    store.start_crawl(s.id)

    assert store.delete_source(s.id) is True
    assert store.get_source(s.id) is None
    assert store.get_video(v.id) is None
    assert store.list_snapshots(v.id) == []
    assert store.list_crawl_log(source_id=s.id) == []


def test_upsert_video_idempotent(store: MonitorStore):
    s = store.create_source(account_id="a", platform="youtube", channel_url="u", external_id="e")
    v1, new1 = store.upsert_video(
        source_id=s.id,
        platform="youtube",
        external_id="yt1",
        url="https://y/yt1",
        title="Original",
    )
    assert new1 is True

    v2, new2 = store.upsert_video(
        source_id=s.id,
        platform="youtube",
        external_id="yt1",
        url="https://y/yt1",
        title="Updated Title",
    )
    assert new2 is False
    assert v2.id == v1.id
    assert v2.title == "Updated Title"


def test_insert_snapshot_computes_engagement(store: MonitorStore):
    s = store.create_source(account_id="a", platform="youtube", channel_url="u", external_id="e")
    v, _ = store.upsert_video(source_id=s.id, platform="youtube", external_id="yt1", url="u")
    snap = store.insert_snapshot(video_id=v.id, views=1000, likes=50, comments=10)
    assert snap.engagement_rate == pytest.approx(0.06)

    # Zero views -> engagement None
    v2, _ = store.upsert_video(source_id=s.id, platform="youtube", external_id="yt2", url="u2")
    snap2 = store.insert_snapshot(video_id=v2.id, views=0, likes=0, comments=0)
    assert snap2.engagement_rate is None


def test_crawl_log_lifecycle(store: MonitorStore):
    s = store.create_source(account_id="a", platform="youtube", channel_url="u", external_id="e")
    log_id = store.start_crawl(s.id)
    logs = store.list_crawl_log(source_id=s.id)
    assert len(logs) == 1
    assert logs[0].status == "running"

    store.finish_crawl(log_id, status="ok", videos_new=3, videos_updated=5)
    logs = store.list_crawl_log(source_id=s.id)
    assert logs[0].status == "ok"
    assert logs[0].videos_new == 3
    assert logs[0].videos_updated == 5
    assert logs[0].finished_at is not None


def test_mark_stale_crawls_as_failed(store: MonitorStore):
    s = store.create_source(account_id="a", platform="youtube", channel_url="u", external_id="e")
    # Ручная вставка старого running crawl
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with store._conn() as c:
        c.execute(
            "INSERT INTO crawl_log (source_id, started_at, status) VALUES (?, ?, 'running')",
            (s.id, old),
        )
    # Свежий running — не трогать
    store.start_crawl(s.id)

    marked = store.mark_stale_crawls_as_failed(older_than_minutes=10)
    assert marked == 1
    logs = store.list_crawl_log(source_id=s.id)
    statuses = sorted(l.status for l in logs)
    assert statuses == ["failed", "running"]


def test_quota_counter(store: MonitorStore):
    assert store.get_quota(date="2026-04-15") == 0
    store.increment_quota(3, date="2026-04-15")
    store.increment_quota(5, date="2026-04-15")
    assert store.get_quota(date="2026-04-15") == 8

    store.increment_quota(1, date="2026-04-16")
    assert store.get_quota(date="2026-04-15") == 8
    assert store.get_quota(date="2026-04-16") == 1


def test_trending_upsert(store: MonitorStore):
    s = store.create_source(account_id="a", platform="youtube", channel_url="u", external_id="e")
    v, _ = store.upsert_video(source_id=s.id, platform="youtube", external_id="yt1", url="u")

    # Используем явные computed_at в прошлом — latest должен быть по ORDER BY DESC.
    t1 = store.upsert_trending(
        video_id=v.id, zscore_24h=1.5, growth_rate_24h=0.3, is_trending=False,
        computed_at="2020-01-01T00:00:00+00:00",
    )
    assert t1.is_trending is False

    t2 = store.upsert_trending(
        video_id=v.id, zscore_24h=3.2, growth_rate_24h=0.8, is_trending=True,
        computed_at="2099-01-01T00:00:00+00:00",
    )
    assert t2.is_trending is True

    latest = store.latest_trending(v.id)
    assert latest is not None
    assert latest.is_trending is True


def test_count_active_sources(store: MonitorStore):
    s1 = store.create_source(account_id="a", platform="youtube", channel_url="u1", external_id="e1")
    store.create_source(account_id="a", platform="youtube", channel_url="u2", external_id="e2")
    store.update_source(s1.id, is_active=False)
    assert store.count_active_sources() == 1


# ---------------- SCHEMA_V2: is_short + apify_usage ----------------

def test_schema_v2_is_present(store: MonitorStore):
    """После миграции v2 колонка is_short + таблица apify_usage."""
    with store._conn() as c:
        v = c.execute("PRAGMA user_version").fetchone()[0]
    assert v >= 2
    # is_short колонка есть
    with store._conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(videos)").fetchall()}
    assert "is_short" in cols
    # apify_usage таблица создана
    with store._conn() as c:
        tbls = {r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "apify_usage" in tbls


def test_upsert_video_stores_is_short(store: MonitorStore):
    s = store.create_source(
        account_id="a", platform="youtube", channel_url="u", external_id="e"
    )
    v, _ = store.upsert_video(
        source_id=s.id,
        platform="youtube",
        external_id="short1",
        url="u",
        duration_sec=30,
        is_short=True,
    )
    assert v.is_short is True

    v2, _ = store.upsert_video(
        source_id=s.id,
        platform="youtube",
        external_id="long1",
        url="u2",
        duration_sec=600,
        is_short=False,
    )
    assert v2.is_short is False


def test_update_video_duration_autodetects_short(store: MonitorStore):
    s = store.create_source(
        account_id="a", platform="youtube", channel_url="u", external_id="e"
    )
    v, _ = store.upsert_video(
        source_id=s.id, platform="youtube", external_id="v1", url="u"
    )
    store.update_video_duration(v.id, 45)
    v = store.get_video(v.id)
    assert v.duration_sec == 45
    assert v.is_short is True

    store.update_video_duration(v.id, 300)
    v = store.get_video(v.id)
    assert v.duration_sec == 300
    assert v.is_short is False


def test_apify_usage_recording(store: MonitorStore):
    assert store.get_apify_usage(date="2026-04-15") == []
    store.record_apify_run("instagram", items=25, date="2026-04-15")
    store.record_apify_run("instagram", items=10, date="2026-04-15")
    store.record_apify_run("tiktok", items=5, date="2026-04-15")
    usage = dict(
        (p, (r, i)) for (p, r, i) in store.get_apify_usage(date="2026-04-15")
    )
    assert usage["instagram"] == (2, 35)
    assert usage["tiktok"] == (1, 5)


# ---------------- SCHEMA_V3: plan_limits ----------------

def test_schema_v3_is_present(store: MonitorStore):
    """После миграции версия БД минимум v3 (plan_limits появилась)."""
    with store._conn() as c:
        v = c.execute("PRAGMA user_version").fetchone()[0]
    assert v >= 3


def test_schema_v4_per_source_max_results_limit(store: MonitorStore):
    """SCHEMA_V4: per-source override лимита Apify-results."""
    with store._conn() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(sources)").fetchall()]
    assert "max_results_limit" in cols

    row = store.create_source(
        account_id="acc1", platform="instagram",
        channel_url="https://instagram.com/u", external_id="u",
    )
    assert row.max_results_limit is None

    row2 = store.create_source(
        account_id="acc1", platform="instagram",
        channel_url="https://instagram.com/u2", external_id="u2",
        max_results_limit=30,
    )
    assert row2.max_results_limit == 30

    updated = store.update_source(row.id, max_results_limit=15)
    assert updated is not None
    assert updated.max_results_limit == 15


def test_plan_limits_seeded_with_self_defaults(store: MonitorStore):
    """Миграция v3 должна вставить singleton с self-план дефолтами."""
    plan = store.get_plan()
    assert plan.plan_name == "self"
    assert plan.max_sources_total == 50
    assert plan.min_interval_min == 360
    assert plan.max_results_limit == 5
    assert plan.crawl_anchor_utc == "00:00"


def test_update_plan_partial(store: MonitorStore):
    store.update_plan(max_sources_total=100)
    plan = store.get_plan()
    assert plan.max_sources_total == 100
    # Остальные поля не изменились
    assert plan.min_interval_min == 360
    assert plan.max_results_limit == 5


def test_update_plan_all_fields(store: MonitorStore):
    store.update_plan(
        plan_name="starter",
        max_sources_total=200,
        min_interval_min=60,
        max_results_limit=30,
        crawl_anchor_utc="12:00",
    )
    plan = store.get_plan()
    assert plan.plan_name == "starter"
    assert plan.max_sources_total == 200
    assert plan.min_interval_min == 60
    assert plan.max_results_limit == 30
    assert plan.crawl_anchor_utc == "12:00"


def test_count_sources_total(store: MonitorStore):
    assert store.count_sources_total() == 0
    store.create_source(account_id="a", platform="youtube", channel_url="u1", external_id="e1")
    store.create_source(account_id="b", platform="tiktok", channel_url="u2", external_id="e2")
    assert store.count_sources_total() == 2
