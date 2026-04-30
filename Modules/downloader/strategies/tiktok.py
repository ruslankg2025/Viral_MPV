"""TikTok: yt-dlp-first → Apify fallback (clockworks~tiktok-scraper)."""
from apify_client import ApifyError, run_actor_sync
from config import get_settings
from logging_setup import get_logger
from strategies.base import BaseDownloader, DownloadResult
from strategies.file_utils import atomic_download, ffprobe_meta, sha256_of
from strategies.httpx_download import HttpDownloadError, stream_download
from strategies.url_parsers import extract_tiktok_id
from strategies.ytdlp_helper import YtDlpError, download_with_ytdlp

log = get_logger("strategies.tiktok")


class TikTokDownloader(BaseDownloader):
    name = "tiktok"
    prefix = "tt"

    async def download(self, url, *, downloads_dir, quality):
        settings = get_settings()
        external_id = extract_tiktok_id(url)
        target = (downloads_dir / f"{self.prefix}_{external_id}.mp4").resolve()

        if target.exists():
            return self._build_result(target, external_id, strategy="cached")

        # 1) yt-dlp
        last_err: str | None = None
        try:
            quality_fmt = "bestaudio" if quality == "audio_only" else "best[height<=720]/best"

            async def write_via_ytdlp(tmp):
                await download_with_ytdlp(url, tmp, format_str=quality_fmt)
            await atomic_download(target, write_via_ytdlp)
            log.info("tt_ytdlp_ok", external_id=external_id)
            return self._build_result(target, external_id, strategy="yt_dlp")
        except YtDlpError as e:
            last_err = f"ytdlp: {e}"
            log.warning("tt_ytdlp_failed", external_id=external_id, error=str(e))

        # 2) Apify fallback
        if not settings.apify_token:
            raise RuntimeError(
                f"tiktok_download_failed: ytdlp={last_err}; apify_token_not_set"
            )

        try:
            items = await run_actor_sync(
                actor_id=settings.apify_tiktok_actor,
                token=settings.apify_token,
                input_body={
                    "postURLs": [url],
                    "shouldDownloadVideos": True,
                    "resultsPerPage": 1,
                },
                timeout_sec=120,
            )
            video_url = self._extract_video_url(items)
            if not video_url:
                raise RuntimeError(
                    f"tiktok_download_failed: ytdlp={last_err}; "
                    f"apify_no_video_url"
                )

            async def write_via_apify(tmp):
                await stream_download(video_url, tmp, timeout_sec=120)
            await atomic_download(target, write_via_apify)
            log.info("tt_apify_ok", external_id=external_id)
            return self._build_result(target, external_id, strategy="apify")
        except (ApifyError, HttpDownloadError) as e:
            raise RuntimeError(
                f"tiktok_download_failed: ytdlp={last_err}; apify: {e}"
            ) from e

    @staticmethod
    def _extract_video_url(items: list[dict]) -> str | None:
        if not items:
            return None
        item = items[0]
        # clockworks/tiktok-scraper варианты:
        #   videoMeta.downloadAddr (без водяного знака — если плагин качал)
        #   videoMeta.playAddr     (с водяным знаком)
        #   mediaUrls[0]
        meta = item.get("videoMeta") or {}
        for key in ("downloadAddr", "playAddr"):
            v = meta.get(key)
            if v:
                return v
        urls = item.get("mediaUrls") or []
        if urls:
            first = urls[0]
            return first.get("url") if isinstance(first, dict) else first
        return None

    def _build_result(self, target, external_id, *, strategy: str) -> DownloadResult:
        sha = sha256_of(target)
        meta = ffprobe_meta(target)
        return DownloadResult(
            file_path=target,
            external_id=external_id,
            platform="tiktok",
            duration_sec=meta["duration_sec"],
            width=meta["width"],
            height=meta["height"],
            size_bytes=target.stat().st_size,
            sha256=sha,
            format="mp4",
            strategy_used=strategy,
        )
