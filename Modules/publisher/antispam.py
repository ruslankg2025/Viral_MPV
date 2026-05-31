"""Phase 4 — чистая логика: разброс расписания и anti-spam cooldown.

Никакой сети и внешних данных. Всё детерминировано (jitter и хэш — от входных
строк/индексов, а не от random/now), поэтому юнит-тесты воспроизводимы.

Состоит из трёх частей:
  * spread_schedule  — раздвигает близкие публикации по времени (±jitter);
  * content_hash     — стабильный хэш контента (заголовок+описание);
  * antispam_warnings — собирает предупреждения rate-cap / content-cooldown
                        поверх запросов к storage (count/recent hashes).
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, Protocol


# ── разбор / форматирование iso ───────────────────────────────────────────────
def _parse_iso(value: str) -> datetime:
    """Парсит iso-строку в aware datetime (UTC, если tz не указан)."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ── 1. разброс расписания ─────────────────────────────────────────────────────
def _jitter_minutes(seed: str, jitter_min: int) -> int:
    """Детерминированный сдвиг в диапазоне [-jitter_min, +jitter_min].

    Источник «случайности» — стабильный хэш seed (id/платформа), а не random,
    поэтому при одних и тех же входных данных результат всегда одинаков.
    """
    if jitter_min <= 0:
        return 0
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    span = 2 * jitter_min + 1  # значений в диапазоне [-jitter_min, +jitter_min]
    return (int(digest[:8], 16) % span) - jitter_min


def spread_schedule(
    base_iso: str,
    index: int,
    *,
    step_min: int = 15,
    jitter_min: int = 15,
    seed: str | None = None,
) -> str:
    """Сдвигает базовое время для index-й публикации.

    offset = index*step_min + jitter, где jitter ∈ [-jitter_min, +jitter_min]
    детерминирован по seed (по умолчанию seed = str(index)).

    Гарантии (используются в тестах):
      * index=0 без seed → ровно base (offset(0,"0") даёт jitter, но шаг 0;
        для абсолютной стабильности «нулевого» слота см. ниже);
      * результат воспроизводим при одинаковых (base, index, seed);
      * фактический сдвиг ∈ [index*step - jitter, index*step + jitter] минут.
    """
    base = _parse_iso(base_iso)
    seed = seed if seed is not None else str(index)
    jitter = _jitter_minutes(seed, jitter_min)
    offset_min = index * step_min + jitter
    shifted = base + timedelta(minutes=offset_min)
    return shifted.isoformat()


# ── 2. хэш контента ───────────────────────────────────────────────────────────
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _normalize(text: str) -> str:
    """Нижний регистр + схлопывание слов — устойчиво к пробелам/пунктуации."""
    return " ".join(_WORD_RE.findall((text or "").lower()))


def content_hash(title: str, description: str = "") -> str:
    """Стабильный хэш контента (заголовок+описание).

    Нормализуем текст, чтобы мелкие отличия в пробелах/регистре/пунктуации
    давали тот же хэш (простая защита от near-duplicate репостов).
    """
    norm = f"{_normalize(title)}\n{_normalize(description)}"
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# ── 3. anti-spam предупреждения ───────────────────────────────────────────────
class _Store(Protocol):
    def count_recent_by_platform(self, *, platform: str, since_iso: str) -> int: ...
    def recent_content_hashes(self, *, since_iso: str) -> Iterable[str]: ...


def _window_start(now: datetime, hours: int) -> str:
    return (now - timedelta(hours=hours)).isoformat()


def antispam_warnings(
    store: _Store,
    *,
    platform: str,
    title: str,
    description: str = "",
    now: datetime | None = None,
    rate_window_hours: int = 4,
    rate_limit: int = 3,
    content_cooldown_hours: int = 24,
) -> list[str]:
    """Собирает предупреждения (НЕ блокирует публикацию).

    * rate-cap: >= rate_limit публикаций на платформе за rate_window_hours;
    * content-cooldown: похожий контент публиковался за content_cooldown_hours.

    `now` прокидывается параметром (а не datetime.now()), чтобы тесты были
    детерминированными.
    """
    now = now or datetime.now(timezone.utc)
    warnings: list[str] = []

    # rate-cap
    if rate_limit > 0 and rate_window_hours > 0:
        since = _window_start(now, rate_window_hours)
        recent = store.count_recent_by_platform(platform=platform, since_iso=since)
        if recent >= rate_limit:
            warnings.append(
                f"rate_cap: за последние {rate_window_hours}ч на {platform} уже "
                f"{recent} публикаций (лимит {rate_limit})"
            )

    # content-cooldown
    if content_cooldown_hours > 0:
        since = _window_start(now, content_cooldown_hours)
        chash = content_hash(title, description)
        if chash in set(store.recent_content_hashes(since_iso=since)):
            warnings.append(
                f"content_cooldown: похожий контент публиковался за последние "
                f"{content_cooldown_hours}ч"
            )

    return warnings
