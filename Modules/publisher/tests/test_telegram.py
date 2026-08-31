"""TelegramPublisher live-путь с мок-транспортом httpx (без реальных HTTP)."""
import httpx
import pytest

from config import Settings, get_settings
from platforms.base import NotConfiguredError, PlatformError
from platforms.telegram import TelegramPublisher


def _client_with_handler(handler):
    """Подменяем httpx.AsyncClient на версию с MockTransport (как в test_vk_client)."""
    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        kwargs.pop("timeout", None)
        orig_init(self, *args, **kwargs)

    return patched_init, orig_init


def _configured_settings(**over) -> Settings:
    """Settings с заданными telegram-кредами (conftest их не задаёт)."""
    base = dict(telegram_bot_token="bot-token", telegram_channel_id="@mychan")
    base.update(over)
    return Settings(**base)


@pytest.mark.asyncio
async def test_sendvideo_called_with_chat_id_and_parses_external_id(monkeypatch, tmp_path):
    """sendVideo вызывается с правильным chat_id; external_id = chat_id_message_id."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        url = str(request.url)
        assert "api.telegram.org" in url
        assert "/botbot-token/sendVideo" in url  # bot<token>/sendVideo
        # chat_id и caption передаются как multipart-поля.
        body = request.content
        assert b"@mychan" in body
        assert b"name=\"chat_id\"" in body
        assert b"name=\"video\"" in body
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    patched, _ = _client_with_handler(handler)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"fakevideo")

    pub = TelegramPublisher(_configured_settings())
    result = await pub.publish(
        video_path=str(vid), title="Hi", description="d", tags=["a"],
    )

    assert result["external_id"] == "@mychan_42"
    assert result["message_id"] == 42
    assert len(calls) == 1
    assert "/sendVideo" in str(calls[0].url)


@pytest.mark.asyncio
async def test_api_error_ok_false_raises_platform_error(monkeypatch, tmp_path):
    """ok=false → PlatformError с описанием от Telegram."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"},
        )

    patched, _ = _client_with_handler(handler)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x")

    pub = TelegramPublisher(_configured_settings())
    with pytest.raises(PlatformError) as exc:
        await pub.publish(video_path=str(vid), title="t", description="", tags=[])
    assert "chat not found" in str(exc.value)


@pytest.mark.asyncio
async def test_not_configured_raises(tmp_path):
    """Без кредов (дефолтный conftest-env) → NotConfiguredError, без сетевых вызовов."""
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x")

    # get_settings() из conftest не задаёт telegram_* → is_configured() == False.
    pub = TelegramPublisher(get_settings())
    assert pub.is_configured() is False
    with pytest.raises(NotConfiguredError):
        await pub.publish(video_path=str(vid), title="t")
