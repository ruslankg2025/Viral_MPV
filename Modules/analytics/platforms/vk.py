"""VK-адаптер метрик (Phase 1).

Тянет статистику видео/клипа через VK API (wall.getById / video.get + stats).
external_id публикации в publisher имеет форму "{owner_id}_{item_id}"
(стандартная VK-нотация, напр. "-12345_67890"), что соответствует обоим
платформам publisher-а: vk_video и vk_clips.

Без VK_TOKEN (token=None) сервис не может ходить в VK — fetch_metrics
поднимает VKError. Воркер в этом случае использует mock-фикстуры.
"""
from __future__ import annotations

import httpx

from platforms.base import PlatformAdapter, PlatformMetrics


VK_API_VERSION = "5.199"
VK_VIDEO_GET_URL = "https://api.vk.com/method/video.get"


class VKError(RuntimeError):
    pass


class VKAdapter(PlatformAdapter):
    platform_name = "vk"

    def __init__(
        self,
        *,
        token: str | None = None,
        timeout: float = 15.0,
        api_version: str = VK_API_VERSION,
    ):
        super().__init__(token=token, timeout=timeout)
        self.api_version = api_version

    async def fetch_metrics(self, external_id: str) -> PlatformMetrics:
        if not self.token:
            raise VKError("vk_token_not_set")
        if not external_id:
            raise VKError("empty_external_id")

        params = {
            "videos": external_id,  # "owner_id_video_id"
            "extended": 1,
            "access_token": self.token,
            "v": self.api_version,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(VK_VIDEO_GET_URL, params=params)
        except httpx.RequestError as e:
            raise VKError(f"network: {e}") from e
        if resp.status_code != 200:
            raise VKError(f"vk_http_{resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise VKError(f"vk_parse: {e}") from e

        if "error" in data:
            err = data["error"]
            raise VKError(
                f"vk_api_error_{err.get('error_code')}: {err.get('error_msg')}"
            )

        items = (data.get("response") or {}).get("items") or []
        if not items:
            raise VKError(f"vk_video_not_found: {external_id}")
        return self._parse_item(external_id, items[0])

    def _parse_item(self, external_id: str, item: dict) -> PlatformMetrics:
        """Нормализует VK video object в PlatformMetrics."""
        def _count(field: str) -> int:
            v = item.get(field)
            if isinstance(v, dict):  # VK отдаёт {"count": N}
                return int(v.get("count", 0) or 0)
            return int(v or 0)

        return PlatformMetrics(
            platform=self.platform_name,
            external_id=external_id,
            views=_count("views"),
            reach=_count("reach") or _count("views"),
            likes=_count("likes"),
            comments=_count("comments"),
            shares=_count("reposts"),
            saves=_count("saves"),  # VK обычно не отдаёт — будет 0
            click_through_to_external=0,
        )
