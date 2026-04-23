"""Tests for InstagramSource (Apify adapter)."""
import pytest

from platforms.instagram import InstagramSource, parse_instagram_url
from platforms.base import ChannelInfo, ChannelNotFound


# ---------------- URL parser ----------------

class TestParseInstagramUrl:
    def test_profile_full_url(self):
        assert parse_instagram_url("https://www.instagram.com/nasa/") == "nasa"

    def test_profile_no_www(self):
        assert parse_instagram_url("https://instagram.com/nasa") == "nasa"

    def test_profile_with_trailing_slash(self):
        assert parse_instagram_url("https://www.instagram.com/nasa/") == "nasa"

    def test_dots_and_underscores(self):
        assert parse_instagram_url("https://instagram.com/cool.user_01") == "cool.user_01"

    def test_rejects_post_url(self):
        with pytest.raises(ValueError, match="post url"):
            parse_instagram_url("https://instagram.com/p/ABC123/")

    def test_rejects_reel_url(self):
        with pytest.raises(ValueError, match="post url"):
            parse_instagram_url("https://instagram.com/reel/XYZ/")

    def test_rejects_explore(self):
        with pytest.raises(ValueError, match="post url"):
            parse_instagram_url("https://instagram.com/explore/tags/viral/")

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            parse_instagram_url("https://tiktok.com/@user")


# ---------------- Fake mode (MetricsSource API) ----------------

@pytest.fixture
def ig():
    return InstagramSource(fake_mode=True, apify_token="", results_limit=10)


@pytest.mark.asyncio
async def test_resolve_channel_returns_handle(ig):
    ch = await ig.resolve_channel("https://www.instagram.com/testuser/")
    assert ch.external_id == "testuser"


@pytest.mark.asyncio
async def test_resolve_channel_bad_url_raises(ig):
    with pytest.raises(ChannelNotFound):
        await ig.resolve_channel("https://instagram.com/p/ABC/")


@pytest.mark.asyncio
async def test_fetch_new_videos_populates_cache(ig):
    ch = ChannelInfo(external_id="testuser", channel_name="testuser", extra={})
    videos = await ig.fetch_new_videos(ch, known_external_ids=set())
    # Fixture имеет 3 post'а
    assert len(videos) == 3
    # channel_name обновлён из ownerFullName
    assert ch.channel_name == "Test User"


@pytest.mark.asyncio
async def test_fetch_new_videos_skips_known(ig):
    # В fake-режиме shortCode суффиксуется handle'ом для per-author уникальности.
    ch = ChannelInfo(external_id="testuser", channel_name="", extra={})
    videos = await ig.fetch_new_videos(ch, known_external_ids={"CfakeShort001_testuser"})
    assert len(videos) == 2
    assert all(v.external_id != "CfakeShort001_testuser" for v in videos)


@pytest.mark.asyncio
async def test_is_short_detection(ig):
    ch = ChannelInfo(external_id="testuser", channel_name="", extra={})
    videos = await ig.fetch_new_videos(ch, known_external_ids=set())
    by_id = {v.external_id: v for v in videos}
    # 18.5s → short
    assert by_id["CfakeShort001_testuser"].is_short is True
    assert by_id["CfakeShort001_testuser"].duration_sec == 18
    # 28.0s → short
    assert by_id["CfakeShort002_testuser"].is_short is True
    # Image → нет duration → не short
    assert by_id["CfakeShort003_testuser"].is_short is False


@pytest.mark.asyncio
async def test_fetch_metrics_reads_from_cache(ig):
    ch = ChannelInfo(external_id="testuser", channel_name="", extra={})
    await ig.fetch_new_videos(ch, known_external_ids=set())
    metrics = await ig.fetch_metrics(["CfakeShort001_testuser", "CfakeShort002_testuser"])
    assert len(metrics) == 2
    by_id = {m.external_id: m for m in metrics}
    assert by_id["CfakeShort001_testuser"].views == 152000
    assert by_id["CfakeShort001_testuser"].likes == 9800
    assert by_id["CfakeShort001_testuser"].comments == 420


@pytest.mark.asyncio
async def test_fetch_metrics_before_new_returns_empty(ig):
    # Кеш пуст — метрик нет
    metrics = await ig.fetch_metrics(["any"])
    assert metrics == []
