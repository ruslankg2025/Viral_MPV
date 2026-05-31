"""Pinterest адаптер (API v5). СТАБ — реализуется в Phase 2 Track 2-B.

Контракт зафиксирован фундаментом: класс PinterestPublisher,
platform_id='pinterest', зарегистрирован в platforms/__init__.py.
Особенность: Pinterest РФ-ограничен → для prod нужен residential proxy
(settings.http_proxy).
"""
from __future__ import annotations

from typing import Any

from platforms.base import NotConfiguredError, PlatformPublisher


class PinterestPublisher(PlatformPublisher):
    platform_id = "pinterest"

    def is_configured(self) -> bool:
        return bool(
            self.settings.pinterest_app_id
            and self.settings.pinterest_app_secret
            and self.settings.pinterest_refresh_token
            and self.settings.pinterest_board_id
        )

    async def publish(
        self,
        *,
        video_path: str | None,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured():
            raise NotConfiguredError("pinterest_not_configured")
        # TODO(Phase 2 / Track 2-B): OAuth refresh → media upload → pins.create
        # (Idea Pin для видео, fallback на image pin), external_id = pin id.
        # httpx с прокси settings.http_proxy. Реализует спавн-агент.
        raise NotImplementedError("pinterest live publish not implemented yet")
