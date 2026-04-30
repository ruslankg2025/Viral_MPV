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


# ============================================================
# V13: analyze-pipeline state (orchestrator → monitor PATCH)
# ============================================================

def test_v13_video_analyze_fields_default_none(store: MonitorStore):
    s = store.create_source(account_id="a", platform="instagram", channel_url="u", external_id="e")
    v, _ = store.upsert_video(
        source_id=s.id, platform="instagram", external_id="ig1", url="https://x"
    )
    fresh = store.get_video(v.id)
    assert fresh is not None
    assert fresh.sha256 is None
    assert fresh.orchestrator_run_id is None
    assert fresh.script_id is None
    assert fresh.analysis_done_at is None


def test_v13_update_video_analysis_partial(store: MonitorStore):
    s = store.create_source(account_id="a", platform="instagram", channel_url="u", external_id="e")
    v, _ = store.upsert_video(
        source_id=s.id, platform="instagram", external_id="ig2", url="https://x"
    )

    # Только run_id (как в начале pipeline)
    ok = store.update_video_analysis(v.id, orchestrator_run_id="run-abc")
    assert ok is True
    after_run = store.get_video(v.id)
    assert after_run.orchestrator_run_id == "run-abc"
    assert after_run.sha256 is None  # не трогали

    # Затем sha256 и script_id (как после успеха pipeline)
    store.update_video_analysis(
        v.id, sha256="deadbeef", script_id="script-xyz",
        analysis_done_at="2026-04-29T12:00:00+00:00",
    )
    after_done = store.get_video(v.id)
    assert after_done.orchestrator_run_id == "run-abc"  # сохранилось
    assert after_done.sha256 == "deadbeef"
    assert after_done.script_id == "script-xyz"
    assert after_done.analysis_done_at == "2026-04-29T12:00:00+00:00"


def test_v13_update_video_analysis_unknown_returns_false(store: MonitorStore):
    assert store.update_video_analysis("nonexistent-id", script_id="x") is False


def test_v13_update_video_analysis_no_fields_is_noop(store: MonitorStore):
    s = store.create_source(account_id="a", platform="instagram", channel_url="u", external_id="e")
    v, _ = store.upsert_video(
        source_id=s.id, platform="instagram", external_id="ig3", url="https://x"
    )
    # Передаём всё None — никаких изменений, но возвращаем True (нет ошибки)
    assert store.update_video_analysis(v.id) is True


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


# ---------------- SCHEMA_V5: velocity + is_rising ----------------

def test_schema_v5_trending_has_velocity_and_rising(store: MonitorStore):
    with store._conn() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(trending_scores)").fetchall()}
    assert "velocity" in cols
    assert "is_rising" in cols


def test_upsert_trending_with_velocity_and_rising(store: MonitorStore):
    src = store.create_source(
        account_id="a", platform="instagram",
        channel_url="https://instagram.com/u", external_id="u",
    )
    video, _ = store.upsert_video(
        source_id=src.id, platform="instagram", external_id="v1",
        url="https://instagram.com/p/v1", title="x",
    )
    t = store.upsert_trending(
        video_id=video.id,
        zscore_24h=1.5, growth_rate_24h=0.3, is_trending=True,
        velocity=5000.0, is_rising=True,
    )
    assert t.velocity == 5000.0
    assert t.is_rising is True
    # reload & check
    t2 = store.latest_trending(video.id)
    assert t2 is not None
    assert t2.velocity == 5000.0
    assert t2.is_rising is True


# ---------------- get_snapshot_at_least_hours_ago ----------------

