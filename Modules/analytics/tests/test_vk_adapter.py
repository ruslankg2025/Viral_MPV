"""Тесты VKAdapter с mock HTTP (httpx.MockTransport)."""
import httpx
import pytest

from platforms.vk import VKAdapter, VKError


def _client_factory(handler):
    """Подменяет httpx.AsyncClient на клиент с MockTransport."""
    transport = httpx.MockTransport(handler)

    class _Patched(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    return _Patched


@pytest.fixture(autouse=True)
def _no_real_http(monkeypatch):
    yield


@pytest.mark.asyncio
async def test_fetch_metrics_parses_vk_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "video.get" in str(request.url)
        assert request.url.params["videos"] == "-100_200"
        return httpx.Response(200, json={
            "response": {
                "count": 1,
                "items": [{
                    "id": 200,
                    "owner_id": -100,
                    "views": 12345,
                    "likes": {"count": 678},
                    "comments": {"count": 90},
                    "reposts": {"count": 12},
                }],
            }
        })

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    adapter = VKAdapter(token="fake-token")
    m = await adapter.fetch_metrics("-100_200")
    assert m.platform == "vk"
    assert m.external_id == "-100_200"
    assert m.views == 12345
    assert m.likes == 678
    assert m.comments == 90
    assert m.shares == 12
    # reach отсутствует → fallback на views
    assert m.reach == 12345


@pytest.mark.asyncio
async def test_fetch_metrics_no_token_raises():
    adapter = VKAdapter(token=None)
    with pytest.raises(VKError):
        await adapter.fetch_metrics("-1_2")


@pytest.mark.asyncio
async def test_fetch_metrics_vk_api_error(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"error": {"error_code": 5, "error_msg": "auth"}})

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    adapter = VKAdapter(token="bad")
    with pytest.raises(VKError):
        await adapter.fetch_metrics("-1_2")


@pytest.mark.asyncio
async def test_fetch_metrics_not_found(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"response": {"count": 0, "items": []}})

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    adapter = VKAdapter(token="ok")
    with pytest.raises(VKError):
        await adapter.fetch_metrics("-1_2")
