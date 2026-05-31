"""Telegram-адаптер метрик.

ВАЖНО про доступность метрик в Telegram:
Bot API НЕ отдаёт per-post аналитику (просмотры/реакции отдельного поста
программно недоступны без MTProto/клиентского API). Единственное, что реально
можно достать через Bot API, — это размер аудитории канала:
  - getChatMemberCount (https://core.telegram.org/bots/api#getchatmembercount).
Просмотры поста (`views`) присутствуют в объекте Message ТОЛЬКО при получении
апдейтов канала через MTProto (telethon/pyrogram), а не через Bot API, поэтому
здесь честно: views/likes/comments/shares/saves недоступны → 0.

external_id публикации в publisher для telegram имеет форму
"{chat_id}_{message_id}" (аналогично VK-нотации). Мы используем chat_id (часть
до последнего "_") для запроса getChatMemberCount и кладём результат в `reach`
(потенциальный охват = размер аудитории канала). Это явное, документированное
приближение, а не реальный охват поста.

Без TELEGRAM_BOT_TOKEN (token=None) — TelegramError; воркер тогда берёт
mock-метрики (mock_metrics).
"""
from __future__ import annotations

import httpx

from platforms.base import PlatformAdapter, PlatformMetrics


def _telegram_api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def mock_metrics(external_id: str) -> dict[str, int]:
    """Детерминированные mock-метрики Telegram (без токена / для dev/тестов).

    Telegram через Bot API почти ничего не отдаёт, поэтому mock условный —
    только чтобы dev-агрегации были непустыми. reach — «размер аудитории»,
    views ~ часть reach. likes/comments/shares/saves остаются 0 (как в реале).
    """
    seed = sum(ord(ch) for ch in external_id)
    reach = 500 + seed * 5
    return {
        "views": int(reach * 0.6),
        "reach": reach,
        "likes": 0,  # Bot API не отдаёт реакции отдельного поста
        "comments": 0,
        "shares": 0,
        "saves": 0,
        "click_through_to_external": 0,
    }


class TelegramError(RuntimeError):
    pass


class TelegramAdapter(PlatformAdapter):
    """Реальный адаптер Telegram (Bot API).

    Реально доступно: размер аудитории канала (getChatMemberCount) → reach.
    Остальные метрики поста Bot API не предоставляет → 0.
    """

    platform_name = "telegram"

    def _chat_id(self, external_id: str) -> str:
        """external_id "{chat_id}_{message_id}" → chat_id.

        Поддерживает отрицательные chat_id ("-100123_456" → "-100123").
        Если "_" нет — считаем, что весь external_id уже chat_id.
        """
        if "_" in external_id:
            return external_id.rsplit("_", 1)[0]
        return external_id

    async def fetch_metrics(self, external_id: str) -> PlatformMetrics:
        if not self.token:
            raise TelegramError("telegram_bot_token_not_set")
        if not external_id:
            raise TelegramError("empty_external_id")

        chat_id = self._chat_id(external_id)
        url = _telegram_api_url(self.token, "getChatMemberCount")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params={"chat_id": chat_id})
        except httpx.RequestError as e:
            raise TelegramError(f"network: {e}") from e
        if resp.status_code != 200:
            raise TelegramError(f"telegram_http_{resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise TelegramError(f"telegram_parse: {e}") from e

        if not data.get("ok"):
            raise TelegramError(
                f"telegram_api_error_{data.get('error_code')}: {data.get('description')}"
            )

        # getChatMemberCount → {"ok": true, "result": <int>}
        member_count = int(data.get("result") or 0)
        return PlatformMetrics(
            platform=self.platform_name,
            external_id=external_id,
            views=0,             # per-post views недоступны через Bot API
            reach=member_count,  # аудитория канала как потенциальный охват
            likes=0,
            comments=0,
            shares=0,
            saves=0,
            click_through_to_external=0,
        )
