import pytest

from platforms.base import ChannelNotFound
from platforms.youtube import YouTubeSource, parse_channel_url, parse_iso_duration


# ---------------- ISO 8601 duration ----------------

class TestParseIsoDuration:
    def test_seconds_only(self):
        assert parse_iso_duration("PT45S") == 45

    def test_minutes_seconds(self):
        assert parse_iso_duration("PT12M34S") == 12 * 60 + 34

    def test_hours_minutes_seconds(self):
        assert parse_iso_duration("PT1H2M3S") == 3600 + 120 + 3

    def test_minutes_only(self):
        assert parse_iso_duration("PT5M") == 300

    def test_zero(self):
        assert parse_iso_duration("PT0S") == 0

    def test_none(self):
        assert parse_iso_duration(None) is None

    def test_empty(self):
        assert parse_iso_duration("") is None

    def test_garbage(self):
        assert parse_iso_duration("not-iso") is None


# ---------------- URL parser ----------------

class TestParseChannelUrl:
    def test_channel_id_format(self):
        r = parse_channel_url("https://www.youtube.com/channel/UCX6OQ3DkcsbYNE6H8uQQuVA")
        assert r == {"kind": "channel_id", "value": "UCX6OQ3DkcsbYNE6H8uQQuVA"}

    def test_handle_format(self):
        r = parse_channel_url("https://www.youtube.com/@MrBeast")
        assert r == {"kind": "handle", "value": "@MrBeast"}

    def test_handle_format_short(self):
        r = parse_channel_url("https://youtube.com/@pavel.pluzhnikov")
        assert r["kind"] == "handle"
        assert r["value"] == "@pavel.pluzhnikov"

    def test_c_format(self):
        r = parse_channel_url("https://www.youtube.com/c/MrBeast6000")
        assert r == {"kind": "c_name", "value": "MrBeast6000"}

    def test_user_format(self):
        r = parse_channel_url("https://www.youtube.com/user/PewDiePie")
        assert r == {"kind": "user_name", "value": "PewDiePie"}

    def test_video_url_rejected(self):
        with pytest.raises(ValueError, match="video url"):
            parse_channel_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_youtu_be_rejected(self):
        with pytest.raises(ValueError, match="video url"):
            parse_channel_url("https://youtu.be/dQw4w9WgXcQ")

    def test_garbage_rejected(self):
        with pytest.raises(ValueError):
            parse_channel_url("not a url at all")


# ---------------- Fake mode ----------------

@pytest.fixture
def fake_yt():
    return YouTubeSource(api_key="", fake_mode=True)


@pytest.mark.asyncio
async def test_fake_resolve_channel(fake_yt):
    info = await fake_yt.resolve_channel("https://www.youtube.com/@MrBeast")
    assert info.external_id.startswith("UC")
    assert info.channel_name == "Fake Channel"
    assert info.extra.get("uploads_playlist_id", "").startswith("UU")
    assert info.extra.get("resolved_from") == "handle"


@pytest.mark.asyncio
async def test_fake_resolve_rejects_video_url(fake_yt):
    with pytest.raises(ChannelNotFound):
        await fake_yt.resolve_channel("https://youtu.be/dQw4w9WgXcQ")


@pytest.mark.asyncio
async def test_fake_fetch_new_videos(fake_yt):
    info = await fake_yt.resolve_channel("https://www.youtube.com/@MrBeast")
    videos = await fake_yt.fetch_new_videos(info, known_external_ids=set())
    assert len(videos) == 3
    assert all(v.external_id.startswith("VIDEO_") for v in videos)
    assert all(v.url.startswith("https://www.youtube.com/watch") for v in videos)
    assert videos[0].title == "Fake Video 1"


@pytest.mark.asyncio
async def test_fake_fetch_new_videos_skips_known(fake_yt):
    info = await fake_yt.resolve_channel("https://www.youtube.com/@MrBeast")
    videos = await fake_yt.fetch_new_videos(info, known_external_ids={"VIDEO_001", "VIDEO_002"})
    assert len(videos) == 1
    assert videos[0].external_id == "VIDEO_003"


@pytest.mark.asyncio
async def test_fake_fetch_metrics(fake_yt):
    snaps = await fake_yt.fetch_metrics(["VIDEO_001", "VIDEO_002", "VIDEO_003"])
    assert len(snaps) == 3
    s1 = next(s for s in snaps if s.external_id == "VIDEO_001")
    assert s1.views == 45000
    assert s1.likes == 3200
    assert s1.comments == 180
    # contentDetails.duration из fixture
    assert s1.duration_sec == 12 * 60 + 34
    assert s1.is_short is False
    # VIDEO_002: PT45S — это Short
    s2 = next(s for s in snaps if s.external_id == "VIDEO_002")
    assert s2.duration_sec == 45
    assert s2.is_short is True


@pytest.mark.asyncio
async def test_fake_fetch_metrics_empty(fake_yt):
    snaps = await fake_yt.fetch_metrics([])
    assert snaps == []


@pytest.mark.asyncio
async def test_fake_fetch_metrics_filters_unknown(fake_yt):
    snaps = await fake_yt.fetch_metrics(["VIDEO_999"])
    assert snaps == []
