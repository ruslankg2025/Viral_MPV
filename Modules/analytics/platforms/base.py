"""Абстрактный адаптер платформы для analytics.

Каждый адаптер умеет по external_id публикации вернуть нормализованный
набор метрик `PlatformMetrics`. Реализованы адаптеры VK (platforms/vk.py),
Telegram (telegram.py), YouTube (youtube.py), Pinterest (pinterest.py) и
Дзен (zen.py — заглушка-mock, публичного API метрик нет). Каждый адаптер
без живого токена поднимает своё *Error, и воркер уходит в mock-ветку
(см. fetcher.fetch_one); Дзен токена не требует и всегда отдаёт mock.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass


@dataclass
class PlatformMetrics:
    """Нормализованные метрики одной публикации на одной платформе.

    Поля совпадают (по смыслу) с числовыми колонками platform_metrics.
    """
    platform: str
    external_id: str
    views: int = 0
    reach: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    click_through_to_external: int = 0

    def as_metric_kwargs(self) -> dict[str, int]:
        """kwargs для AnalyticsStore.insert_metrics (без platform/external_id)."""
        d = asdict(self)
        d.pop("platform", None)
        d.pop("external_id", None)
        return d


class PlatformAdapter(ABC):
    """Базовый адаптер. platform_name — ключ платформы в БД."""

    platform_name: str = "base"

    def __init__(self, *, token: str | None = None, timeout: float = 15.0):
        self.token = token
        self.timeout = timeout

    @abstractmethod
    async def fetch_metrics(self, external_id: str) -> PlatformMetrics:
        """Тянет метрики публикации по её external_id на платформе."""
        raise NotImplementedError