def test_get_snapshot_at_least_hours_ago(store: MonitorStore):
    from datetime import datetime, timedelta, timezone
    src = store.create_source(
        account_id="a", platform="instagram",
        channel_url="https://instagram.com/u", external_id="u",
    )
    video, _ = store.upsert_video(
        source_id=src.id, platform="instagram", external_id="v1", url="x",
    )
    now = datetime.now(timezone.utc)

    # Manually insert snapshots at specific times
    with store._conn() as c:
        for hours_ago, views in [(0, 1000), (6, 800), (12, 600), (25, 400), (48, 200)]:
            ts = (now - timedelta(hours=hours_ago)).isoformat()
            c.execute(
                "INSERT INTO metric_snapshots (video_id, captured_at, views, likes, comments)"
                " VALUES (?, ?, ?, 0, 0)",
                (video.id, ts, views),
            )

    # Запрос «≥ 24ч назад» → самый свежий из {25ч, 48ч} = 25ч (views=400)
    snap = store.get_snapshot_at_least_hours_ago(video.id, 24)
    assert snap is not None
    assert snap.views == 400

    # Запрос «≥ 1ч назад» → 6ч (views=800)
    snap = store.get_snapshot_at_least_hours_ago(video.id, 1)
    assert snap.views == 800

    # Запрос «≥ 72ч назад» → нет таких → None
    snap = store.get_snapshot_at_least_hours_ago(video.id, 72)
    assert snap is None


# ---------------- compute_niche_velocity_percentile ----------------

def test_niche_percentile_none_for_small_sample(store: MonitorStore):
    """< 10 видео в нише → None (shumу защита)."""
    src = store.create_source(
        account_id="a", platform="instagram",
        channel_url="https://instagram.com/u", external_id="u",
        niche_slug="money",
    )
    # 1 видео
    video, _ = store.upsert_video(
        source_id=src.id, platform="instagram", external_id="v1", url="x",
    )
    store.upsert_trending(
        video_id=video.id, zscore_24h=None, growth_rate_24h=None,
        is_trending=False, velocity=1000.0,
    )
    assert store.compute_niche_velocity_percentile("money", 1000.0) is None


def test_niche_percentile_distribution(store: MonitorStore):
    """Распределение velocity 100..1000, проверяем percentile для конкретных значений."""
    src = store.create_source(
        account_id="a", platform="instagram",
        channel_url="https://instagram.com/u", external_id="u",
        niche_slug="money",
    )
    # 10 видео с velocity 100, 200, ..., 1000
    for i in range(10):
        v, _ = store.upsert_video(
            source_id=src.id, platform="instagram",
            external_id=f"v{i}", url=f"x{i}",
        )
        store.upsert_trending(
            video_id=v.id, zscore_24h=None, growth_rate_24h=None,
            is_trending=False, velocity=(i + 1) * 100.0,
        )
    # velocity=500 должен быть на 50-м percentile (5 из 10 ≤ 500)
    pct = store.compute_niche_velocity_percentile("money", 500.0)
    assert pct == 0.5
    # velocity=1000 → 100% (все ≤)
    assert store.compute_niche_velocity_percentile("money", 1000.0) == 1.0
    # velocity=50 → 0% (никто ≤ 50)
    assert store.compute_niche_velocity_percentile("money", 50.0) == 0.0


# ---------------- list_recent_videos_for_account — days filter ----------------

def test_list_recent_with_days_filter(store: MonitorStore):
    from datetime import datetime, timedelta, timezone
    src = store.create_source(
        account_id="a", platform="instagram",
        channel_url="https://instagram.com/u", external_id="u",
    )
    now = datetime.now(timezone.utc)
    # 3 видео: 5 дней, 40 дней, 400 дней назад
    for ex, ago_days in [("v1", 5), ("v2", 40), ("v3", 400)]:
        ts = (now - timedelta(days=ago_days)).isoformat()
        store.upsert_video(
            source_id=src.id, platform="instagram",
            external_id=ex, url=ex, published_at=ts,
        )
    # days=7 → только v1
    rows = store.list_recent_videos_for_account("a", days=7)
    assert len(rows) == 1
    assert rows[0][0].external_id == "v1"
    # days=60 → v1 + v2
    rows = store.list_recent_videos_for_account("a", days=60)
    assert len(rows) == 2
    # days=500 → все 3
    rows = store.list_recent_videos_for_account("a", days=500)
    assert len(rows) == 3
    # без days → все 3
    rows = store.list_recent_videos_for_account("a")
    assert len(rows) == 3


# ---------------- SCHEMA_V12: video_hashtags ----------------

