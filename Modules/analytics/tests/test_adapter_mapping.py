"""Тесты согласования publisher-платформ с analytics-адаптерами.

Ключевое требование: fetcher должен матчить publication.platform
(vk_video/vk_clips/telegram/youtube_shorts/pinterest) на правильный адаптер,
включая youtube_shorts → youtube. build_adapters регистрирует все адаптеры.
"""
import httpx
import pytest

from config import Settings
from fetcher import (
    _adapter_key,
    _resolve_adapter,
    build_adapters,
    fetch_one,
)


def _settings():
    return Settings(analytics_use_mock=False, enable_fetcher=False)


def test_build_adapters_registers_all_platforms(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("PINTEREST_TOKEN", raising=False)
    adapters = build_adapters(_settings(), vk_token="vk")
    assert set(adapters) == {"vk", "telegram", "youtube", "pinterest", "zen"}
    assert adapters["vk"].platform_name == "vk"
    assert adapters["youtube"].platform_name == "youtube"


@pytest.mark.parametrize("publisher_platform,adapter_key", [
    ("vk_video", "vk"),
    ("vk_clips", "vk"),
    ("telegram", "telegram"),
    ("youtube_shorts", "youtube"),  # ключевое: shorts → youtube
    ("pinterest", "pinterest"),
    ("zen", "zen"),
])
def test_adapter_key_and_resolution(publisher_platform, adapter_key, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("PINTEREST_TOKEN", raising=False)
    assert _adapter_key(publisher_platform) == adapter_key
    adapters = build_adapters(_settings(), vk_token="vk")
    resolved = _resolve_adapter(publisher_platform, adapters)
    assert resolved is adapters[adapter_key]


@pytest.mark.asyncio
async def test_fetch_one_youtube_shorts_real_path(monkeypatch):
    """youtube_shorts публикация → youtube-адаптер → строка с platform='youtube'."""
    def handler(request):
        if "youtube/v3/videos" in str(request.url):
            return httpx.Response(200, json={"items": [{"statistics": {
                "viewCount": "777", "likeCount": "12", "commentCount": "3",
            }}]})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    class _Patched(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", _Patched)
    monkeypatch.setenv("YOUTUBE_API_KEY", "yt-key")
    adapters = build_adapters(_settings(), vk_token="vk")
    pub = {"id": "pY", "platform": "youtube_shorts", "external_id": "vidX"}
    row = await fetch_one(pub, adapters, allow_mock=False)
    assert row is not None
    assert row["platform"] == "youtube"  # нормализованный ключ
    assert row["publication_id"] == "pY"
    assert row["views"] == 777
    assert row["likes"] == 12


@pytest.mark.asyncio
async def test_fetch_one_telegram_mock_branch_without_token(monkeypatch):
    """Нет TELEGRAM_BOT_TOKEN + allow_mock → mock-метрики telegram."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    adapters = build_adapters(_settings(), vk_token="vk")
    pub = {"id": "pT", "platform": "telegram", "external_id": "-100_5"}
    row = await fetch_one(pub, adapters, allow_mock=True)
    assert row is not None
    assert row["platform"] == "telegram"
    assert row["reach"] > 0  # mock непустой


@pytest.mark.asyncio
async def test_fetch_one_pinterest_mock_branch_without_token(monkeypatch):
    monkeypatch.delenv("PINTEREST_TOKEN", raising=False)
    adapters = build_adapters(_settings(), vk_token="vk")
    pub = {"id": "pP", "platform": "pinterest", "external_id": "PIN9"}
    row = await fetch_one(pub, adapters, allow_mock=True)
    assert row is not None
    assert row["platform"] == "pinterest"
    assert row["saves"] >= 0 and row["reach"] > 0


@pytest.mark.asyncio
async def test_fetch_one_zen_always_returns_metrics(monkeypatch):
    """Дзен без токена и без allow_mock — адаптер сам отдаёт mock (не падает)."""
    adapters = build_adapters(_settings(), vk_token="vk")
    pub = {"id": "pZ", "platform": "zen", "external_id": "zen_7"}
    row = await fetch_one(pub, adapters, allow_mock=False)
    assert row is not None
    assert row["platform"] == "zen"
    assert row["views"] > 0
