"""Tests for TikTokSource (Apify adapter)."""
import pytest

from platforms.base import ChannelInfo, ChannelNotFound
from platforms.tiktok import TikTokSource, parse_tiktok_url


class TestParseTikTokUrl:
    def test_profile_full_url(self):
        assert parse_tiktok_url("https://www.tiktok.com/@charli") == "charli"

    def test_profile_trailing_slash(self):
        assert parse_tiktok_url("https://www.tiktok.com/@charli/") == "charli"

    def test_extract_handle_from_video_url(self):
        assert (
            parse_tiktok_url("https://www.tiktok.com/@charli/video/123456789")
            == "charli"
        )

    def test_dots_and_underscores(self):
        assert parse_tiktok_url("https://tiktok.com/@cool.user_1") == "cool.user_1"

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            parse_tiktok_url("https://instagram.com/charli")


@pytest.fixture
def tt():
    return TikTokSource(fake_mode=True, apify_token="", results_limit=10)


@pytest.mark.asyncio
async def test_resolve_channel(tt):
    ch = await tt.resolve_channel("https://www.tiktok.com/@testtt")
    assert ch.external_id == "testtt"


@pytest.mark.asyncio
async def test_resolve_channel_bad_url_raises(tt):
    with pytest.raises(ChannelNotFound):
        await tt.resolve_channel("https://instagram.com/user")


@pytest.mark.asyncio
async def test_fetch_new_videos_from_fixture(tt):
    ch = ChannelInfo(external_id="testtt", channel_name="", extra={})
    videos = await tt.fetch_new_videos(ch, known_external_ids=set())
    assert len(videos) == 3
    # channel_name обновлён из nickName
    assert ch.channel_name == "Test TT"


@pytest.mark.asyncio
async def test_is_short_detection(tt):
    ch = ChannelInfo(external_id="testtt", channel_name="", extra={})
    videos = await tt.fetch_new_videos(ch, known_external_ids=set())
    by_id = {v.external_id: v for v in videos}
    # fake-fixture id суффиксуется handle'ом (_testtt)
    # 22s → short
    assert by_id["7300000000000000001_testtt"].is_short is True
    # 47s → short
    assert by_id["7300000000000000002_testtt"].is_short is True
    # 180s → NOT short
    assert by_id["7300000000000000003_testtt"].is_short is False


@pytest.mark.asyncio
async def test_fetch_metrics_from_cache(tt):
    ch = ChannelInfo(external_id="testtt", channel_name="", extra={})
    await tt.fetch_new_videos(ch, known_external_ids=set())
    metrics = await tt.fetch_metrics(["7300000000000000001_testtt"])
    assert len(metrics) == 1
    m = metrics[0]
    assert m.views == 325000  # playCount
    assert m.likes == 18700  # diggCount
    assert m.comments == 430


@pytest.mark.asyncio
async def test_skips_known(tt):
    ch = ChannelInfo(external_id="testtt", channel_name="", extra={})
    videos = await tt.fetch_new_videos(
        ch, known_external_ids={"7300000000000000001_testtt"}
    )
    assert len(videos) == 2
