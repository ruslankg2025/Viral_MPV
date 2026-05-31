"""Pinterest live-путь с мок-транспортом httpx (без реальных HTTP к api.pinterest.com).

Покрываем:
  1. happy-path video pin: oauth → media → upload → media-status → pins.create,
     external_id парсится из {"id": ...}.
  2. ошибка API (4xx на pins.create) → PlatformError.
  3. без кредов → NotConfiguredError (guard).
  4. http_proxy задан → httpx.AsyncClient конструируется с proxy=<url>.
"""
import httpx
import pytest

from config import Settings
from platforms.base import NotConfiguredError, PlatformError
from platforms.pinterest import PinterestPublisher


def _settings(**over):
    """Settings с заданными pinterest-кредами (минуя env)."""
    base = dict(
        pinterest_app_id="app-id",
        pinterest_app_secret="app-secret",
        pinterest_refresh_token="refresh-tok",
        pinterest_board_id="board-123",
        http_proxy="",
        # быстрый поллинг — без задержки в тестах не нужен, но MEDIA_POLL_DELAY
        # обходим тем, что media сразу 'succeeded' (см. handler).
    )
    base.update(over)
    return Settings(**base)


def _patch_async_client(monkeypatch, handler, *, captured_kwargs=None):
    """Подменяем httpx.AsyncClient.__init__: инжектим MockTransport.

    Если передан captured_kwargs (list) — сохраняем туда исходные kwargs
    конструктора ДО подмены (чтобы проверить proxy=...).
    transport несовместим с proxy в httpx, поэтому proxy из kwargs убираем,
    предварительно зафиксировав факт его передачи.
    """
    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        if captured_kwargs is not None:
            captured_kwargs.append(dict(kwargs))
        kwargs.pop("timeout", None)
        kwargs.pop("proxy", None)  # MockTransport маршрутизирует сам
        kwargs["transport"] = transport
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


def _happy_handler(calls):
    """Маршрутизатор happy-path: oauth → media → upload → status → pins."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url.endswith("/v5/oauth/token"):
            return httpx.Response(200, json={"access_token": "ACCESS-TOKEN", "token_type": "bearer"})
        if url.endswith("/v5/media"):
            # register media
            return httpx.Response(201, json={
                "media_id": "media-777",
                "upload_url": "https://pinterest-media-upload.example/s3",
                "upload_parameters": {"key": "val", "x-amz-signature": "sig"},
            })
        if "pinterest-media-upload.example" in url:
            # S3-заливка: 204 без тела
            return httpx.Response(204)
        if "/v5/media/media-777" in url:
            # media status — сразу succeeded
            return httpx.Response(200, json={"media_id": "media-777", "status": "succeeded"})
        if url.endswith("/v5/pins"):
            return httpx.Response(201, json={"id": "pin-999", "board_id": "board-123"})
        return httpx.Response(404, json={"code": 0, "message": f"no route: {url}"})

    return handler


@pytest.mark.asyncio
async def test_video_pin_happy_path(monkeypatch, tmp_path):
    calls = []
    _patch_async_client(monkeypatch, _happy_handler(calls))

    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"fakevideo")

    pub = PinterestPublisher(_settings())
    result = await pub.publish(
        video_path=str(vid), title="Hello", description="desc", tags=["tag"]
    )

    assert result == {"external_id": "pin-999"}
    # Прошли все 5 шагов флоу.
    assert any(c.endswith("/v5/oauth/token") for c in calls)
    assert any(c.endswith("/v5/media") for c in calls)
    assert any("pinterest-media-upload.example" in c for c in calls)
    assert any("/v5/media/media-777" in c for c in calls)
    assert any(c.endswith("/v5/pins") for c in calls)


@pytest.mark.asyncio
async def test_api_error_raises_platform_error(monkeypatch, tmp_path):
    """4xx на pins.create → PlatformError (нет image-fallback URL)."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/v5/oauth/token"):
            return httpx.Response(200, json={"access_token": "ACCESS-TOKEN"})
        if url.endswith("/v5/media"):
            return httpx.Response(201, json={
                "media_id": "m1",
                "upload_url": "https://up.example/s3",
                "upload_parameters": {},
            })
        if "up.example" in url:
            return httpx.Response(204)
        if "/v5/media/m1" in url:
            return httpx.Response(200, json={"status": "succeeded"})
        if url.endswith("/v5/pins"):
            return httpx.Response(400, json={"code": 7, "message": "invalid board"})
        return httpx.Response(404, json={"message": "nope"})

    _patch_async_client(monkeypatch, handler)

    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x")

    pub = PinterestPublisher(_settings())
    with pytest.raises(PlatformError) as ei:
        await pub.publish(video_path=str(vid), title="t", description="", tags=[])
    # Видео-флоу упал, image-fallback недоступен (URL нет) → внятная ошибка.
    assert "no_image_fallback" in str(ei.value) or "create_pin_error" in str(ei.value)


@pytest.mark.asyncio
async def test_oauth_error_raises_platform_error(monkeypatch, tmp_path):
    """4xx на oauth (до видео-флоу) → PlatformError напрямую, без fallback."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": 1, "message": "bad refresh token"})

    _patch_async_client(monkeypatch, handler)
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x")

    pub = PinterestPublisher(_settings())
    with pytest.raises(PlatformError):
        await pub.publish(video_path=str(vid), title="t")


@pytest.mark.asyncio
async def test_not_configured_raises(monkeypatch):
    """Без кредов publish() кидает NotConfiguredError (guard сохранён)."""
    pub = PinterestPublisher(_settings(pinterest_app_id="", pinterest_board_id=""))
    assert pub.is_configured() is False
    with pytest.raises(NotConfiguredError):
        await pub.publish(video_path="/whatever.mp4", title="t")


@pytest.mark.asyncio
async def test_proxy_passed_to_httpx_client(monkeypatch, tmp_path):
    """Если http_proxy задан — httpx.AsyncClient конструируется с proxy=<url>."""
    captured = []
    _patch_async_client(monkeypatch, _happy_handler([]), captured_kwargs=captured)

    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x")

    proxy_url = "http://user:pass@residential.proxy.example:8080"
    pub = PinterestPublisher(_settings(http_proxy=proxy_url))
    await pub.publish(video_path=str(vid), title="t", description="d", tags=[])

    # Конструктор клиента получил proxy=<наш url>.
    assert captured, "AsyncClient не конструировался"
    assert any(kw.get("proxy") == proxy_url for kw in captured)


@pytest.mark.asyncio
async def test_no_proxy_no_proxy_kwarg(monkeypatch, tmp_path):
    """Если http_proxy пуст — proxy в конструктор НЕ передаётся."""
    captured = []
    _patch_async_client(monkeypatch, _happy_handler([]), captured_kwargs=captured)

    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x")

    pub = PinterestPublisher(_settings(http_proxy=""))
    await pub.publish(video_path=str(vid), title="t")

    assert captured
    assert all("proxy" not in kw for kw in captured)
