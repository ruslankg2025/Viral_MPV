"""Клиент VK API (live-путь). Все сетевые вызовы — через httpx.AsyncClient.

Phase 1: загрузка видео (video.save → upload-server → подтверждение),
публикация на стену (wall.post с attachment), статистика (stats.getPostReach).

Клипы (vk_clips): официального документированного метода для загрузки в Клипы
через Public API НЕТ. Существует недокументированный/скрытый метод
`shortVideo.create`, но он не входит в официальную документацию vk.com/dev и
может отвалиться/потребовать спец-прав. Поэтому Клипы деградируют на путь
video.save + wall.post (см. publish_clip). Подробности — README "Ограничение VK Клипов".

КРИТИЧНО: этот модуль НЕ должен вызываться в dry-run. Реальные HTTP к
api.vk.com делает только он; dry-run.py его не трогает.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

API_BASE = "https://api.vk.com/method"


class VKError(RuntimeError):
    """Ошибка уровня VK API (error в ответе) или транспортная."""


class VKClient:
    def __init__(
        self,
        *,
        access_token: str,
        api_version: str = "5.199",
        group_id: str = "",
        timeout: float = 60.0,
    ):
        if not access_token:
            raise VKError("vk_access_token_missing")
        self.access_token = access_token
        self.api_version = api_version
        self.group_id = group_id.strip()
        self.timeout = timeout

    # ── низкоуровневый вызов method ───────────────────────────────────────────
    async def _call(self, client: httpx.AsyncClient, method: str, **params: Any) -> dict[str, Any]:
        payload = {k: v for k, v in params.items() if v is not None}
        payload["access_token"] = self.access_token
        payload["v"] = self.api_version
        resp = await client.post(f"{API_BASE}/{method}", data=payload)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            err = data["error"]
            msg = err.get("error_msg", "unknown")
            code = err.get("error_code", -1)
            raise VKError(f"vk_api_error[{code}]: {msg}")
        return data.get("response", {})

    def _owner_id(self) -> int | None:
        if not self.group_id:
            return None
        gid = self.group_id.lstrip("-")
        return -int(gid)

    # ── video.save: получить upload_url, залить файл, подтвердить ──────────────
    async def upload_video(
        self,
        *,
        video_path: str,
        title: str,
        description: str = "",
    ) -> dict[str, Any]:
        """video.save → POST файла на upload_url → возвращает {owner_id, video_id}.

        Возвращает dict с external_id вида "{owner_id}_{video_id}".
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            save = await self._call(
                client,
                "video.save",
                name=title,
                description=description,
                group_id=self.group_id.lstrip("-") or None,
            )
            upload_url = save.get("upload_url")
            if not upload_url:
                raise VKError("video.save_no_upload_url")

            with open(video_path, "rb") as fh:
                files = {"video_file": (os.path.basename(video_path), fh, "video/mp4")}
                up = await client.post(upload_url, files=files)
            up.raise_for_status()
            up_data = up.json()

            owner_id = up_data.get("owner_id") or save.get("owner_id")
            video_id = up_data.get("video_id") or save.get("video_id")
            if owner_id is None or video_id is None:
                raise VKError(f"video_upload_no_ids: {up_data}")
            return {
                "owner_id": owner_id,
                "video_id": video_id,
                "external_id": f"{owner_id}_{video_id}",
            }

    # ── wall.post с прикреплённым видео ───────────────────────────────────────
    async def wall_post(
        self,
        *,
        message: str,
        attachment: str,
    ) -> dict[str, Any]:
        """wall.post → {post_id}. owner_id = группа (если задана)."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await self._call(
                client,
                "wall.post",
                owner_id=self._owner_id(),
                message=message,
                attachments=attachment,
                from_group=1 if self.group_id else None,
            )
            return resp

    # ── stats.getPostReach ────────────────────────────────────────────────────
    async def post_reach(self, *, owner_id: int, post_id: int) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await self._call(
                client,
                "stats.getPostReach",
                owner_id=owner_id,
                post_ids=post_id,
            )
            return resp

    # ── высокоуровневые сценарии ──────────────────────────────────────────────
    async def publish_video(
        self,
        *,
        video_path: str,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """vk_video: video.save + wall.post. Возвращает {external_id, post_id?}."""
        msg = _compose_message(title, description, tags)
        up = await self.upload_video(video_path=video_path, title=title, description=description)
        attachment = f"video{up['external_id']}"
        post = await self.wall_post(message=msg, attachment=attachment)
        return {"external_id": up["external_id"], "post_id": post.get("post_id")}

    async def publish_clip(
        self,
        *,
        video_path: str,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """vk_clips: ДЕГРАДАЦИЯ на video.save + wall.post.

        Официального документированного метода загрузки в Клипы через Public API
        нет (см. docstring модуля и README). Видео грузится как обычное VK-видео
        и публикуется на стену; VK может (но не гарантированно) показать его в
        Клипах по соотношению сторон. Несуществующий метод НЕ вызываем.
        """
        return await self.publish_video(
            video_path=video_path, title=title, description=description, tags=tags
        )


def _compose_message(title: str, description: str, tags: list[str] | None) -> str:
    parts = [p for p in (title, description) if p]
    if tags:
        parts.append(" ".join(f"#{t.lstrip('#')}" for t in tags))
    return "\n\n".join(parts)
