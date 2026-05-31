"""Dry-run публикация: НЕ делает HTTP к api.vk.com.

Логирует полный payload и возвращает синтетический external_id 'dry-run-<uuid>'.
КРИТИЧНО: этот модуль не импортирует vk_client и не создаёт httpx-клиент.
"""
from __future__ import annotations

import uuid
from typing import Any

from logging_setup import get_logger

log = get_logger()


def simulate_publish(
    *,
    platform: str,
    video_path: str | None,
    title: str,
    description: str,
    tags: list[str],
) -> dict[str, Any]:
    """Возвращает {external_id, payload}. Никаких сетевых вызовов."""
    external_id = f"dry-run-{uuid.uuid4()}"
    payload = {
        "platform": platform,
        "video_path": video_path,
        "title": title,
        "description": description,
        "tags": tags,
    }
    log.info(
        "dry_run_publish",
        platform=platform,
        external_id=external_id,
        title=title,
        description=description,
        tags=tags,
        video_path=video_path,
        note="NO HTTP to api.vk.com",
    )
    return {"external_id": external_id, "payload": payload}