def test_schema_v12_table_present(store: MonitorStore):
    with store._conn() as c:
        v = c.execute("PRAGMA user_version").fetchone()[0]
        tbls = {r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert v >= 12
    assert "video_hashtags" in tbls


def test_upsert_video_syncs_hashtags(store: MonitorStore):
    """При указании description при upsert_video — теги парсятся в video_hashtags
    (lower-cased, де-дуплицированные, кириллица OK)."""
    s = store.create_source(account_id="a", platform="instagram", channel_url="u", external_id="e")
    v, _ = store.upsert_video(
        source_id=s.id, platform="instagram", external_id="v1", url="u",
        description="Чек #money #MONEY #инвестиции тут",
    )
    with store._conn() as c:
        tags = {r["tag"] for r in c.execute(
            "SELECT tag FROM video_hashtags WHERE video_id = ?", (v.id,)
        ).fetchall()}
    assert tags == {"money", "инвестиции"}


def test_upsert_video_resyncs_hashtags_on_update(store: MonitorStore):
    """Повторный upsert с новым description → старые теги удалены, новые вставлены."""
    s = store.create_source(account_id="a", platform="instagram", channel_url="u", external_id="e")
    store.upsert_video(
        source_id=s.id, platform="instagram", external_id="v1", url="u",
        description="#oldtag #foo",
    )
    store.upsert_video(
        source_id=s.id, platform="instagram", external_id="v1", url="u",
        description="#newtag",
    )
    with store._conn() as c:
        tags = {r["tag"] for r in c.execute(
            "SELECT tag FROM video_hashtags WHERE video_id = (SELECT id FROM videos WHERE external_id='v1')"
        ).fetchall()}
    assert tags == {"newtag"}


def test_upsert_video_without_description_keeps_hashtags(store: MonitorStore):
    """Если description=None — теги не сбрасываются (например, при обновлении только метрик)."""
    s = store.create_source(account_id="a", platform="instagram", channel_url="u", external_id="e")
    store.upsert_video(
        source_id=s.id, platform="instagram", external_id="v1", url="u",
        description="#keep",
    )
    store.upsert_video(  # update without description
        source_id=s.id, platform="instagram", external_id="v1", url="u",
        title="new-title",
    )
    with store._conn() as c:
        tags = {r["tag"] for r in c.execute(
            "SELECT tag FROM video_hashtags WHERE video_id = (SELECT id FROM videos WHERE external_id='v1')"
        ).fetchall()}
    assert tags == {"keep"}


# ---------------- posting_heatmap_for_source ----------------

def test_posting_heatmap_groups_by_dow_hour(store: MonitorStore):
    s = store.create_source(account_id="a", platform="instagram", channel_url="u", external_id="e")
    # Понедельник 2026-04-20 14:00 UTC и 18:00 UTC
    ts1 = "2026-04-20T14:30:00+00:00"
    ts2 = "2026-04-20T18:15:00+00:00"
    ts3 = "2026-04-21T18:00:00+00:00"  # вторник 18:00
    for i, ts in enumerate([ts1, ts2, ts3]):
        v, _ = store.upsert_video(
            source_id=s.id, platform="instagram",
            external_id=f"v{i}", url=f"u{i}", published_at=ts,
        )
        store.insert_snapshot(video_id=v.id, views=1000, likes=50, comments=5)
        store.upsert_trending(
            video_id=v.id, zscore_24h=0.0, growth_rate_24h=0.0,
            is_trending=False, velocity=100.0,
        )
    cells = store.posting_heatmap_for_source(s.id, days=365)
    by_slot = {(c["dow"], c["hour"]): c for c in cells}
    assert by_slot[(1, 14)]["posts"] == 1   # Пн 14:00
    assert by_slot[(1, 18)]["posts"] == 1   # Пн 18:00
    assert by_slot[(2, 18)]["posts"] == 1   # Вт 18:00


# ---------------- er_trend_for_source ----------------

def test_er_trend_groups_by_week(store: MonitorStore):
    s = store.create_source(account_id="a", platform="instagram", channel_url="u", external_id="e")
    v, _ = store.upsert_video(
        source_id=s.id, platform="instagram", external_id="v1", url="u",
        published_at="2026-04-01T00:00:00+00:00",
    )
    # Два snapshot в одной неделе — должен учитываться только последний
    store.insert_snapshot(video_id=v.id, views=1000, likes=10, comments=0,
                          captured_at="2026-04-13T10:00:00+00:00")
    store.insert_snapshot(video_id=v.id, views=2000, likes=200, comments=0,
                          captured_at="2026-04-13T20:00:00+00:00")
    # Snapshot в другой неделе
    store.insert_snapshot(video_id=v.id, views=2000, likes=100, comments=0,
                          captured_at="2026-04-20T10:00:00+00:00")
    buckets = store.er_trend_for_source(s.id, days=365, granularity="week")
    assert len(buckets) == 2
    # ER второго snapshot в неделе #15 = 200/2000 = 0.1
    assert buckets[0]["avg_er"] == pytest.approx(0.1, rel=1e-3)
    # ER неделя #16 = 100/2000 = 0.05
    assert buckets[1]["avg_er"] == pytest.approx(0.05, rel=1e-3)


# ---------------- hashtag_stats_for_account / videos_by_hashtag ----------------

def test_hashtag_stats_basic(store: MonitorStore):
    s1 = store.create_source(account_id="acc", platform="instagram",
                             channel_url="u1", external_id="e1", niche_slug="money")
    s2 = store.create_source(account_id="acc", platform="instagram",
                             channel_url="u2", external_id="e2", niche_slug="money")
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=2)).isoformat()
    older = (now - timedelta(days=10)).isoformat()
    # Два рилса с #foo от автора 1, один с #foo и #bar от автора 2
    for sid, ext, desc, ts in [
        (s1.id, "v1", "#foo cool", recent),
        (s1.id, "v2", "#foo again", older),
        (s2.id, "v3", "#foo #bar", recent),
    ]:
        v, _ = store.upsert_video(
            source_id=sid, platform="instagram",
            external_id=ext, url=ext, description=desc, published_at=ts,
        )
        store.insert_snapshot(video_id=v.id, views=1000, likes=50, comments=5)
    items = store.hashtag_stats_for_account("acc", days=30, sort="count")
    by_tag = {it["tag"]: it for it in items}
    assert by_tag["foo"]["posts_count"] == 3
    assert by_tag["foo"]["authors_using"] == 2
    assert by_tag["bar"]["posts_count"] == 1


