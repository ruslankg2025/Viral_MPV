"""Vitality classifier для авторов + smart cooldown логика.

vitality — классификация «здоровья» источника:
  broken → постоянная ошибка resolve/fetch
  empty  → ≥3 успешных обхода, но 0 видео (приватный/inactive аккаунт)
  active → последнее видео <2 дней назад
  slow   → 2-7 дней
  silent → >7 дней

Используется для:
- Отображения badge в UI (router._source_to_response)
- Smart cooldown: реже опрашиваем тех, кто давно не публикует (экономия Apify)
"""
from datetime import datetime, timezone

# Множители интервала по vitality. Применяются поверх source.interval_min.
# active=1 — без изменений, остальные — реже.
COOLDOWN_MULTIPLIERS: dict[str, int] = {
    "active": 1,
    "slow":   2,
    "silent": 4,
    "empty":  4,
    "broken": 8,
}


def compute_vitality(row, store) -> tuple[str, float | None]:
    """Классификация «здоровья» источника. (vitality, age_days_or_None)."""
    if row.last_error:
        return "broken", None
    age_days = store.days_since_latest_video(row.id)
    if age_days is None:
        if store.count_successful_crawls(row.id) >= 3:
            return "empty", None
        return "active", None
    if age_days < 2:
        return "active", age_days
    if age_days < 7:
        return "slow", age_days
    return "silent", age_days


def should_crawl_now(row, vitality: str, *, now: datetime | None = None) -> bool:
    """Решает, делать ли реальный crawl на этом тике scheduler-а.

    Логика: если у источника есть `last_crawled_at`, считаем сколько прошло секунд.
    Эффективный интервал = source.interval_min × cooldown_multiplier(vitality).
    Если прошло меньше — пропускаем тик (return False).

    active авторы всегда crawl-ятся (multiplier=1, gate всегда пропускает).
    Источники без last_crawled_at — тоже всегда crawl-ятся (первичный обход).
    """
    if not row.last_crawled_at:
        return True

    multiplier = COOLDOWN_MULTIPLIERS.get(vitality, 1)
    if multiplier <= 1:
        return True

    now = now or datetime.now(timezone.utc)
    try:
        last = datetime.fromisoformat(row.last_crawled_at)
    except (ValueError, TypeError):
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)

    effective_interval_sec = (row.interval_min or 60) * 60 * multiplier
    elapsed_sec = (now - last).total_seconds()
    return elapsed_sec >= effective_interval_sec
