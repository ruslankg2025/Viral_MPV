"""Тесты YouTubeAdapter с mock HTTP (httpx.MockTransport).

Data API v3 videos.list?part=statistics → views/likes/comments.
reach=views (публичного reach нет), shares/saves=0 (только Analytics API).
"""
import httpx
import pytest

from platforms.youtube import YouTubeAdapter, YouTubeError, mock_metrics


def _client_factory(handler):
    transport = httpx.MockTransport(handler)

    class _Patched(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    return _Patched


@pytest.mark.asyncio
async def test_fetch_metrics_parses_statistics(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "youtube/v3/videos" in str(request.url)
        assert request.url.params["id"] == "dQw4w9WgXcQ"
        assert request.url.params["part"] == "statistics"
        assert request.url.params["key"] == "api-key"
        return httpx.Response(200, json={
            "items": [{
                "id": "dQw4w9WgXcQ",
                "statistics": {
                    "viewCount": "100000",
                    "likeCount": "5000",
                    "commentCount": "321",
                },
            }],
        })

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    adapter = YouTubeAdapter(token="api-key")
    m = await adapter.fetch_metrics("dQw4w9WgXcQ")
    assert m.platform == "youtube"
    assert m.views == 100000
    assert m.reach == 100000  # fallback на views
    assert m.likes == 5000
    assert m.comments == 321
    assert m.shares == 0 and m.saves == 0


@pytest.mark.asyncio
async def test_fetch_metrics_hidden_like_count(monkeypatch):
    """likeCount может быть скрыт автором → отсутствует → 0."""
    def handler(request):
        return httpx.Response(200, json={"items": [{"statistics": {"viewCount": "42"}}]})

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    adapter = YouTubeAdapter(token="k")
    m = await adapter.fetch_metrics("vid")
    assert m.views == 42
    assert m.likes == 0 and m.comments == 0


@pytest.mark.asyncio
async def test_fetch_metrics_no_token_raises():
    adapter = YouTubeAdapter(token=None)
    with pytest.raises(YouTubeError):
        await adapter.fetch_metrics("vid")


@pytest.mark.asyncio
async def test_fetch_metrics_not_found(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"items": []})

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    adapter = YouTubeAdapter(token="k")
    with pytest.raises(YouTubeError):
        await adapter.fetch_metrics("missing")


@pytest.mark.asyncio
async def test_fetch_metrics_api_error(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"error": {"code": 403, "message": "quota"}})

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    adapter = YouTubeAdapter(token="k")
    with pytest.raises(YouTubeError):
        await adapter.fetch_metrics("vid")


def test_mock_metrics_deterministic():
    a = mock_metrics("vid1")
    assert a == mock_metrics("vid1")
    assert a["views"] > 0 and a["reach"] == a["views"]
    assert a["shares"] == 0 and a["saves"] == 0  # только Analytics API
