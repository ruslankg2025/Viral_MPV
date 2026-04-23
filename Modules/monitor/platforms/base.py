"""
MetricsSource — общий протокол для платформ (YouTube, IG, TikTok, VK).

Все платформы должны реализовать 3 метода:
- resolve_channel(url): URL → (external_id, channel_name) + метаданные
- fetch_new_videos(external_id, known_ids): список новых видео
- fetch_metrics(external_ids): текущие метрики для списка видео
"""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class VideoMeta:
    external_id: str
    url: str
    title: str | None = None
    description: str | None = None
    thumbnail_url: str | None = None
    duration_sec: int | None = None
    published_at: str | None = None  # ISO
    is_short: bool = False  # YouTube Shorts / Reels / TikTok short — всё ≤ 60s


@dataclass
class MetricsSnapshot:
    external_id: str
    views: int
    likes: int
    comments: int
    duration_sec: int | None = None
    is_short: bool = False


@dataclass
class ChannelInfo:
    external_id: str
    channel_name: str
    # Дополнительные данные, нужные платформе для дальнейших запросов.
    # Для YouTube тут uploads_playlist_id.
    extra: dict = field(default_factory=dict)


class PlatformError(Exception):
    """Базовая ошибка платформы."""


class ChannelNotFound(PlatformError):
    pass


class QuotaExhausted(PlatformError):
    pass


class TransientError(PlatformError):
    """Сетевые/5xx ошибки — можно ретраить."""


class MetricsSource(Protocol):
    name: str

    async def resolve_channel(self, channel_url: str) -> ChannelInfo:
        """Resolve URL в канал. Raises ChannelNotFound/QuotaExhausted/TransientError."""
        ...

    async def fetch_new_videos(
        self,
        channel: ChannelInfo,
        known_external_ids: set[str],
        *,
        results_limit: int | None = None,
    ) -> list[VideoMeta]:
        """Вернуть видео канала, которых ещё нет в known_external_ids.

        results_limit — per-source override. Если None, платформа использует
        свой дефолт (обычно plan.max_results_limit из init).
        """
        ...

    async def fetch_metrics(self, external_ids: list[str]) -> list[MetricsSnapshot]:
        """Текущие метрики для списка видео (batch до 50)."""
        ...
