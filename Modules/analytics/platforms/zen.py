"""Zen (Яндекс.Дзен) — адаптер метрик.

У Дзена НЕТ публичного API для метрик отдельной публикации. Кабинет
авторской статистики не предоставляет открытого программного доступа
(метрики доступны только вручную в веб-кабинете). Поэтому этот адаптер —
ЗАГЛУШКА: он не ходит ни в какую сеть и всегда возвращает детерминированные
mock-метрики, рассчитанные из external_id (чтобы dev-агрегации были
непустыми и воспроизводимыми).

Если у проекта появится приватный доступ к статистике Дзена, реальный
fetch стоит добавить здесь по аналогии с vk.py. Сейчас — честный mock.

token здесь игнорируется (API нет); fetch_metrics НЕ поднимает ошибку при
отсутствии токена — она всегда возвращает mock.
"""
from __future__ import annotations

from platforms.base import PlatformAdapter, PlatformMetrics


def mock_metrics(external_id: str) -> dict[str, int]:
    """Детерминированные mock-метрики Дзена (единственный источник: API нет)."""
    seed = sum(ord(ch) for ch in external_id)
    views = 1200 + seed * 9
    return {
        "views": views,
        "reach": int(views * 0.85),
        "likes": 30 + seed % 23,
        "comments": 4 + seed % 6,
        "shares": 2 + seed % 4,
        "saves": 0,  # у Дзена нет «сохранений» в публичном смысле
        "click_through_to_external": seed % 9,
    }


class ZenError(RuntimeError):
    pass


class ZenAdapter(PlatformAdapter):
    """Заглушка Дзена: публичного API метрик нет → всегда mock.

    В отличие от остальных адаптеров, НЕ требует токена и НЕ делает HTTP —
    возвращает детерминированные mock-метрики по external_id.
    """

    platform_name = "zen"

    async def fetch_metrics(self, external_id: str) -> PlatformMetrics:
        if not external_id:
            raise ZenError("empty_external_id")
        m = mock_metrics(external_id)
        return PlatformMetrics(
            platform=self.platform_name,
            external_id=external_id,
            **m,
        )
