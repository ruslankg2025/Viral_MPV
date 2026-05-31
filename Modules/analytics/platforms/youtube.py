"""YouTube-адаптер метрик.

Источник метрик — YouTube Data API v3, videos.list?part=statistics
(https://developers.google.com/youtube/v3/docs/videos/list). statistics
содержит публично доступные счётчики:
  - viewCount   → views
  - likeCount   → likes      (может быть скрыт автором → отсутствует)
  - commentCount→ comments   (может быть отключён → отсутствует)
Doc: https://developers.google.com/youtube/v3/docs/videos#statistics

Чего НЕТ в публичном statistics:
  - reach / уникальный охват, shares, saves (добавления в плейлисты),
    переходы по ссылкам — это приватная аналитика канала и доступна только
    через YouTube **Analytics** API (OAuth владельца, метрики shares, saves,
    cardClickRate и т.п.). Здесь по публичному ключу их нет → 0.
    reach в нормализации = views (как и у VK, единственное близкое значение).

Аутентификация: API-ключ (?key=...). external_id публикации publisher для
youtube_shorts — это videoId (11 символов, напр. "dQw4w9WgXcQ").

Без YOUTUBE_API_KEY (token=None) — YouTubeError; воркер берёт mock-метрики.
"""
from __future__ import annotations

import httpx

from platforms.base import PlatformAdapter, PlatformMetrics


YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def mock_metrics(external_id: str) -> dict[str, int]:
    """Детерминированные mock-метрики YouTube (без ключа / для dev/тестов)."""
    seed = sum(ord(ch) for ch in external_id)
    views = 2000 + seed * 15
    return {
        "views": views,
        "reach": views,  # публичного reach у YouTube нет → дублируем views
        "likes": 80 + seed * 2,
        "comments": 10 + seed % 9,
        "shares": 0,   # только в Analytics API (OAuth владельца)
        "saves": 0,    # «добавления в плейлисты» — только Analytics API
        "click_through_to_external": 0,
    }


class YouTubeError(RuntimeError):
    pass


class YouTubeAdapter(PlatformAdapter):
    """Реальный адаптер YouTube (Data API v3 videos.list statistics).

    Реально доступно по публичному API-ключу: views, likes, comments.
    reach/shares/saves/click_through — только в Analytics API (OAuth) → 0.
    """

    platform_name = "youtube"

    async def fetch_metrics(self, external_id: str) -> PlatformMetrics:
        if not self.token:
            raise YouTubeError("youtube_api_key_not_set")
        if not external_id:
            raise YouTubeError("empty_external_id")

        params = {
            "part": "statistics",
            "id": external_id,
            "key": self.token,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(YOUTUBE_VIDEOS_URL, params=params)
        except httpx.RequestError as e:
            raise YouTubeError(f"network: {e}") from e
        if resp.status_code != 200:
            raise YouTubeError(f"youtube_http_{resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise YouTubeError(f"youtube_parse: {e}") from e

        if "error" in data:
            err = data["error"]
            raise YouTubeError(
                f"youtube_api_error_{err.get('code')}: {err.get('message')}"
            )

        items = data.get("items") or []
        if not items:
            raise YouTubeError(f"youtube_video_not_found: {external_id}")
        stats = items[0].get("statistics") or {}
        return self._parse_stats(external_id, stats)

    def _parse_stats(self, external_id: str, stats: dict) -> PlatformMetrics:
        """statistics object (строковые числа) → PlatformMetrics."""
        def _int(field: str) -> int:
            # statistics-поля приходят строками; могут отсутствовать (скрыты).
            try:
                return int(stats.get(field) or 0)
            except (TypeError, ValueError):
                return 0

        views = _int("viewCount")
        return PlatformMetrics(
            platform=self.platform_name,
            external_id=external_id,
            views=views,
            reach=views,  # публичного reach нет → fallback на views
            likes=_int("likeCount"),
            comments=_int("commentCount"),
            shares=0,  # только Analytics API (OAuth владельца)
            saves=0,   # только Analytics API
            click_through_to_external=0,
        )
