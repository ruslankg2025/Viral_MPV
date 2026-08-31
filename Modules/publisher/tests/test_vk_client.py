"""vk_client live-путь с мок-транспортом httpx (без реальных HTTP)."""
import httpx
import pytest

from vk_client import VKClient, VKError


def _client_with_handler(handler):
    """Подменяем httpx.AsyncClient на версию с MockTransport."""
    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        kwargs.pop("timeout", None)
        orig_init(self, *args, **kwargs)

    return patched_init, orig_init


@pytest.mark.asyncio
async def test_publish_video_uploads_and_posts(monkeypatch, tmp_path):
    """video.save → upload → wall.post — все три шага через мок."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        url = str(request.url)
        if "video.save" in url:
            return httpx.Response(200, json={"response": {
                "upload_url": "https://upload.vk.example/video?x=1",
            }})
        if "upload.vk.example" in url:
            return httpx.Response(200, json={"owner_id": 12345, "video_id": 678})
        if "wall.post" in url:
            return httpx.Response(200, json={"response": {"post_id": 999}})
        return httpx.Response(404, json={"error": {"error_code": 1, "error_msg": "no route"}})

    patched, orig = _client_with_handler(handler)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"fakevideo")

    client = VKClient(access_token="t", api_version="5.199")
    result = await client.publish_video(video_path=str(vid), title="Hi", description="d", tags=["a"])

    assert result["external_id"] == "12345_678"
    assert result["post_id"] == 999
    assert any("video.save" in c for c in calls)
    assert any("wall.post" in c for c in calls)


@pytest.mark.asyncio
async def test_clip_degrades_to_video(monkeypatch, tmp_path):
    """vk_clips НЕ вызывает несуществующий метод — деградирует на video.save+wall.post."""
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/method/" in url:
            methods.append(url.split("/method/")[1].split("?")[0])
        if "video.save" in url:
            return httpx.Response(200, json={"response": {"upload_url": "https://up.example/u"}})
        if "up.example" in url:
            return httpx.Response(200, json={"owner_id": 1, "video_id": 2})
        if "wall.post" in url:
            return httpx.Response(200, json={"response": {"post_id": 3}})
        return httpx.Response(404, json={"error": {"error_code": 1, "error_msg": "x"}})

    patched, orig = _client_with_handler(handler)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x")

    client = VKClient(access_token="t")
    await client.publish_clip(video_path=str(vid), title="c", description="", tags=[])

    # Не должно быть обращений к shortVideo.* / clips-методам.
    assert all("shortVideo" not in m and "clips" not in m.lower() for m in methods)
    assert "video.save" in methods


@pytest.mark.asyncio
async def test_vk_api_error_raises(monkeypatch, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"error_code": 5, "error_msg": "auth fail"}})

    patched, orig = _client_with_handler(handler)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x")

    client = VKClient(access_token="t")
    with pytest.raises(VKError):
        await client.publish_video(video_path=str(vid), title="t", description="", tags=[])


def test_missing_token_raises():
    with pytest.raises(VKError):
        VKClient(access_token="")
