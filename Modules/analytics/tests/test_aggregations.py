"""Тесты pandas-агрегаций."""
import aggregations as agg


def _row(pid, platform, **kw):
    base = dict(
        publication_id=pid, platform=platform, fetched_at="2026-05-01T00:00:00+00:00",
        views=0, reach=0, likes=0, comments=0, shares=0, saves=0,
        click_through_to_external=0,
    )
    base.update(kw)
    return base


def test_cross_platform_totals_and_er():
    rows = [
        _row("p1", "vk", views=1000, likes=80, comments=10, shares=5, saves=5),
        _row("p2", "zen", views=500, likes=40, comments=5, shares=0, saves=5),
    ]
    out = agg.cross_platform(rows)
    assert out["totals"]["views"] == 1500
    assert out["publications"] == 2
    # engagement = (80+10+5+5)+(40+5+0+5) = 100 + 50 = 150
    assert out["engagement"] == 150
    assert out["engagement_rate"] == round(150 / 1500 * 100, 2)
    assert {p["platform"] for p in out["platforms"]} == {"vk", "zen"}
    # отсортировано по views desc
    assert out["platforms"][0]["platform"] == "vk"


def test_platform_summary():
    rows = [
        _row("p1", "vk", views=300, likes=30),
        _row("p2", "vk", views=700, likes=70),
        _row("p3", "zen", views=999),
    ]
    out = agg.platform_summary([r for r in rows if r["platform"] == "vk"], "vk")
    assert out["totals"]["views"] == 1000
    assert out["publications_count"] == 2
    assert out["publications"][0]["views"] == 700  # отсортировано desc


def test_top_publications_by_metric():
    rows = [
        _row("p1", "vk", views=100),
        _row("p2", "vk", views=900),
        _row("p3", "vk", views=500),
    ]
    top = agg.top_publications(rows, metric="views", limit=2)
    assert [t["publication_id"] for t in top] == ["p2", "p3"]
    assert top[0]["value"] == 900.0


def test_top_publications_by_engagement_rate():
    rows = [
        _row("p1", "vk", views=1000, likes=10),   # er 1%
        _row("p2", "vk", views=100, likes=50),    # er 50%
    ]
    top = agg.top_publications(rows, metric="engagement_rate", limit=1)
    assert top[0]["publication_id"] == "p2"


def test_ab_compare_picks_winner():
    rows_by_id = {
        "a": [_row("a", "vk", views=1000, likes=10)],            # er ~1%
        "b": [_row("b", "vk", views=1000, likes=200, saves=100)],  # er 30%
    }
    out = agg.ab_compare(rows_by_id)
    assert out["winner"] == "b"
    assert len(out["variants"]) == 2


def test_ab_compare_missing_variant():
    out = agg.ab_compare({"ghost": []})
    assert out["variants"][0]["found"] is False
    assert out["winner"] is None


def test_publication_timeseries_orders_by_time():
    rows = [
        _row("p1", "vk", views=10, fetched_at="2026-05-02T00:00:00+00:00"),
        _row("p1", "vk", views=5, fetched_at="2026-05-01T00:00:00+00:00"),
    ]
    out = agg.publication_timeseries(rows)
    assert [h["views"] for h in out["history"]] == [5, 10]
    assert out["latest"]["views"] == 10


def test_period_to_since():
    assert agg.period_to_since("all") is None
    assert agg.period_to_since(None) is None
    assert agg.period_to_since("7d") is not None
    assert agg.period_to_since("garbage") is None


def test_empty_rows_safe():
    assert agg.cross_platform([])["totals"]["views"] == 0
    assert agg.top_publications([], metric="views", limit=5) == []
    assert agg.platform_summary([], "vk")["publications_count"] == 0
