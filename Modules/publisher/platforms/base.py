"""Платформенная абстракция publisher.

Каждая площадка (VK, Telegram, YouTube, Pinterest, …) реализует
`PlatformPublisher.publish` — live-публикацию одного видео. dry-run сюда НЕ
заходит: симуляция платформо-агностична и живёт в dry_run.py. Таким образом
сетевые вызовы инкапсулированы в адаптерах, а service.py их только диспетчеризует.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from config import Settings


class PlatformError(RuntimeError):
    """Ошибка публикации на конкретной платформе (API/транспорт/валидация)."""


class NotConfiguredError(PlatformError):
    """Нет необходимых креденшелов для live-публикации на этой площадке."""


class PlatformPublisher(ABC):
    """Базовый адаптер площадки. Конструируется из Settings.

    Контракт publish():
      вход  — video_path (путь к mp4 под MEDIA_DIR), title, description, tags;
      выход — dict с обязательным ключом 'external_id' (str) и любыми доп. полями;
      ошибки — PlatformError / NotConfiguredError.
    """

    platform_id: str = ""

    def __init__(self, settings: Settings):
        self.settings = settings

    def is_configured(self) -> bool:
        """Есть ли креды для live. По умолчанию False; адаптеры переопределяют."""
        return False

    @abstractmethod
    async def publish(
        self,
        *,
        video_path: str | None,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError


def compose_caption(title: str, description: str, tags: list[str] | None) -> str:
    """Единый помощник: title + description + #теги. Используют адаптеры."""
    parts = [p for p in (title, description) if p]
    if tags:
        parts.append(" ".join(f"#{t.lstrip('#')}" for t in tags))
    return "\n\n".join(parts)
