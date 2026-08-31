"""Реестр платформенных адаптеров publisher.

Добавление площадки = новый файл platforms/<name>.py + строка в _REGISTRY.
service.py диспетчеризует live-публикацию через get_publisher().
"""
from __future__ import annotations

from config import Settings
from platforms.base import (
    NotConfiguredError,
    PlatformError,
    PlatformPublisher,
)
from platforms.pinterest import PinterestPublisher
from platforms.telegram import TelegramPublisher
from platforms.vk import VKClipsPublisher, VKVideoPublisher
from platforms.youtube import YouTubeShortsPublisher

_REGISTRY: dict[str, type[PlatformPublisher]] = {
    "vk_video": VKVideoPublisher,
    "vk_clips": VKClipsPublisher,
    "telegram": TelegramPublisher,
    "youtube_shorts": YouTubeShortsPublisher,
    "pinterest": PinterestPublisher,
}


def get_publisher(platform: str, settings: Settings) -> PlatformPublisher:
    cls = _REGISTRY.get(platform)
    if cls is None:
        raise KeyError(f"unknown_platform: {platform}")
    return cls(settings)


def known_platforms() -> list[str]:
    return list(_REGISTRY)


__all__ = [
    "get_publisher",
    "known_platforms",
    "PlatformPublisher",
    "PlatformError",
    "NotConfiguredError",
]
