from analytics.trending import compute_trending


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
