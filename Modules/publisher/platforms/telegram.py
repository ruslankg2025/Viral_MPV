"""Telegram-канал адаптер (Bot API).

Публикует видео в канал через `sendVideo`:
  POST https://api.telegram.org/bot<token>/sendVideo
  multipart: chat_id=<channel_id>, video=<файл>, caption=<title+desc+#теги>

Контракт зафиксирован фундаментом: класс TelegramPublisher, platform_id='telegram',
зарегистрирован в platforms/__init__.py. Сетевые вызовы — через httpx.AsyncClient.

external_id формируется как "{channel_id}_{message_id}" (по аналогии с VK
"{owner_id}_{video_id}"): channel_id однозначно идентифицирует канал, message_id —
сообщение внутри него.

Лимит загрузки видео для ботов через Bot API — ~50 MB на файл (ограничение
Telegram для обычной отправки файлов ботом). Видео крупнее придётся слать иначе
(например, через локальный Bot API сервер); здесь это не поддерживается.

Caption отправляется как обычный текст БЕЗ parse_mode: title/description/теги —
произвольный пользовательский ввод, и символы вроде `<`, `>`, `&` сломали бы
HTML-разбор. Plain text надёжнее и не требует экранирования.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from platforms.base import (
    NotConfiguredError,
    PlatformError,
    PlatformPublisher,
    compose_caption,
)

API_BASE = "https://api.telegram.org"

# Лимит Telegram на caption — 1024 символа. Длинный текст усекаем, чтобы
# sendVideo не падал с ошибкой валидации.
CAPTION_LIMIT = 1024


class TelegramPublisher(PlatformPublisher):
    platform_id = "telegram"

    timeout: float = 120.0

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
        if not video_path:
            raise PlatformError("telegram_video_path_missing")

        token = self.settings.telegram_bot_token
        chat_id = self.settings.telegram_channel_id
        caption = compose_caption(title, description, tags)[:CAPTION_LIMIT]

        url = f"{API_BASE}/bot{token}/sendVideo"
        data = {"chat_id": chat_id, "caption": caption}

        try:
            with open(video_path, "rb") as fh:
                files = {"video": (os.path.basename(video_path), fh, "video/mp4")}
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, data=data, files=files)
            resp.raise_for_status()
            payload = resp.json()
        except FileNotFoundError as exc:
            raise PlatformError(f"telegram_video_not_found: {video_path}") from exc
        except httpx.HTTPError as exc:
            raise PlatformError(f"telegram_http_error: {exc}") from exc
        except ValueError as exc:  # невалидный JSON в ответе
            raise PlatformError(f"telegram_bad_response: {exc}") from exc

        if not payload.get("ok"):
            desc = payload.get("description", "unknown")
            code = payload.get("error_code", -1)
            raise PlatformError(f"telegram_api_error[{code}]: {desc}")

        result = payload.get("result") or {}
        message_id = result.get("message_id")
        if message_id is None:
            raise PlatformError(f"telegram_no_message_id: {payload}")

        return {
            "external_id": f"{chat_id}_{message_id}",
            "message_id": message_id,
        }
