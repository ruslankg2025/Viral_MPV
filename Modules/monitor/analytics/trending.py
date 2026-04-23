"""
Trending detector — z-score по views за 24 часа против 7-дневного baseline канала.

Алгоритм:
1. Последние 2 snapshot за окно ~24ч → views_now, views_24h_ago.
2. growth_rate_24h = (now - 24h_ago) / max(24h_ago, 1).
3. Baseline канала — средние views по видео канала за 30 дней, кроме текущего.
4. zscore = (views_now - mean) / max(stdev, 1).
5. is_trending = (zscore >= THRESHOLD_Z) AND (growth >= THRESHOLD_GROWTH) AND (views >= MIN_VIEWS).

Edge cases:
- < 2 снапшотов → NULL, is_trending=False
- < 3 видео в истории канала → baseline=NULL, is_trending=False (только growth)
- stdev == 0 → используется 1.0 (защита от zero division)
- views_now < MIN_VIEWS → is_trending=False (шумит в первый час)
"""

import statistics
from dataclasses import dataclass


@dataclass
class TrendingResult:
    zscore_24h: float | None
    growth_rate_24h: float | None
    is_trending: bool


def compute_trending(
    *,
    current_views: int,
    views_24h_ago: int | None,
    channel_baseline_views: list[int],
    zscore_threshold: float = 2.0,
    growth_threshold: float = 0.5,
    min_views: int = 100,
) -> TrendingResult:
    """Посчитать trending для одного видео.

    Parameters
    ----------
    current_views : int
        Текущее количество просмотров (из latest snapshot).
    views_24h_ago : int | None
        Просмотры ~24ч назад. None если недостаточно данных.
    channel_baseline_views : list[int]
        Views других видео канала за 30 дней (исключая текущее).
    """
    # Growth rate
    if views_24h_ago is None:
        growth_rate = None
    else:
        growth_rate = (current_views - views_24h_ago) / max(views_24h_ago, 1)

    # Z-score
    if len(channel_baseline_views) < 3:
        zscore = None
    else:
        mean = statistics.mean(channel_baseline_views)
        try:
            stdev = statistics.stdev(channel_baseline_views)
        except statistics.StatisticsError:
            stdev = 0.0
        if stdev < 1.0:
            stdev = 1.0
        zscore = (current_views - mean) / stdev

    # is_trending
    if current_views < min_views:
        is_trending = False
    elif zscore is None or growth_rate is None:
        is_trending = False
    else:
        is_trending = (zscore >= zscore_threshold) and (growth_rate >= growth_threshold)

    return TrendingResult(
        zscore_24h=zscore,
        growth_rate_24h=growth_rate,
        is_trending=is_trending,
    )
