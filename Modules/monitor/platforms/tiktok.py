"""
TikTok adapter (Apify).

Actor: clockworks/tiktok-scraper (default, переопределяется через env).
Input: {"profiles": [handle_without_at], "resultsPerPage": N, "shouldDownloadVideos": false}
Output: массив видео с полями
  - id / webVideoUrl / videoUrl
  - authorMeta: {name, nickName, fans, ...}
  - text (caption)
  - videoMeta: {duration, coverUrl, originalCoverUrl}
  - playCount / diggCount (likes) / commentCount / shareCount
  - createTime (unix timestamp seconds)
  - createTimeISO

FAKE MODE: fixtures/tiktok_profile.json.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from platforms.apify_client import run_actor_sync
from platforms.base import (
    ChannelInfo,
    ChannelNotFound,
    MetricsSnapshot,
    PlatformError,
    VideoMeta,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

# Принимаем:
# https://www.tiktok.com/@username
# https://tiktok.com/@username/
# https://www.tiktok.com/@username/video/123 (извлечём handle)
_TT_HANDLE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?tiktok\.com/@([A-Za-z0-9._]+)",
    re.IGNORECASE,
)


def parse_tiktok_url(url: str) -> str:
    url = url.strip()
    m = _TT_HANDLE_RE.search(url)
    if not m:
        raise ValueError(f"unrecognized tiktok url: {url}")
    return m.group(1)


def _load_fixture(name: str) -> list[dict]:
    path = FIXTURES_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    return data  # type: ignore[return-value]


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except (ValueError, TypeError):
        return default


class TikTokSource:
    name = "tiktok"

    def __init__(
        self,
        *,
        apify_token: str = "",
        actor_id: str = "clockworks~tiktok-scraper",
        fake_mode: bool = False,
        results_limit: int = 30,
        timeout_sec: int = 180,
        usage_counter: Callable[[str, int], None] | None = None,
    ):
        self.apify_token = apify_token
        self.actor_id = actor_id
        self.fake_mode = fake_mode
        self.results_limit = results_limit
        self.timeout_sec = timeout_sec
        self._usage_counter = usage_counter
        self._metrics_cache: dict[str, dict[str, MetricsSnapshot]] = {}

    # ------------------------------------------------------------------ #

    def _item_to_video_meta(self, item: dict) -> VideoMeta:
        external_id = str(item.get("id") or "")
        video_meta = item.get("videoMeta") or {}
        duration_sec = _to_int(video_meta.get("duration")) or None
        is_short = bool(duration_sec and duration_sec <= 60)
        author = item.get("authorMeta") or {}
        handle = author.get("name") or ""
        url = item.get("webVideoUrl") or (
            f"https://www.tiktok.com/@{handle}/video/{external_id}" if handle else ""
        )
        published_iso = item.get("createTimeISO")
        if not published_iso and item.get("createTime"):
            try:
                published_iso = datetime.fromtimestamp(
                    int(item["createTime"]), tz=timezone.utc
                ).isoformat()
            except Exception:
                published_iso = None
        return VideoMeta(
            external_id=external_id,
            url=url,
            title=(item.get("text") or "")[:200] or None,
            description=item.get("text"),
            thumbnail_url=video_meta.get("coverUrl") or video_meta.get("originalCoverUrl"),
            duration_sec=duration_sec,
            published_at=published_iso,
            is_short=is_short,
        )

    def _item_to_metrics(self, item: dict) -> MetricsSnapshot:
        video_meta = item.get("videoMeta") or {}
        duration_sec = _to_int(video_meta.get("duration")) or None
        return MetricsSnapshot(
            external_id=str(item.get("id") or ""),
            views=_to_int(item.get("playCount")),
            likes=_to_int(item.get("diggCount")),
            comments=_to_int(item.get("commentCount")),
            duration_sec=duration_sec,
            is_short=bool(duration_sec and duration_sec <= 60),
        )

    def _count_usage(self, items: int) -> None:
        if self._usage_counter is not None:
            try:
                self._usage_counter(self.name, items)
            except Exception:
                pass

    # ------------------------------------------------------------------ #

    async def _fake_fetch(self, handle: str) -> list[dict]:
        """См. instagram.py: суффиксуем id хендлом для уникальности per-handle."""
        try:
            items = _load_fixture("tiktok_profile.json")
        except FileNotFoundError:
            raise PlatformError("fixture_not_found: tiktok_profile.json")
        suffix = "_" + handle
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            new = dict(item)
            if "id" in new and new["id"]:
                new["id"] = str(new["id"]) + suffix
            out.append(new)
        return out

    # ------------------------------------------------------------------ #

    async def resolve_channel(self, channel_url: str) -> ChannelInfo:
        try:
            handle = parse_tiktok_url(channel_url)
        except ValueError as e:
            raise ChannelNotFound(str(e))
        return ChannelInfo(
            external_id=handle,
            channel_name=handle,
            extra={"resolved_from": "url"},
        )

    async def fetch_new_videos(
        self,
        channel: ChannelInfo,
        known_external_ids: set[str],
        *,
        results_limit: int | None = None,
    ) -> list[VideoMeta]:
        handle = channel.external_id
        effective_limit = results_limit if results_limit is not None else self.results_limit

        if self.fake_mode:
            items = await self._fake_fetch(handle)
        else:
            try:
                items = await run_actor_sync(
                    actor_id=self.actor_id,
                    token=self.apify_token,
                    input_body={
                        "profiles": [handle],
                        "resultsPerPage": effective_limit,
                        "shouldDownloadVideos": False,
                        "shouldDownloadCovers": False,
                    },
                    timeout_sec=self.timeout_sec,
                )
            except Exception as e:
                raise PlatformError(f"apify_tiktok_failed: {type(e).__name__}: {e}")

        self._count_usage(len(items))

        channel_cache: dict[str, MetricsSnapshot] = {}
        new_videos: list[VideoMeta] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            vm = self._item_to_video_meta(item)
            if not vm.external_id:
                continue
            channel_cache[vm.external_id] = self._item_to_metrics(item)
            if vm.external_id not in known_external_ids:
                new_videos.append(vm)
        self._metrics_cache[handle] = channel_cache

        if items:
            first = items[0] if isinstance(items[0], dict) else {}
            author = first.get("authorMeta") or {}
            name = author.get("nickName") or author.get("name")
            if name:
                channel.channel_name = str(name)

        return new_videos

    async def fetch_metrics(self, external_ids: list[str]) -> list[MetricsSnapshot]:
        if not external_ids:
            return []
        snapshots: list[MetricsSnapshot] = []
        for cache in self._metrics_cache.values():
            for vid in external_ids:
                snap = cache.get(vid)
                if snap is not None:
                    snapshots.append(snap)
        return snapshots
