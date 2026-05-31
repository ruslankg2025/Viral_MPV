"""VK-адаптеры (vk_video, vk_clips) поверх существующего VKClient.

Вся VK-специфика (video.save + wall.post, деградация Клипов) остаётся в
vk_client.py; здесь — только приведение к интерфейсу PlatformPublisher.
"""
from __future__ import annotations

from typing import Any

from platforms.base import NotConfiguredError, PlatformError, PlatformPublisher
from vk_client import VKClient, VKError


class _VKBase(PlatformPublisher):
    def is_configured(self) -> bool:
        return bool(self.settings.vk_access_token)

    def _client(self) -> VKClient:
        return VKClient(
            access_token=self.settings.vk_access_token,
            api_version=self.settings.vk_api_version,
            group_id=self.settings.vk_group_id,
        )

    async def _run(self, coro_name: str, **kwargs: Any) -> dict[str, Any]:
        if not self.is_configured():
            raise NotConfiguredError("vk_access_token_missing")
        try:
            client = self._client()
            return await getattr(client, coro_name)(**kwargs)
        except VKError as exc:
            raise PlatformError(str(exc)) from exc


class VKVideoPublisher(_VKBase):
    platform_id = "vk_video"

    async def publish(self, *, video_path, title, description="", tags=None):
        return await self._run(
            "publish_video",
            video_path=video_path,
            title=title,
            description=description,
            tags=tags,
        )


class VKClipsPublisher(_VKBase):
    platform_id = "vk_clips"

    async def publish(self, *, video_path, title, description="", tags=None):
        # Деградация на video.save + wall.post — см. vk_client.publish_clip.
        return await self._run(
            "publish_clip",
            video_path=video_path,
            title=title,
            description=description,
            tags=tags,
        )
