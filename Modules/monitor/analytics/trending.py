"""
Trending detector — velocity-first, с fallback на z-score и growth_rate.

Цель — «поймать идею в моменте»: рилс, набирающий просмотры быстро и ещё
не насыщенный. Основной сигнал — velocity (views/час с момента публикации),
дополняется:
- growth_rate_24h — как ускорилось за последние 24ч (относительный прирост).
- zscore_24h — насколько аномально относительно baseline канала.
- is_rising — velocity ускоряется (последние 3 snapshot: Δ растёт).

Алгоритм is_trending (OR-логика, а не AND):
- velocity >= VELOCITY_THRESHOLD (ранжирует абсолютную скорость)
- OR (zscore >= ZSCORE_THRESHOLD AND growth >= GROWTH_THRESHOLD)
- с фильтром views >= MIN_VIEWS (anti-noise).

Edge cases:
- нет hours_since_published → velocity=None
- < 2 snapshot → views_24h_ago=None, growth=None
- < 3 snapshot → is_rising=False (не вычислимо)
- < 3 видео в baseline → zscore=None
- stdev == 0 → stdev=1.0
"""

import statistics
from dataclasses import dataclass


@dataclass
class TrendingResult:
    zscore_24h: float | None
    growth_rate_24h: float | None
    is_trending: bool
    velocity: float | None = None
    is_rising: bool = False


def compute_velocity(current_views: int, hours_since_published: float | None) -> float | None:
    """views / hour с момента публикации. None если hours неизвестен."""
    if hours_since_published is None:
        return None
    denom = max(hours_since_published, 1.0)
    return current_views / denom


def compute_is_rising(snapshot_views: list[int]) -> bool:
    """True если последние 3+ snapshot показывают ускорение (Δ-between-intervals растёт).

    snapshot_views ожидается в хронологическом порядке (oldest → newest).
    При < 3 значениях возвращает False.
    """
    if len(snapshot_views) < 3:
        return False
    last = snapshot_views[-3:]
    d1 = last[1] - last[0]
    d2 = last[2] - last[1]
    return d2 > d1


def compute_trending(
    *,
    current_views: int,
    views_24h_ago: int | None,
    channel_baseline_views: list[int],
    hours_since_published: float | None = None,
    recent_snapshots_ascending: list[int] | None = None,
    zscore_threshold: float = 2.0,
    growth_threshold: float = 0.5,
    velocity_threshold: float = 1000.0,  # 1000 views/hour — тысяча просмотров в час
    min_views: int = 100,
) -> TrendingResult:
    """Посчитать trending для одного видео.

    Parameters
    ----------
    current_views : int
        Текущее кол-во просмотров (из latest snapshot).
    views_24h_ago : int | None
        Просмотры ровно ~24ч назад. None если нет такого snapshot.
    channel_baseline_views : list[int]
        Views других видео канала за 30 дней (исключая текущее).
    hours_since_published : float | None
        Часы с момента публикации. Нужен для velocity.
    recent_snapshots_ascending : list[int] | None
        Последние N (N>=3) snapshots views в возрастающем порядке времени.
        Нужен для is_rising.
    velocity_threshold : float
        Минимальная velocity (views/h), чтобы попасть в trending по velocity-ветке.
    """
    # Growth rate
    if views_24h_ago is None:
        growth_rate = None
    else:
        growth_rate = (current_views - views_24h_ago) / max(views_24h_ago, 1)

    # Velocity
    velocity = compute_velocity(current_views, hours_since_published)

    # Rising
    is_rising = compute_is_rising(recent_snapshots_ascending or [])

    # Z-score (within-channel, как есть)
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

    # is_trending: OR-логика между velocity и (zscore + growth)
    if current_views < min_views:
        is_trending = False
    else:
        velocity_hit = velocity is not None and velocity >= velocity_threshold
        combo_hit = (
            zscore is not None and growth_rate is not None
            and zscore >= zscore_threshold
            and growth_rate >= growth_threshold
        )
        is_trending = velocity_hit or combo_hit

    return TrendingResult(
        zscore_24h=zscore,
        growth_rate_24h=growth_rate,
        is_trending=is_trending,
        velocity=velocity,
        is_rising=is_rising,
    )
