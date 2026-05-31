"""Клиент publisher-сервиса + mock-фикстуры.

Analytics читает активные публикации НАПРЯМУЮ из publisher по REST
(НЕ через shell, порт publisher-а 8000, путь с префиксом /publisher):

    GET http://publisher:8000/publisher/publications?limit=1000
    заголовок X-Worker-Token

Контракт публикации (форма ответа publisher-а):
    {id, platform: "vk_video"|"vk_clips", external_id, title, description,
     tags, status, scheduled_at, published_at, error_message}

Если publisher недоступен (или ANALYTICS_USE_MOCK=1) — переходим на
mock-фикстуры, чтобы сервис и воркер не зависели от готовности publisher.
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog


log = structlog.get_logger()


# Платформы publisher-а, которые в analytics маппятся на VK-адаптер.
VK_PUBLISHER_PLATFORMS = {"vk_video", "vk_clips"}


def mock_publications() -> list[dict[str, Any]]:
    """Тестовые фикстуры публикаций (когда publisher недоступен).

    Покрывают оба VK-типа + один без external_id (ещё не опубликован),
    чтобы fetcher корректно его пропускал.
    """
    return [
        {
            "id": "pub_mock_1",
            "platform": "vk_video",
            "external_id": "-100500_1001",
            "title": "Как я заработал миллион на контенте",
            "description": "разбор воронки",
            "tags": ["финансы", "контент"],
            "status": "published",
            "scheduled_at": None,
            "published_at": "2026-05-20T10:00:00+00:00",
            "error_message": None,
        },
        {
            "id": "pub_mock_2",
            "platform": "vk_clips",
            "external_id": "-100500_1002",
            "title": "3 ошибки новичков",
            "description": "короткий клип",
            "tags": ["советы"],
            "status": "published",
            "scheduled_at": None,
            "published_at": "2026-05-22T12:30:00+00:00",
            "error_message": None,
        },
        {
            "id": "pub_mock_3",
            "platform": "vk_video",
            "external_id": None,  # ещё не опубликован — fetcher пропустит
            "title": "Черновик",
            "description": "",
            "tags": [],
            "status": "scheduled",
            "scheduled_at": "2026-06-01T09:00:00+00:00",
            "published_at": None,
            "error_message": None,
        },
    ]


def mock_vk_metrics(external_id: str) -> dict[str, int]:
    """Детерминированные mock-метрики VK для тестов/dev (без VK_TOKEN).

    Значения зависят от external_id, чтобы агрегации/топы были осмысленными.
    """
    seed = sum(ord(ch) for ch in external_id)
    return {
        "views": 1000 + seed * 10,
        "reach": 800 + seed * 8,
        "likes": 50 + seed,
        "comments": 5 + seed % 7,
        "shares": 3 + seed % 5,
        "saves": 2 + seed % 4,
        "click_through_to_external": seed % 11,
    }


class PublisherClient:
    def __init__(self, *, base_url: str, token: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    async def list_publications(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        """GET /publisher/publications?limit=... с X-Worker-Token.

        При сетевой ошибке / не-200 поднимает httpx-исключение или RuntimeError —
        вызывающий код решает, переходить ли на mock.
        """
        url = f"{self.base_url}/publisher/publications"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                url,
                params={"limit": limit},
                headers={"X-Worker-Token": self.token},
            )
        resp.raise_for_status()
        data = resp.json()
        # publisher может вернуть список или {"items": [...]}.
        if isinstance(data, dict):
            return list(data.get("items") or data.get("publications") or [])
        return list(data)
