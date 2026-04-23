from analytics.trending import compute_trending, compute_velocity, compute_is_rising


def test_insufficient_snapshots_returns_null():
    r = compute_trending(
        current_views=1000,
        views_24h_ago=None,
        channel_baseline_views=[100, 200, 300, 400],
    )
    assert r.zscore_24h is not None  # baseline есть → zscore считается
    assert r.growth_rate_24h is None
    assert r.is_trending is False  # growth=None → не trending


def test_insufficient_baseline_returns_null_zscore():
    r = compute_trending(
        current_views=1000,
        views_24h_ago=500,
        channel_baseline_views=[100, 200],  # < 3 видео
    )
    assert r.zscore_24h is None
    assert r.growth_rate_24h == 1.0
    assert r.is_trending is False


def test_clear_trending_case():
    r = compute_trending(
        current_views=50000,
        views_24h_ago=15000,
        channel_baseline_views=[500, 800, 1000, 1200, 700, 900],
        zscore_threshold=2.0,
        growth_threshold=0.5,
        min_views=100,
    )
    assert r.zscore_24h is not None
    assert r.zscore_24h > 2.0
    assert r.growth_rate_24h is not None
    assert r.growth_rate_24h > 0.5
    assert r.is_trending is True


def test_growth_below_threshold_not_trending():
    r = compute_trending(
        current_views=10000,
        views_24h_ago=9500,  # growth = 0.053
        channel_baseline_views=[100, 200, 300, 400, 500],
        zscore_threshold=2.0,
        growth_threshold=0.5,
    )
    assert r.growth_rate_24h is not None
    assert r.growth_rate_24h < 0.5
    assert r.is_trending is False


def test_views_below_min_filters_out():
    r = compute_trending(
        current_views=50,  # < min_views=100
        views_24h_ago=10,
        channel_baseline_views=[1, 2, 3, 4, 5],
        min_views=100,
    )
    assert r.is_trending is False


def test_zero_stdev_protected():
    # Все baseline одинаковые → stdev=0, защита через max(stdev, 1)
    r = compute_trending(
        current_views=10000,
        views_24h_ago=5000,
        channel_baseline_views=[500, 500, 500, 500],
    )
    assert r.zscore_24h is not None
    # (10000 - 500) / 1 = 9500 (огромный z-score, но не падает)
    assert r.zscore_24h > 100


def test_zero_views_24h_ago_does_not_divide_by_zero():
    r = compute_trending(
        current_views=1000,
        views_24h_ago=0,
        channel_baseline_views=[100, 200, 300],
    )
    # (1000 - 0) / max(0, 1) = 1000
    assert r.growth_rate_24h == 1000.0


# ---- Velocity ----

def test_compute_velocity():
    assert compute_velocity(10000, 10) == 1000.0      # 1000 views/ч
    assert compute_velocity(5000, 0.5) == 5000.0      # min 1h clamp
    assert compute_velocity(100, None) is None


def test_velocity_triggers_trending_without_zscore():
    # Свежее видео (1ч), 5000 views → 5000 views/hour → velocity_threshold=1000 → trending
    r = compute_trending(
        current_views=5000,
        views_24h_ago=None,
        channel_baseline_views=[],   # пусто → zscore=None
        hours_since_published=1.0,
        velocity_threshold=1000.0,
        min_views=100,
    )
    assert r.velocity == 5000.0
    assert r.is_trending is True  # velocity ветка OR-логики


def test_velocity_below_threshold_not_trending():
    # velocity=500/h, growth и zscore не выполнены → not trending
    r = compute_trending(
        current_views=5000,
        views_24h_ago=None,
        channel_baseline_views=[],
        hours_since_published=10.0,   # 5000/10 = 500
        velocity_threshold=1000.0,
        min_views=100,
    )
    assert r.velocity == 500.0
    assert r.is_trending is False


# ---- is_rising ----

def test_is_rising_positive_acceleration():
    # views с ускорением: +1000, +3000 → rising
    assert compute_is_rising([1000, 2000, 5000]) is True


def test_is_rising_decelerating():
    # +3000, +1000 → замедление
    assert compute_is_rising([1000, 4000, 5000]) is False


def test_is_rising_insufficient_data():
    assert compute_is_rising([]) is False
    assert compute_is_rising([100]) is False
    assert compute_is_rising([100, 200]) is False


def test_is_rising_plateaued():
    # +1000, +1000 → not rising (d2 == d1)
    assert compute_is_rising([1000, 2000, 3000]) is False


def test_compute_trending_returns_rising():
    r = compute_trending(
        current_views=5000,
        views_24h_ago=None,
        channel_baseline_views=[],
        hours_since_published=2.0,
        recent_snapshots_ascending=[1000, 2000, 5000],
    )
    assert r.is_rising is True
