"""Instagram: Apify-first → yt-dlp fallback (per Ревизия 2026-04-29).

Apify actor (по умолчанию `apify~instagram-scraper`) принимает `directUrls`,
возвращает items с `videoUrl`. Скачиваем videoUrl стримом.

При ошибке Apify (token missing / actor down / нет videoUrl) — fallback на
yt-dlp с опциональным cookies-file.
"""
import asyncio

from apify_client import ApifyError, run_actor_sync
from config import get_settings
from logging_setup import get_logger
from strategies.base import BaseDownloader, DownloadResult
from strategies.file_utils import atomic_download, ffprobe_meta, sha256_of
from strategies.httpx_download import HttpDownloadError, stream_download
from strategies.url_parsers import extract_instagram_id
from strategies.ytdlp_helper import YtDlpError, download_with_ytdlp

log = get_logger("strategies.instagram")


class InstagramDownloader(BaseDownloader):
    name = "instagram"
    prefix = "ig"

    async def download(self, url, *, downloads_dir, quality):
        settings = get_settings()
        external_id = extract_instagram_id(url)
        target = (downloads_dir / f"{self.prefix}_{external_id}.mp4").resolve()

        # Cache hit
        if target.exists():
            return self._build_result(target, external_id, strategy="cached")

        # 1) Apify
        last_err: str | None = None
        if settings.apify_token:
            try:
                items = await run_actor_sync(
                    actor_id=settings.apify_instagram_actor,
                    token=settings.apify_token,
                    input_body={
                        "directUrls": [url],
                        "resultsLimit": 1,
                        "addParentData": False,
                    },
                    timeout_sec=120,
                )
                video_url = self._extract_video_url(items)
                if video_url:
                    async def write_via_apify(tmp):
                        await stream_download(video_url, tmp, timeout_sec=120)
                    await atomic_download(target, write_via_apify)
                    log.info("ig_apify_ok", external_id=external_id)
                    return self._build_result(target, external_id, strategy="apify")
                last_err = "apify_no_video_url_in_response"
                log.warning("ig_apify_no_video_url", external_id=external_id)
            except (ApifyError, HttpDownloadError) as e:
                last_err = f"apify: {e}"
                log.warning("ig_apify_failed", external_id=external_id, error=str(e))
        else:
            last_err = "apify_token_not_set"

        # 2) yt-dlp fallback
        try:
            cookies = settings.instagram_cookies_file or None
            quality_fmt = "bestaudio" if quality == "audio_only" else "best[height<=720]/best"

            async def write_via_ytdlp(tmp):
                await download_with_ytdlp(
                    url, tmp, format_str=quality_fmt, cookies_file=cookies
                )
            await atomic_download(target, write_via_ytdlp)
            log.info("ig_ytdlp_ok", external_id=external_id)
            return self._build_result(target, external_id, strategy="yt_dlp")
        except YtDlpError as e:
            raise RuntimeError(
                f"instagram_download_failed: apify={last_err}; ytdlp={e}"
            ) from e

    @staticmethod
    def _extract_video_url(items: list[dict]) -> str | None:
        if not items:
            return None
        item = items[0]
        # apify/instagram-scraper возвращает либо videoUrl, либо videoUrls (array)
        v = item.get("videoUrl")
        if v:
            return v
        urls = item.get("videoUrls") or []
        if urls:
            first = urls[0]
            return first.get("url") if isinstance(first, dict) else first
        # Иногда поле называется displayUrl, но это превью — не используем
        return None

    def _build_result(self, target, external_id, *, strategy: str) -> DownloadResult:
        sha = sha256_of(target)
        meta = ffprobe_meta(target)
        return DownloadResult(
            file_path=target,
            external_id=external_id,
            platform="instagram",
            duration_sec=meta["duration_sec"],
            width=meta["width"],
            height=meta["height"],
            size_bytes=target.stat().st_size,
            sha256=sha,
            format="mp4",
            strategy_used=strategy,
        )
