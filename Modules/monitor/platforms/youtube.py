"""
YouTube Data API v3 adapter.

URL formats supported by resolve_channel_id:
- https://www.youtube.com/channel/UCxxx        → channel_id напрямую, 0 units
- https://www.youtube.com/@handle              → channels.list?forHandle=, 1 unit
- https://www.youtube.com/c/customname         → channels.list?forUsername= (legacy), 1 unit
- https://www.youtube.com/user/legacy          → channels.list?forUsername=, 1 unit
- https://www.youtube.com/UC...                → извлечение напрямую

FAKE MODE: если settings.effective_fake_mode == True, возвращает данные из fixtures/*.json
без обращения к сети. Используется в тестах и при отсутствии YOUTUBE_API_KEY.
"""
import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx

# ISO 8601 duration → seconds (PT#H#M#S)
_DURATION_RE = re.compile(
    r"^PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?$"
)


def parse_iso_duration(value: str | None) -> int | None:
    if not value:
        return None
    m = _DURATION_RE.match(value)
    if not m:
        return None
    h = int(m.group("h") or 0)
    mi = int(m.group("m") or 0)
    s = int(m.group("s") or 0)
    total = h * 3600 + mi * 60 + s
    return total if total > 0 else 0

from platforms.base import (
    ChannelInfo,
    ChannelNotFound,
    MetricsSnapshot,
    PlatformError,
    QuotaExhausted,
    TransientError,
    VideoMeta,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


# ------------------------------------------------------------------ #
# URL parser
# ------------------------------------------------------------------ #

_CHANNEL_ID_RE = re.compile(r"/channel/(UC[\w-]{20,24})")
_HANDLE_RE = re.compile(r"/(@[\w\.-]+)")
_C_NAME_RE = re.compile(r"/c/([\w\.-]+)")
_USER_NAME_RE = re.compile(r"/user/([\w\.-]+)")
_VIDEO_RE = re.compile(r"(?:youtu\.be/|watch\?v=)([\w-]{11})")


def parse_channel_url(url: str) -> dict[str, str]:
    """Извлекает тип и значение из URL.
    Возвращает {kind, value}, где kind:
    - 'channel_id' → value = 'UC...'
    - 'handle' → value = '@name'
    - 'c_name' → value = 'name'
    - 'user_name' → value = 'name'

    Raises ValueError для видео-URL и прочих невалидных форматов.
    """
    url = url.strip()
    if _VIDEO_RE.search(url):
        raise ValueError("this is a video url, not a channel url")
    m = _CHANNEL_ID_RE.search(url)
    if m:
        return {"kind": "channel_id", "value": m.group(1)}
    m = _HANDLE_RE.search(url)
    if m:
        return {"kind": "handle", "value": m.group(1)}
    m = _C_NAME_RE.search(url)
    if m:
        return {"kind": "c_name", "value": m.group(1)}
    m = _USER_NAME_RE.search(url)
    if m:
        return {"kind": "user_name", "value": m.group(1)}
    raise ValueError(f"unrecognized youtube channel url: {url}")


# ------------------------------------------------------------------ #
# Fixture loading (fake mode)
# ------------------------------------------------------------------ #

def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ #
# YouTubeSource
# ------------------------------------------------------------------ #

class YouTubeSource:
    name = "youtube"

    def __init__(
        self,
        *,
        api_key: str = "",
        fake_mode: bool = False,
        quota_counter=None,  # callable(units: int) -> int
    ):
        self.api_key = api_key
        self.fake_mode = fake_mode
        self._quota_counter = quota_counter

    def _count_quota(self, units: int) -> None:
        if self._quota_counter is not None:
            try:
                self._quota_counter(units)
            except Exception:
                pass  # quota counter не критичен

    async def _request(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        params: dict[str, Any],
        *,
        cost: int = 1,
        max_retries: int = 3,
    ) -> dict:
        """HTTP с retry на transient ошибках."""
        params = {**params, "key": self.api_key}
        backoff = [1, 4, 16]
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                r = await client.get(f"{YOUTUBE_API_BASE}/{endpoint}", params=params, timeout=10.0)
                if r.status_code == 200:
                    self._count_quota(cost)
                    return r.json()
                if r.status_code == 403:
                    body = r.text
                    if "quotaExceeded" in body or "dailyLimitExceeded" in body:
                        raise QuotaExhausted("youtube_quota_exhausted")
                    raise PlatformError(f"forbidden: {body[:200]}")
                if r.status_code == 404:
                    raise ChannelNotFound(f"404 from youtube: {endpoint}")
                if 500 <= r.status_code < 600:
                    last_exc = TransientError(f"{r.status_code}: {r.text[:200]}")
                else:
                    raise PlatformError(f"{r.status_code}: {r.text[:200]}")
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as e:
                last_exc = TransientError(f"{type(e).__name__}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(backoff[attempt])
        if last_exc:
            raise last_exc
        raise PlatformError("unknown error")

    # ------------------------------------------------------------------ #
    # Fake mode
    # ------------------------------------------------------------------ #

    async def _fake_resolve(self, url: str) -> ChannelInfo:
        try:
            parsed = parse_channel_url(url)
        except ValueError as e:
            raise ChannelNotFound(str(e))
        fx = _load_fixture("youtube_channel_response.json")
        items = fx.get("items", [])
        if not items:
            raise ChannelNotFound("fixture empty")
        item = items[0]
        return ChannelInfo(
            external_id=item["id"],
            channel_name=item["snippet"]["title"],
            extra={
                "uploads_playlist_id": item["contentDetails"]["relatedPlaylists"]["uploads"],
                "resolved_from": parsed["kind"],
            },
        )

    async def _fake_fetch_new(
        self, channel: ChannelInfo, known: set[str]
    ) -> list[VideoMeta]:
        fx = _load_fixture("youtube_playlist_response.json")
        videos = []
        for item in fx.get("items", []):
            vid = item["snippet"]["resourceId"]["videoId"]
            if vid in known:
                continue
            videos.append(
                VideoMeta(
                    external_id=vid,
                    url=f"https://www.youtube.com/watch?v={vid}",
                    title=item["snippet"]["title"],
                    description=item["snippet"].get("description"),
                    thumbnail_url=item["snippet"]
                    .get("thumbnails", {})
                    .get("high", {})
                    .get("url"),
                    published_at=item["snippet"].get("publishedAt"),
                )
            )
        return videos

    async def _fake_fetch_metrics(self, ids: list[str]) -> list[MetricsSnapshot]:
        fx = _load_fixture("youtube_videos_response.json")
        snapshots = []
        for item in fx.get("items", []):
            if item["id"] not in ids:
                continue
            stats = item.get("statistics", {})
            content = item.get("contentDetails", {})
            duration_sec = parse_iso_duration(content.get("duration"))
            is_short = bool(duration_sec and 0 < duration_sec <= 60)
            snapshots.append(
                MetricsSnapshot(
                    external_id=item["id"],
                    views=int(stats.get("viewCount", 0)),
                    likes=int(stats.get("likeCount", 0)),
                    comments=int(stats.get("commentCount", 0)),
                    duration_sec=duration_sec,
                    is_short=is_short,
                )
            )
        return snapshots

    # ------------------------------------------------------------------ #
    # Real API
    # ------------------------------------------------------------------ #

    async def resolve_channel(self, channel_url: str) -> ChannelInfo:
        if self.fake_mode:
            return await self._fake_resolve(channel_url)

        try:
            parsed = parse_channel_url(channel_url)
        except ValueError as e:
            raise ChannelNotFound(str(e))

        async with httpx.AsyncClient() as client:
            params = {"part": "snippet,contentDetails"}
            if parsed["kind"] == "channel_id":
                params["id"] = parsed["value"]
            elif parsed["kind"] == "handle":
                params["forHandle"] = parsed["value"]
            else:  # c_name / user_name → legacy forUsername
                params["forUsername"] = parsed["value"]

            data = await self._request(client, "channels", params, cost=1)
            items = data.get("items", [])
            if not items:
                raise ChannelNotFound(f"no channel for {channel_url}")
            item = items[0]
            return ChannelInfo(
                external_id=item["id"],
                channel_name=item["snippet"]["title"],
                extra={
                    "uploads_playlist_id": item["contentDetails"]["relatedPlaylists"]["uploads"],
                    "resolved_from": parsed["kind"],
                },
            )

    async def fetch_new_videos(
        self,
        channel: ChannelInfo,
        known_external_ids: set[str],
        *,
        results_limit: int | None = None,
    ) -> list[VideoMeta]:
        # results_limit игнорируется для YouTube: фиксированный maxResults=50
        # в playlistItems.list. Cost ограничивается только YouTube quota (10k/день).
        _ = results_limit
        if self.fake_mode:
            return await self._fake_fetch_new(channel, known_external_ids)

        playlist_id = channel.extra.get("uploads_playlist_id")
        if not playlist_id:
            raise PlatformError("missing uploads_playlist_id in channel.extra")

        async with httpx.AsyncClient() as client:
            data = await self._request(
                client,
                "playlistItems",
                {
                    "part": "snippet,contentDetails",
                    "playlistId": playlist_id,
                    "maxResults": 50,
                },
                cost=1,
            )
        videos: list[VideoMeta] = []
        for item in data.get("items", []):
            vid = item["snippet"]["resourceId"]["videoId"]
            if vid in known_external_ids:
                continue
            videos.append(
                VideoMeta(
                    external_id=vid,
                    url=f"https://www.youtube.com/watch?v={vid}",
                    title=item["snippet"].get("title"),
                    description=item["snippet"].get("description"),
                    thumbnail_url=item["snippet"]
                    .get("thumbnails", {})
                    .get("high", {})
                    .get("url"),
                    published_at=item["snippet"].get("publishedAt"),
                )
            )
        return videos

    async def fetch_metrics(self, external_ids: list[str]) -> list[MetricsSnapshot]:
        if not external_ids:
            return []
        if self.fake_mode:
            return await self._fake_fetch_metrics(external_ids)

        snapshots: list[MetricsSnapshot] = []
        async with httpx.AsyncClient() as client:
            # Batches по 50
            for i in range(0, len(external_ids), 50):
                batch = external_ids[i : i + 50]
                data = await self._request(
                    client,
                    "videos",
                    {"part": "statistics,contentDetails", "id": ",".join(batch)},
                    cost=1,
                )
                for item in data.get("items", []):
                    stats = item.get("statistics", {})
                    content = item.get("contentDetails", {})
                    duration_sec = parse_iso_duration(content.get("duration"))
                    is_short = bool(duration_sec and 0 < duration_sec <= 60)
                    snapshots.append(
                        MetricsSnapshot(
                            external_id=item["id"],
                            views=int(stats.get("viewCount", 0)),
                            likes=int(stats.get("likeCount", 0)),
                            comments=int(stats.get("commentCount", 0)),
                            duration_sec=duration_sec,
                            is_short=is_short,
                        )
                    )
        return snapshots
