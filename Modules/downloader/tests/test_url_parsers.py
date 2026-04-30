import pytest

from strategies.url_parsers import (
    UrlParseError,
    extract_external_id,
    extract_instagram_id,
    extract_tiktok_id,
    extract_youtube_shorts_id,
)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.instagram.com/reel/CABCdef-_123/", "CABCdef-_123"),
        ("https://instagram.com/reels/CABC/", "CABC"),
        ("https://www.instagram.com/p/CXYZ/", "CXYZ"),
        ("instagram.com/tv/CTV123/", "CTV123"),
    ],
)
def test_instagram(url, expected):
    assert extract_instagram_id(url) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.tiktok.com/@user/video/7012345678901234567",
         "7012345678901234567"),
        ("https://m.tiktok.com/@u/video/7000000000000000000",
         "7000000000000000000"),
        ("https://vm.tiktok.com/ZMxAbCdEf/", "ZMxAbCdEf"),
    ],
)
def test_tiktok(url, expected):
    assert extract_tiktok_id(url) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/shorts/abc123XYZ", "abc123XYZ"),
        ("https://youtube.com/shorts/abcDEF_-1", "abcDEF_-1"),
        ("https://youtu.be/abcXYZ123", "abcXYZ123"),
    ],
)
def test_youtube_shorts(url, expected):
    assert extract_youtube_shorts_id(url) == expected


def test_extract_dispatches_by_platform():
    assert extract_external_id("instagram", "https://instagram.com/reel/CABC/") == "CABC"
    assert extract_external_id(
        "tiktok", "https://www.tiktok.com/@u/video/123"
    ) == "123"
    assert extract_external_id(
        "youtube_shorts", "https://youtube.com/shorts/abc"
    ) == "abc"


def test_unknown_url_raises():
    with pytest.raises(UrlParseError):
        extract_instagram_id("https://example.com/foo")
    with pytest.raises(UrlParseError):
        extract_tiktok_id("https://example.com/foo")
    with pytest.raises(UrlParseError):
        extract_youtube_shorts_id("https://example.com/foo")
    with pytest.raises(UrlParseError):
        extract_external_id("vk", "https://vk.com/x")
