"""Telegram-канал адаптер (Bot API). СТАБ — реализуется в Phase 3 Track 3-B.

Контракт зафиксирован фундаментом: класс TelegramPublisher, platform_id='telegram',
зарегистрирован в platforms/__init__.py. Агент реализует publish()/is_configured().
"""
from __future__ import annotations

from typing import Any

from platforms.base import NotConfiguredError, PlatformPublisher


class TelegramPublisher(PlatformPublisher):
    platform_id = "telegram"

    def is_configured(self) -> bool:
        return bool(self.settings.telegram_bot_token and self.settings.telegram_channel_id)

    async def publish(
        self,
        *,
        video_path: str | None,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured():
            raise NotConfiguredError("telegram_not_configured")
        # TODO(Phase 3 / Track 3-B): sendVideo в канал через Bot API,
        # external_id = message_id. Реализует спавн-агент.
        raise NotImplementedError("telegram live publish not implemented yet")
