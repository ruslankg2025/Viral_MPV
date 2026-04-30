"""Тесты compute_vitality + should_crawl_now (smart cooldown)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from analytics.vitality import (
    COOLDOWN_MULTIPLIERS,
    compute_vitality,
    should_crawl_now,
)


# ============================================================
# compute_vitality
# ============================================================

def _row(*, last_error="", interval_min=60, last_crawled_at=None, source_id="src-1"):
    return SimpleNamespace(
        id=source_id,
        last_error=last_error,
        interval_min=interval_min,
        last_crawled_at=last_crawled_at,
    )


def test_compute_vitality_broken_when_last_error_set():
    row = _row(last_error="resolve_failed")
    store = MagicMock()
    v, age = compute_vitality(row, store)
    assert v == "broken"
    assert age is None
    # Не должны вызывать days_since_latest_video если broken
    store.days_since_latest_video.assert_not_called()


def test_compute_vitality_empty_after_3_successful_crawls_no_videos():
    row = _row()
    store = MagicMock()
    store.days_since_latest_video.return_value = None
    store.count_successful_crawls.return_value = 5
    v, age = compute_vitality(row, store)
    assert v == "empty"
    assert age is None


def test_compute_vitality_active_for_new_source_without_videos_yet():
    """Новые источники (<3 успешных обхода) — active, даём шанс набрать статистику."""
    row = _row()
    store = MagicMock()
    store.days_since_latest_video.return_value = None
    store.count_successful_crawls.return_value = 1
    v, _ = compute_vitality(row, store)
    assert v == "active"


@pytest.mark.parametrize("age_days,expected", [
    (0.5,  "active"),
    (1.9,  "active"),
    (2.0,  "slow"),
    (5.0,  "slow"),
    (7.0,  "silent"),
    (30.0, "silent"),
])
def test_compute_vitality_age_classification(age_days, expected):
    row = _row()
    store = MagicMock()
    store.days_since_latest_video.return_value = age_days
    v, age = compute_vitality(row, store)
    assert v == expected
    assert age == age_days


# ============================================================
# should_crawl_now (smart cooldown)
# ============================================================

def test_should_crawl_now_true_when_no_last_crawled_at():
    row = _row(interval_min=60, last_crawled_at=None)
    assert should_crawl_now(row, "silent") is True
    assert should_crawl_now(row, "active") is True


def test_should_crawl_now_active_always_true():
    """active авторы всегда crawl-ятся (multiplier=1)."""
    now = datetime.now(timezone.utc)
    just_now = (now - timedelta(seconds=10)).isoformat()
    row = _row(interval_min=60, last_crawled_at=just_now)
    assert should_crawl_now(row, "active", now=now) is True


def test_should_crawl_now_slow_doubles_interval():
    """slow → multiplier×2. Через 60 мин (когда scheduler будит) — пропускаем."""
    now = datetime.now(timezone.utc)
    sixty_min_ago = (now - timedelta(minutes=60)).isoformat()
    row = _row(interval_min=60, last_crawled_at=sixty_min_ago)
    # interval=60min × multiplier=2 = эффективно 120min. Прошло только 60 — skip.
    assert should_crawl_now(row, "slow", now=now) is False

    # Через 121 минуту — crawl-им.
    long_ago = (now - timedelta(minutes=121)).isoformat()
    row = _row(interval_min=60, last_crawled_at=long_ago)
    assert should_crawl_now(row, "slow", now=now) is True


def test_should_crawl_now_silent_quadruples_interval():
    """silent → multiplier×4. Через 3 часа при interval=60 — skip."""
    now = datetime.now(timezone.utc)
    three_h_ago = (now - timedelta(hours=3)).isoformat()
    row = _row(interval_min=60, last_crawled_at=three_h_ago)
    # 60min × 4 = 240min эффективно. 180min < 240 → skip.
    assert should_crawl_now(row, "silent", now=now) is False

    # 5 часов — crawl-им.
    long_ago = (now - timedelta(hours=5)).isoformat()
    row = _row(interval_min=60, last_crawled_at=long_ago)
    assert should_crawl_now(row, "silent", now=now) is True


def test_should_crawl_now_broken_extra_long_cooldown():
    """broken → multiplier×8. Прошло 7 часов при interval=60 — всё ещё skip."""
    now = datetime.now(timezone.utc)
    seven_h_ago = (now - timedelta(hours=7)).isoformat()
    row = _row(interval_min=60, last_crawled_at=seven_h_ago)
    # 60×8=480min. 7h=420min < 480 → skip.
    assert should_crawl_now(row, "broken", now=now) is False
    # 9 часов — crawl-им
    nine_h_ago = (now - timedelta(hours=9)).isoformat()
    row = _row(interval_min=60, last_crawled_at=nine_h_ago)
    assert should_crawl_now(row, "broken", now=now) is True


def test_should_crawl_now_handles_naive_datetime_string():
    """last_crawled_at может быть в БД без tz — должно работать (assume UTC)."""
    now = datetime.now(timezone.utc)
    naive = (now - timedelta(hours=10)).replace(tzinfo=None).isoformat()
    row = _row(interval_min=60, last_crawled_at=naive)
    # silent: 60×4=240min. 10h=600 > 240 → crawl
    assert should_crawl_now(row, "silent", now=now) is True


def test_should_crawl_now_handles_garbage_timestamp():
    """Сломанный last_crawled_at не должен обвалить scheduler — fail open."""
    row = _row(interval_min=60, last_crawled_at="not-a-date")
    assert should_crawl_now(row, "silent") is True


def test_cooldown_multipliers_have_all_vitality_keys():
    """Регрессия: новый vitality value должен также добавиться в COOLDOWN_MULTIPLIERS."""
    expected = {"active", "slow", "silent", "empty", "broken"}
    assert expected.issubset(COOLDOWN_MULTIPLIERS.keys())
