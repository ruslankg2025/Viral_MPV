"""Фундамент platform-абстракции: реестр, конфигурируемость, диспетчеризация."""
import pytest

from config import get_settings
from platforms import get_publisher, known_platforms
from platforms.base import NotConfiguredError
from platforms.pinterest import PinterestPublisher
from platforms.telegram import TelegramPublisher
from platforms.vk import VKClipsPublisher, VKVideoPublisher
from platforms.youtube import YouTubeShortsPublisher


def test_registry_has_all_phase23_platforms():
    assert set(known_platforms()) == {
        "vk_video", "vk_clips", "telegram", "youtube_shorts", "pinterest",
    }


def test_get_publisher_returns_right_class():
    s = get_settings()
    assert isinstance(get_publisher("vk_video", s), VKVideoPublisher)
    assert isinstance(get_publisher("vk_clips", s), VKClipsPublisher)
    assert isinstance(get_publisher("telegram", s), TelegramPublisher)
    assert isinstance(get_publisher("youtube_shorts", s), YouTubeShortsPublisher)
    assert isinstance(get_publisher("pinterest", s), PinterestPublisher)


def test_unknown_platform_raises():
    with pytest.raises(KeyError):
        get_publisher("tiktok", get_settings())


def test_vk_configured_via_token():
    # conftest задаёт VK_ACCESS_TOKEN=test-vk-token.
    assert get_publisher("vk_video", get_settings()).is_configured() is True


def test_stub_platforms_not_configured_without_creds():
    s = get_settings()
    for pid in ("telegram", "youtube_shorts", "pinterest"):
        assert get_publisher(pid, s).is_configured() is False


@pytest.mark.asyncio
async def test_unconfigured_platform_live_raises_not_configured():
    pub = get_publisher("telegram", get_settings())
    with pytest.raises(NotConfiguredError):
        await pub.publish(video_path="/m/v.mp4", title="t")