def test_hashtag_stats_filter_by_q(store: MonitorStore):
    s = store.create_source(account_id="acc", platform="instagram",
                            channel_url="u", external_id="e", niche_slug="money")
    now = datetime.now(timezone.utc).isoformat()
    for ext, desc in [("v1", "#money tips"), ("v2", "#cooking"), ("v3", "#moneymindset")]:
        store.upsert_video(
            source_id=s.id, platform="instagram",
            external_id=ext, url=ext, description=desc, published_at=now,
        )
    items = store.hashtag_stats_for_account("acc", q="money", days=30)
    tags = {it["tag"] for it in items}
    assert tags == {"money", "moneymindset"}


def test_videos_by_hashtag_returns_top_views(store: MonitorStore):
    s = store.create_source(account_id="acc", platform="instagram",
                            channel_url="u", external_id="e")
    now = datetime.now(timezone.utc).isoformat()
    for ext, views in [("v1", 100), ("v2", 5000), ("v3", 1000)]:
        v, _ = store.upsert_video(
            source_id=s.id, platform="instagram",
            external_id=ext, url=ext, description="#foo", published_at=now,
        )
        store.insert_snapshot(video_id=v.id, views=views, likes=10, comments=1)
    rows = store.videos_by_hashtag("acc", "foo", days=30, limit=5)
    assert [r["video_id"] for r in rows][:2] == [
        # отсортировано по views DESC
        next(r["video_id"] for r in rows if r["current_views"] == 5000),
        next(r["video_id"] for r in rows if r["current_views"] == 1000),
    ]
