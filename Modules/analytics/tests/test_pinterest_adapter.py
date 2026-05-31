"""Тесты PinterestAdapter с mock HTTP (httpx.MockTransport).

API v5 Pin analytics → summary_metrics: IMPRESSION→reach, SAVE→saves,
PIN_CLICK→views, OUTBOUND_CLICK→click_through_to_external.
"""
import httpx
import pytest

from platforms.pinterest import PinterestAdapter, PinterestError, mock_metrics


def _client_factory(handler):
    transport = httpx.MockTransport(handler)

    class _Patched(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    return _Patched


@pytest.mark.asyncio
async def test_fetch_metrics_parses_summary(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert "/v5/pins/PIN123/analytics" in url
        assert request.headers.get("Authorization") == "Bearer pin-token"
        # диапазон дат обязателен
        assert request.url.params.get("start_date")
        assert request.url.params.get("end_date")
        return httpx.Response(200, json={
            "all": {
                "summary_metrics": {
                    "IMPRESSION": 9000,
                    "SAVE": 120,
                    "PIN_CLICK": 340,
                    "OUTBOUND_CLICK": 55,
                }
            }
        })

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    adapter = PinterestAdapter(token="pin-token")
    m = await adapter.fetch_metrics("PIN123")
    assert m.platform == "pinterest"
    assert m.reach == 9000          # IMPRESSION
    assert m.saves == 120           # SAVE
    assert m.views == 340           # PIN_CLICK
    assert m.click_through_to_external == 55  # OUTBOUND_CLICK
    assert m.likes == 0 and m.comments == 0 and m.shares == 0


@pytest.mark.asyncio
async def test_fetch_metrics_top_level_summary(monkeypatch):
    """Некоторые ответы кладут summary_metrics на верхний уровень."""
    def handler(request):
        return httpx.Response(200, json={"summary_metrics": {"IMPRESSION": 10, "SAVE": 2}})

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    adapter = PinterestAdapter(token="t")
    m = await adapter.fetch_metrics("PIN1")
    assert m.reach == 10 and m.saves == 2


@pytest.mark.asyncio
async def test_fetch_metrics_no_token_raises():
    adapter = PinterestAdapter(token=None)
    with pytest.raises(PinterestError):
        await adapter.fetch_metrics("PIN1")


@pytest.mark.asyncio
async def test_fetch_metrics_api_error(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"code": 2, "message": "invalid token"})

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    adapter = PinterestAdapter(token="bad")
    with pytest.raises(PinterestError):
        await adapter.fetch_metrics("PIN1")


@pytest.mark.asyncio
async def test_fetch_metrics_http_error(monkeypatch):
    def handler(request):
        return httpx.Response(401, text="unauthorized")

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory(handler))
    adapter = PinterestAdapter(token="bad")
    with pytest.raises(PinterestError):
        await adapter.fetch_metrics("PIN1")


def test_mock_metrics_deterministic():
    a = mock_metrics("PIN1")
    assert a == mock_metrics("PIN1")
    assert a["reach"] > 0 and a["saves"] > 0
    assert a["click_through_to_external"] >= 0
    assert a["likes"] == 0 and a["comments"] == 0
