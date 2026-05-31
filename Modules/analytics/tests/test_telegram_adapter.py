"""Тесты TelegramAdapter с mock HTTP (httpx.MockTransport).

Telegram Bot API даёт только размер аудитории канала (getChatMemberCount),
который кладётся в reach; per-post метрики недоступны → 0.
"""
import httpx
import pytest

from platforms.telegram import TelegramAdapter, TelegramError, mock_metrics


def _client_factory(handler):
    transport = httpx.MockTransport(handler)

    class _Patched(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    return _Patched


@pytest.mark.asyncio
async def test_fetch_metrics_member_count_to_reach(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "getChatMemberCount" in str(request.url)
        # chat_id извлекается из "{chat_id}_{message_id}" (тут отрицательный)
        assert request.url.params["chat_id"] == "-100123"
        return httpx.Response(200, json={"ok": True, "result": 4321})

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    adapter = TelegramAdapter(token="bot-token")
    m = await adapter.fetch_metrics("-100123_456")
    assert m.platform == "telegram"
    assert m.external_id == "-100123_456"
    assert m.reach == 4321
    # per-post метрики через Bot API недоступны
    assert m.views == 0
    assert m.likes == 0
    assert m.comments == 0
    assert m.shares == 0


@pytest.mark.asyncio
async def test_fetch_metrics_no_token_raises():
    adapter = TelegramAdapter(token=None)
    with pytest.raises(TelegramError):
        await adapter.fetch_metrics("-1_2")


@pytest.mark.asyncio
async def test_fetch_metrics_api_error(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"ok": False, "error_code": 400, "description": "bad"})

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    adapter = TelegramAdapter(token="bad")
    with pytest.raises(TelegramError):
        await adapter.fetch_metrics("-1_2")


def test_mock_metrics_deterministic_and_zero_engagement():
    a = mock_metrics("-100_1")
    b = mock_metrics("-100_1")
    assert a == b  # детерминированно
    assert a["reach"] > 0 and a["views"] > 0
    # mock честно отражает: реакции поста через Bot API недоступны
    assert a["likes"] == 0 and a["comments"] == 0 and a["shares"] == 0
