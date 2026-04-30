"""Извлечение external_id (shortcode/id) из URL платформы.

External_id используется для:
- Имени файла: `{prefix}_{external_id}.mp4`
- cache_key processor: `{platform}:{external_id}`
- Дедупа на стороне monitor.
"""
import re

# Instagram: /reel/CABCdef123/, /p/CABCdef123/, /tv/...
_IG_RE = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)

# TikTok: /@username/video/1234567890123456789, vm.tiktok.com/ZMxxx (короткие)
_TIKTOK_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.|vm\.)?tiktok\.com/(?:@[^/]+/video/(\d+)|([A-Za-z0-9]+))",
    re.IGNORECASE,
)

# YouTube Shorts: /shorts/abcXYZ. Требуем минимум 3 символа (реальные ID — 11),
# но не быть строгими ради устойчивости тестов и неожиданных форматов.
_YT_SHORTS_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/shorts/|youtu\.be/)([A-Za-z0-9_-]{3,})",
    re.IGNORECASE,
)


class UrlParseError(ValueError):
    pass


def extract_instagram_id(url: str) -> str:
    m = _IG_RE.search(url)
    if not m:
        raise UrlParseError(f"unrecognized instagram url: {url}")
    return m.group(1)


def extract_tiktok_id(url: str) -> str:
    m = _TIKTOK_RE.search(url)
    if not m:
        raise UrlParseError(f"unrecognized tiktok url: {url}")
    return m.group(1) or m.group(2)


def extract_youtube_shorts_id(url: str) -> str:
    m = _YT_SHORTS_RE.search(url)
    if not m:
        raise UrlParseError(f"unrecognized youtube shorts url: {url}")
    return m.group(1)


PLATFORM_PREFIX = {
    "instagram": "ig",
    "tiktok": "tt",
    "youtube_shorts": "yt",
}


def extract_external_id(platform: str, url: str) -> str:
    if platform == "instagram":
        return extract_instagram_id(url)
    if platform == "tiktok":
        return extract_tiktok_id(url)
    if platform == "youtube_shorts":
        return extract_youtube_shorts_id(url)
    raise UrlParseError(f"unsupported platform: {platform}")
