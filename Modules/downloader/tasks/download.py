from typing import Any

from logging_setup import get_logger
from state import state
from strategies.base import BaseDownloader
from strategies.instagram import InstagramDownloader
from strategies.stub import StubDownloader
from strategies.tiktok import TikTokDownloader
from strategies.youtube import YouTubeShortsDownloader

log = get_logger("tasks.download")


def _select_strategy(platform: str) -> BaseDownloader:
    if platform == "instagram":
        return InstagramDownloader()
    if platform == "tiktok":
        return TikTokDownloader()
    if platform == "youtube_shorts":
        return YouTubeShortsDownloader()
    raise ValueError(f"unsupported_platform: {platform}")


async def run_download(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Handler для kind='download'.

    STUB_MODE=true → возвращает фикстуру (Этап 1).
    STUB_MODE=false → выбирает стратегию по platform:
      - instagram: Apify-first → yt-dlp fallback
      - tiktok:    yt-dlp-first → Apify fallback
      - youtube_shorts: yt-dlp only
    """
    settings = state.settings
    url = payload["url"]
    platform = payload["platform"]
    quality = payload.get("quality", "720p")
    downloads_dir = settings.media_dir / "downloads"

    if settings.stub_mode:
        strategy = StubDownloader(settings.fixture_path)
    else:
        strategy = _select_strategy(platform)

    result = await strategy.download(url, downloads_dir=downloads_dir, quality=quality)

    # Defense-in-depth: file_path в БД — всегда нормализованный resolved-путь.
    # Защищает от path-traversal в DELETE /files (см. ревью 2026-04-29 #1).
    resolved_path = str(result.file_path.resolve())

    return {
        "file_path": resolved_path,
        "external_id": result.external_id,
        "platform": platform,  # echo из запроса (не stub)
        "duration_sec": result.duration_sec,
        "width": result.width,
        "height": result.height,
        "size_bytes": result.size_bytes,
        "sha256": result.sha256,
        "format": result.format,
        "strategy_used": result.strategy_used,
    }
