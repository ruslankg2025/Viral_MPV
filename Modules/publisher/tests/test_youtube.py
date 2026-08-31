"""YouTube Shorts live-путь через мок-транспорт httpx (без реальных HTTP).

Покрываем: (1) refresh → resumable insert happy-path (external_id парсится из
видимого videoId, заголовок получает суффикс #Shorts, privacyStatus=private);
(2) ошибка токена/квоты → PlatformError; (3) без кредов → NotConfiguredError.
"""
import httpx
import pytest

from config import Settings
from platforms.base import NotConfiguredError, PlatformError
from platforms.youtube import YouTubeShortsPublisher


def _patch_transport(monkeypatch, handler):
    """Подменяем httpx.AsyncClient на версию с MockTransport (как в test_vk_client)."""
    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        kwargs.pop("timeout", None)
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


def _configured_settings() -> Settings:
    return Settings(
        youtube_client_id="cid",
        youtube_client_secret="secret",
        youtube_refresh_token="rtok",
    )


@pytest.mark.asyncio
async def test_publish_happy_path(monkeypatch, tmp_path):
    """oauth refresh → resumable init (Location) → PUT байтов → videoId."""
    calls = []
    captured_metadata = {}
    UPLOAD_SESSION = "https://upload.googleapis.com/session/abc123"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "oauth2.googleapis.com/token" in url:
            # grant_type=refresh_token в форме.
            assert b"grant_type=refresh_token" in request.content
            return httpx.Response(200, json={"access_token": "ya29.fake", "expires_in": 3599})
        if "uploadType=resumable" in url:
            # Шаг 1: метаданные + Authorization. Ответ — пустой + Location.
            assert request.headers.get("Authorization") == "Bearer ya29.fake"
            import json as _json
            captured_metadata.update(_json.loads(request.content))
            return httpx.Response(200, headers={"Location": UPLOAD_SESSION})
        if url == UPLOAD_SESSION:
            # Шаг 2: PUT байтов → финальный resource videos.insert.
            assert request.method == "PUT"
            assert request.content == b"fakevideobytes"
            return httpx.Response(200, json={"id": "VID12345", "kind": "youtube#video"})
        return httpx.Response(404, json={"error": {"message": "no route"}})

    _patch_transport(monkeypatch, handler)

    vid = tmp_path / "short.mp4"
    vid.write_bytes(b"fakevideobytes")

    pub = YouTubeShortsPublisher(_configured_settings())
    result = await pub.publish(
        video_path=str(vid),
        title="My viral clip",
        description="desc",
        tags=["#fun", "viral"],
    )

    assert result["external_id"] == "VID12345"
    # #Shorts добавлен к заголовку.
    assert captured_metadata["snippet"]["title"] == "My viral clip #Shorts"
    # tags нормализованы (без ведущего #).
    assert captured_metadata["snippet"]["tags"] == ["fun", "viral"]
    # Приватность по умолчанию.
    assert captured_metadata["status"]["privacyStatus"] == "private"
    # Все три шага совершены по порядку.
    assert any("oauth2" in c for c in calls)
    assert any("uploadType=resumable" in c for c in calls)
    assert UPLOAD_SESSION in calls


@pytest.mark.asyncio
async def test_quota_exceeded_raises_platform_error(monkeypatch, tmp_path):
    """videos.insert вернул 403 quotaExceeded → PlatformError."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            return httpx.Response(200, json={"access_token": "ya29.fake"})
        if "uploadType=resumable" in url:
            return httpx.Response(403, json={
                "error": {"code": 403, "message": "The request cannot be completed because you have exceeded your quota.",
                          "errors": [{"reason": "quotaExceeded"}]},
            })
        return httpx.Response(404, json={"error": {"message": "no route"}})

    _patch_transport(monkeypatch, handler)

    vid = tmp_path / "short.mp4"
    vid.write_bytes(b"x")

    pub = YouTubeShortsPublisher(_configured_settings())
    with pytest.raises(PlatformError) as exc:
        await pub.publish(video_path=str(vid), title="t")
    assert "quota" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_bad_refresh_token_raises_platform_error(monkeypatch, tmp_path):
    """OAuth refresh вернул invalid_grant → PlatformError (не пропускаем дальше)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com/token" in str(request.url):
            return httpx.Response(400, json={
                "error": "invalid_grant",
                "error_description": "Token has been expired or revoked.",
            })
        return httpx.Response(404, json={"error": {"message": "should not reach"}})

    _patch_transport(monkeypatch, handler)

    vid = tmp_path / "short.mp4"
    vid.write_bytes(b"x")

    pub = YouTubeShortsPublisher(_configured_settings())
    with pytest.raises(PlatformError):
        await pub.publish(video_path=str(vid), title="t")


@pytest.mark.asyncio
async def test_not_configured_raises(tmp_path):
    """Без креденшелов — NotConfiguredError, никаких HTTP."""
    vid = tmp_path / "short.mp4"
    vid.write_bytes(b"x")

    pub = YouTubeShortsPublisher(Settings(
        youtube_client_id="", youtube_client_secret="", youtube_refresh_token="",
    ))
    assert pub.is_configured() is False
    with pytest.raises(NotConfiguredError):
        await pub.publish(video_path=str(vid), title="t")
