"""Тесты AnalyticsStore (sync sqlite3 + WAL)."""
import pytest

from storage import AnalyticsStore


@pytest.fixture
def store(tmp_path):
    return AnalyticsStore(tmp_path / "analytics.db")


def test_insert_and_fetch_metrics(store):
    rid = store.insert_metrics(
        publication_id="p1", platform="vk", views=100, likes=10,
    )
    assert rid > 0
    rows = store.fetch_metrics(publication_id="p1")
    assert len(rows) == 1
    assert rows[0]["views"] == 100
    assert rows[0]["likes"] == 10
    assert rows[0]["shares"] == 0  # default


def test_fetch_metrics_filters(store):
    store.insert_metrics(publication_id="p1", platform="vk", views=1)
    store.insert_metrics(publication_id="p2", platform="zen", views=2)
    assert len(store.fetch_metrics(platform="vk")) == 1
    assert len(store.fetch_metrics(platform="zen")) == 1
    assert len(store.fetch_metrics()) == 2


def test_latest_per_publication(store):
    # Два среза одной публикации — latest должен взять самый свежий.
    store.insert_metrics(publication_id="p1", platform="vk", views=100, fetched_at="2026-05-01T00:00:00+00:00")
    store.insert_metrics(publication_id="p1", platform="vk", views=500, fetched_at="2026-05-02T00:00:00+00:00")
    store.insert_metrics(publication_id="p2", platform="vk", views=42, fetched_at="2026-05-01T00:00:00+00:00")
    latest = store.latest_per_publication()
    by_id = {r["publication_id"]: r for r in latest}
    assert len(latest) == 2
    assert by_id["p1"]["views"] == 500
    assert by_id["p2"]["views"] == 42


def test_latest_per_publication_since(store):
    store.insert_metrics(publication_id="old", platform="vk", views=1, fetched_at="2026-01-01T00:00:00+00:00")
    store.insert_metrics(publication_id="new", platform="vk", views=2, fetched_at="2026-05-01T00:00:00+00:00")
    latest = store.latest_per_publication(since="2026-04-01T00:00:00+00:00")
    assert [r["publication_id"] for r in latest] == ["new"]


def test_snapshots(store):
    store.insert_snapshot(
        date="2026-05-30", platform="vk", total_views=1000, total_reach=800,
        new_followers=5, click_through=20, publications_count=3,
    )
    snaps = store.list_snapshots(platform="vk")
    assert len(snaps) == 1
    assert snaps[0]["total_views"] == 1000
    assert snaps[0]["publications_count"] == 3


def test_stats(store):
    store.insert_metrics(publication_id="p1", platform="vk", views=1)
    store.insert_metrics(publication_id="p1", platform="vk", views=2)
    store.insert_metrics(publication_id="p2", platform="vk", views=3)
    s = store.stats()
    assert s["metric_rows"] == 3
    assert s["publications"] == 2
    assert s["snapshots"] == 0


def test_migrations_idempotent(tmp_path):
    # Повторное открытие той же БД не должно падать (миграции идемпотентны).
    path = tmp_path / "a.db"
    s1 = AnalyticsStore(path)
    s1.insert_metrics(publication_id="p1", platform="vk", views=1)
    s2 = AnalyticsStore(path)  # re-open
    assert len(s2.fetch_metrics()) == 1


def test_wal_mode(store):
    conn = store._conn()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    conn.close()
