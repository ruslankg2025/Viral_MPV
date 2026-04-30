"""YouTube Shorts: yt-dlp-only (Apify fallback не реализован в v1).

Фильтр: проверяем duration ≤ 60s после скачивания (это и есть определение Short).
Если ролик длиннее — это не Short, пропускаем (не наш сценарий).
"""
from logging_setup import get_logger
from strategies.base import BaseDownloader, DownloadResult
from strategies.file_utils import atomic_download, ffprobe_meta, sha256_of
from strategies.url_parsers import extract_youtube_shorts_id
from strategies.ytdlp_helper import YtDlpError, download_with_ytdlp

log = get_logger("strategies.youtube")

SHORTS_MAX_DURATION_SEC = 60


class YouTubeShortsDownloader(BaseDownloader):
    name = "youtube_shorts"
    prefix = "yt"

    async def download(self, url, *, downloads_dir, quality):
        external_id = extract_youtube_shorts_id(url)
        target = (downloads_dir / f"{self.prefix}_{external_id}.mp4").resolve()

        if target.exists():
            return self._build_result(target, external_id, strategy="cached")

        quality_fmt = "bestaudio" if quality == "audio_only" else "best[height<=720][ext=mp4]/best[height<=720]/best"

        async def write_via_ytdlp(tmp):
            await download_with_ytdlp(url, tmp, format_str=quality_fmt)
        try:
            await atomic_download(target, write_via_ytdlp)
        except YtDlpError as e:
            raise RuntimeError(f"youtube_shorts_download_failed: {e}") from e

        meta = ffprobe_meta(target)
        if meta["duration_sec"] and meta["duration_sec"] > SHORTS_MAX_DURATION_SEC:
            # Не Short. Удаляем и сообщаем.
            target.unlink(missing_ok=True)
            raise RuntimeError(
                f"not_a_short: duration={meta['duration_sec']:.1f}s > "
                f"{SHORTS_MAX_DURATION_SEC}s"
            )

        log.info("yt_ytdlp_ok", external_id=external_id, duration=meta["duration_sec"])
        return self._build_result(target, external_id, strategy="yt_dlp", _meta=meta)

    def _build_result(self, target, external_id, *, strategy: str, _meta=None) -> DownloadResult:
        sha = sha256_of(target)
        meta = _meta if _meta is not None else ffprobe_meta(target)
        return DownloadResult(
            file_path=target,
            external_id=external_id,
            platform="youtube_shorts",
            duration_sec=meta["duration_sec"],
            width=meta["width"],
            height=meta["height"],
            size_bytes=target.stat().st_size,
            sha256=sha,
            format="mp4",
            strategy_used=strategy,
        )
