"""
Instagram adapter (Apify).

Actor: apify~instagram-reel-scraper (default, переопределяется через env).
Input: {"username": [handle], "resultsLimit": N}
Output: массив Reels с полями
  - id / shortCode / url / displayUrl
  - ownerUsername / ownerFullName
  - caption
  - videoPlayCount / playCount / viewsCount
  - likesCount / likes_count / commentsCount / comments_count
  - videoDuration / video_duration (seconds, может быть float)
  - timestamp (ISO 8601)
  - type (Video)

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
    ProfileInfo,
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
        actor_id: str = "apify~instagram-reel-scraper",
        profile_actor_id: str = "apify~instagram-profile-scraper",
        fake_mode: bool = False,
        results_limit: int = 30,
        timeout_sec: int = 180,
        usage_counter: Callable[[str, int], None] | None = None,
    ):
        self.apify_token = apify_token
        self.actor_id = actor_id
        self.profile_actor_id = profile_actor_id
        self.fake_mode = fake_mode
        self.results_limit = results_limit
        self.timeout_sec = timeout_sec
        self._usage_counter = usage_counter
        # Кеш: channel_external_id -> {post_external_id: MetricsSnapshot}
        self._metrics_cache: dict[str, dict[str, MetricsSnapshot]] = {}

    # ------------------------------------------------------------------ #
    # Apify → domain
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_thumbnail(item: dict) -> str | None:
        """Пробуем несколько полей — Apify отдаёт превью по-разному для
        reels/фото/карусели/видео."""
        # 1. Прямое displayUrl (карусели, фото, иногда reels)
        if u := item.get("displayUrl"):
            return u
        # 2. images[0].url / images[0].displayUrl (карусели)
        images = item.get("images")
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict):
                if u := (first.get("url") or first.get("displayUrl")):
                    return u
        # 3. thumbnailSrc / previewImageUrl (некоторые версии актёра)
        for key in ("thumbnailSrc", "previewImageUrl", "previewUrl", "coverUrl"):
            if u := item.get(key):
                return u
        # 4. firstFrame / videoFirstFrame (актёр сохраняет первый кадр)
        for key in ("firstFrame", "videoFirstFrame"):
            if u := item.get(key):
                return u
        return None

    @staticmethod
    def _is_reel(item: dict) -> bool:
        """Берём всё что похоже на видео, отбрасываем закрепы и явные фото.
        Либеральный фильтр — лучше пропустить IGTV/video-пост, чем потерять Reel
        из-за того что Apify назвал поле иначе."""
        if item.get("isPinned"):
            return False
        # productType=="clips" — явный Reel
        if item.get("productType") == "clips":
            return True
        # Любые видео-сигналы
        if item.get("type") == "Video":
            return True
        if item.get("videoDuration"):
            return True
        if item.get("videoUrl") or item.get("videoPlayCount") or item.get("videoViewCount"):
            return True
        # Остальное (Image, Sidecar с фото) — отбрасываем
        return False

    @staticmethod
    def _parse_timestamp(ts: object) -> str | None:
        """Нормализует timestamp в ISO-строку UTC.
        Принимает ISO-строку ("2024-04-24T15:30:00Z") или Unix-int/float.
        """
        if ts is None:
            return None
        if isinstance(ts, (int, float)):
            from datetime import datetime, timezone as _tz
            return datetime.fromtimestamp(float(ts), tz=_tz.utc).isoformat()
        s = str(ts).strip()
        return s if s else None

    def _item_to_video_meta(self, item: dict) -> VideoMeta:
        external_id = item.get("shortCode") or item.get("id") or ""
        duration = item.get("videoDuration") or item.get("video_duration")
        duration_sec = int(duration) if isinstance(duration, (int, float)) and duration > 0 else None
        is_short = bool(duration_sec and duration_sec <= 60)
        return VideoMeta(
            external_id=str(external_id),
            url=item.get("url") or f"https://www.instagram.com/p/{external_id}/",
            title=(item.get("caption") or "")[:200] or None,
            description=item.get("caption"),
            thumbnail_url=self._extract_thumbnail(item),
            duration_sec=duration_sec,
            published_at=self._parse_timestamp(item.get("timestamp")),
            is_short=is_short,
        )

    def _item_to_metrics(self, item: dict) -> MetricsSnapshot:
        views = _to_int(
            item.get("videoPlayCount")
            or item.get("videoViewCount")
            or item.get("playCount")
            or item.get("viewsCount")
        )
        likes = _to_int(item.get("likesCount") or item.get("likes_count"))
        comments = _to_int(item.get("commentsCount") or item.get("comments_count"))
        duration = item.get("videoDuration") or item.get("video_duration")
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

    def _count_usage(self, items: int, *, actor_kind: str = "reel") -> None:
        if self._usage_counter is not None:
            try:
                self._usage_counter(self.name, items, actor_kind=actor_kind)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Fake mode
    # ------------------------------------------------------------------ #

    async def _fake_fetch(self, handle: str) -> list[dict]:
        """Фикстура — одни и те же посты на все handle; патчим:
        - shortCode/id: +суффикс handle, чтобы каждый автор дал уникальные external_id
          (UNIQUE(platform, external_id) блокирует вставку для 2+ авторов иначе).
        - timestamp: постепенно «свежим» 2ч, 12ч, 26ч назад — чтобы trending-алгоритм
          сработал в fake-режиме (окно 48ч).
        В реальном Apify эта патч-логика не нужна — handle возвращает свои live-данные.
        """
        from datetime import datetime, timedelta, timezone as _tz
        try:
            items = _load_fixture("instagram_profile.json")
        except FileNotFoundError:
            raise PlatformError("fixture_not_found: instagram_profile.json")
        suffix = "_" + handle
        now_utc = datetime.now(_tz.utc)
        fresh_hours_ago = [2, 12, 26, 40, 55, 70]  # идём от свежего к более старому
        out = []
        for idx, item in enumerate(items):
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
            # Динамический timestamp
            hours_ago = fresh_hours_ago[idx % len(fresh_hours_ago)]
            new["timestamp"] = (now_utc - timedelta(hours=hours_ago)).isoformat()
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
        effective_limit = results_limit if results_limit is not None else self.results_limit

        import structlog
        log = structlog.get_logger()
        log.info("ig_fetch_start", handle=handle, actor_id=self.actor_id, limit=effective_limit)

        if self.fake_mode:
            items = await self._fake_fetch(handle)
        else:
            try:
                items = await run_actor_sync(
                    actor_id=self.actor_id,
                    token=self.apify_token,
                    input_body={
                        "username": [handle],
                        "resultsLimit": effective_limit * 3,
                    },
                    timeout_sec=self.timeout_sec,
                )
            except Exception as e:
                # Перебрасываем PlatformError — crawler обработает как platform_error
                raise PlatformError(f"apify_instagram_failed: {type(e).__name__}: {e}")

        self._count_usage(len(items))

        # Фильтр: только Reels, не закрепы.
        # В fake-режиме фильтр не применяем (фикстуры и так только с видео).
        if not self.fake_mode:
            import sys
            from collections import Counter
            valid = [it for it in items if isinstance(it, dict)]
            all_keys = sorted({k for it in valid for k in it.keys()})
            type_counts = Counter(it.get("type", "∅") for it in valid)
            pt_counts = Counter(it.get("productType", "∅") for it in valid)
            pinned_count = sum(1 for it in valid if it.get("isPinned"))
            has_vdur = sum(1 for it in valid if it.get("videoDuration"))
            has_vurl = sum(1 for it in valid if it.get("videoUrl"))
            has_vplay = sum(1 for it in valid if it.get("videoPlayCount"))
            filtered = [it for it in valid if self._is_reel(it)]
            print(
                f"[instagram] handle={handle} raw={len(items)} "
                f"filtered={len(filtered)} pinned={pinned_count} "
                f"type={dict(type_counts)} productType={dict(pt_counts)} "
                f"has_vDur={has_vdur} has_vUrl={has_vurl} has_vPlay={has_vplay} "
                f"all_keys={all_keys}",
                file=sys.stderr,
                flush=True,
            )
            items = filtered[:effective_limit]

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

    async def fetch_profile(self, handle: str) -> ProfileInfo | None:
        if self.fake_mode:
            try:
                items = _load_fixture("instagram_profile_meta.json")
            except FileNotFoundError:
                return None
            item = next((x for x in items if x.get("username") == handle), None)
            if not item:
                return None
        else:
            try:
                items = await run_actor_sync(
                    actor_id=self.profile_actor_id,
                    token=self.apify_token,
                    input_body={"usernames": [handle]},
                    timeout_sec=self.timeout_sec,
                )
            except Exception:
                return None
            self._count_usage(len(items), actor_kind="profile")
            if not items:
                return None
            item = items[0]
        return ProfileInfo(
            username=handle,
            full_name=item.get("fullName") or item.get("full_name"),
            followers_count=_to_int(item.get("followersCount") or item.get("followers_count")),
            posts_count=_to_int(item.get("postsCount") or item.get("posts_count")),
            avatar_url=item.get("profilePicUrlHD") or item.get("profilePicUrl") or item.get("profile_pic_url"),
            is_verified=bool(item.get("verified") or item.get("isVerified")),
            is_private=bool(item.get("isPrivate") or item.get("is_private")),
            business_category=item.get("businessCategoryName") or item.get("category"),
        )
