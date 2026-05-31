"""YouTube Shorts адаптер (Data API v3). СТАБ — реализуется в Phase 3 Track 3-A.

Контракт зафиксирован фундаментом: класс YouTubeShortsPublisher,
platform_id='youtube_shorts', зарегистрирован в platforms/__init__.py.
"""
from __future__ import annotations

from typing import Any

from platforms.base import NotConfiguredError, PlatformPublisher


class YouTubeShortsPublisher(PlatformPublisher):
    platform_id = "youtube_shorts"

    def is_configured(self) -> bool:
        return bool(
            self.settings.youtube_client_id
            and self.settings.youtube_client_secret
            and self.settings.youtube_refresh_token
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
            raise NotConfiguredError("youtube_not_configured")
        # TODO(Phase 3 / Track 3-A): OAuth refresh → videos.insert (resumable upload),
        # #Shorts в заголовок, external_id = videoId. Реализует спавн-агент.
        raise NotImplementedError("youtube live publish not implemented yet")
