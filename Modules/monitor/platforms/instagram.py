"""
Instagram adapter (Apify).

Actor: apify/instagram-scraper (default, переопределяется через env).
Input: {"directUrls": [profile_url], "resultsType": "posts", "resultsLimit": N}
Output: массив постов с полями
  - id / shortCode / url / displayUrl
  - ownerUsername / ownerFullName
  - caption
  - videoViewCount / videoPlayCount / viewsCount
  - likesCount / commentsCount
  - videoDuration (seconds, может быть float)
  - timestamp (ISO 8601)
  - type (Image | Video | Sidecar)

FAKE MODE: fixtures/instagram_profile.json — массив как вернёт актёр.
"""
import json
import re
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
# https://www.instagram.com/username/
# https://instagram.com/username
# instagram.com/username/reels/
_IG_HANDLE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9._]+)/?",
    re.IGNORECASE,
)


def parse_instagram_url(url: str) -> str:
    """Вернуть handle (username) из URL. Raises ValueError."""
    url = url.strip().rstrip("/")
    m = _IG_HANDLE_RE.search(url)
    if not m:
        raise ValueError(f"unrecognized instagram url: {url}")
    handle = m.group(1)
    # Исключаем "p", "reel", "tv", "explore" — это пост-URL, а не профиль
    if handle.lower() in {"p", "reel", "reels", "tv", "explore", "stories"}:
        raise ValueError(f"this is a post url, not a profile: {url}")
    return handle


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


class InstagramSource:
    name = "instagram"

    def __init__(
        self,
        *,
        apify_token: str = "",
        actor_id: str = "apify~instagram-scraper",
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
        # Кеш: channel_external_id -> {post_external_id: MetricsSnapshot}
        self._metrics_cache: dict[str, dict[str, MetricsSnapshot]] = {}

    # ------------------------------------------------------------------ #
    # Apify → domain
    # ------------------------------------------------------------------ #

    def _item_to_video_meta(self, item: dict) -> VideoMeta:
        external_id = item.get("shortCode") or item.get("id") or ""
        duration = item.get("videoDuration")
        duration_sec = int(duration) if isinstance(duration, (int, float)) and duration > 0 else None
        is_short = bool(duration_sec and duration_sec <= 60)
        return VideoMeta(
            external_id=str(external_id),
            url=item.get("url") or f"https://www.instagram.com/p/{external_id}/",
            title=(item.get("caption") or "")[:200] or None,
            description=item.get("caption"),
            thumbnail_url=item.get("displayUrl"),
            duration_sec=duration_sec,
            published_at=item.get("timestamp"),
            is_short=is_short,
        )

    def _item_to_metrics(self, item: dict) -> MetricsSnapshot:
        # Instagram posts: videoViewCount / videoPlayCount / viewsCount
        views = _to_int(
            item.get("videoViewCount")
            or item.get("videoPlayCount")
            or item.get("viewsCount")
        )
        likes = _to_int(item.get("likesCount"))
        comments = _to_int(item.get("commentsCount"))
        duration = item.get("videoDuration")
        duration_sec = int(duration) if isinstance(duration, (int, float)) and duration > 0 else None
        external_id = item.get("shortCode") or item.get("id") or ""
        return MetricsSnapshot(
            external_id=str(external_id),
            views=views,
            likes=likes,
            comments=comments,
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
    # Fake mode
    # ------------------------------------------------------------------ #

    async def _fake_fetch(self, handle: str) -> list[dict]:
        """Фикстура — одни и те же посты на все handle; патчим shortCode/id
        суффиксом handle, чтобы каждый автор получил уникальные external_id
        (иначе UNIQUE(platform, external_id) блокирует вставку для 2+ авторов).
        В реальном Apify эта проблема не возникает — handle возвращает свои посты.
        """
        try:
            items = _load_fixture("instagram_profile.json")
        except FileNotFoundError:
            raise PlatformError("fixture_not_found: instagram_profile.json")
        suffix = "_" + handle
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            new = dict(item)
            if "shortCode" in new and new["shortCode"]:
                new["shortCode"] = str(new["shortCode"]) + suffix
            if "id" in new and new["id"]:
                new["id"] = str(new["id"]) + suffix
            if "ownerUsername" not in new or not new["ownerUsername"]:
                new["ownerUsername"] = handle
            if "ownerFullName" not in new or not new["ownerFullName"]:
                new["ownerFullName"] = handle
            out.append(new)
        return out

    # ------------------------------------------------------------------ #
    # MetricsSource API
    # ------------------------------------------------------------------ #

    async def resolve_channel(self, channel_url: str) -> ChannelInfo:
        """Парсим URL локально — Apify-вызов не делаем, чтобы не тратить compute.
        channel_name заполнится при первом fetch_new_videos из поля ownerFullName."""
        try:
            handle = parse_instagram_url(channel_url)
        except ValueError as e:
            raise ChannelNotFound(str(e))
        return ChannelInfo(
            external_id=handle,
            channel_name=handle,  # placeholder, обновится в crawler после fetch_new_videos
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
        profile_url = f"https://www.instagram.com/{handle}/"
        effective_limit = results_limit if results_limit is not None else self.results_limit

        if self.fake_mode:
            items = await self._fake_fetch(handle)
        else:
            try:
                items = await run_actor_sync(
                    actor_id=self.actor_id,
                    token=self.apify_token,
                    input_body={
                        "directUrls": [profile_url],
                        "resultsType": "posts",
                        "resultsLimit": effective_limit,
                        "addParentData": False,
                    },
                    timeout_sec=self.timeout_sec,
                )
            except Exception as e:
                # Перебрасываем PlatformError — crawler обработает как platform_error
                raise PlatformError(f"apify_instagram_failed: {type(e).__name__}: {e}")

        self._count_usage(len(items))

        # Заполняем кеш метрик для ВСЕХ постов (не только новых)
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

        # Обновить channel_name из первого item, если не совпадает
        if items:
            first = items[0] if isinstance(items[0], dict) else {}
            owner_name = first.get("ownerFullName") or first.get("ownerUsername")
            if owner_name:
                channel.channel_name = str(owner_name)

        return new_videos

    async def fetch_metrics(self, external_ids: list[str]) -> list[MetricsSnapshot]:
        """Читает из кеша, заполненного в fetch_new_videos. Apify не дёргаем повторно."""
        if not external_ids:
            return []
        snapshots: list[MetricsSnapshot] = []
        # Перебираем все кеши каналов (обычно один на crawl)
        for cache in self._metrics_cache.values():
            for vid in external_ids:
                snap = cache.get(vid)
                if snap is not None:
                    snapshots.append(snap)
        return snapshots
