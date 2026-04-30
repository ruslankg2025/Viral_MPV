"""yt-dlp wrapper. Используется TikTok и YouTube Shorts стратегиями."""
import asyncio
from pathlib import Path

from logging_setup import get_logger

log = get_logger("strategies.ytdlp")


class YtDlpError(Exception):
    pass


def _run_ytdlp(url: str, output_path: Path, *, format_str: str, cookies_file: str | None) -> dict:
    """Sync вызов yt-dlp. Запускать через asyncio.to_thread."""
    import yt_dlp

    opts = {
        "format": format_str,
        "outtmpl": str(output_path),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        "merge_output_format": "mp4",
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return {
                "duration_sec": info.get("duration"),
                "width": info.get("width"),
                "height": info.get("height"),
                "ext": info.get("ext"),
                "title": info.get("title"),
            }
    except yt_dlp.utils.DownloadError as e:
        raise YtDlpError(f"download_error: {e}") from e
    except Exception as e:
        raise YtDlpError(f"{type(e).__name__}: {e}") from e


async def download_with_ytdlp(
    url: str,
    output_path: Path,
    *,
    format_str: str = "best[height<=720]/best",
    cookies_file: str | None = None,
) -> dict:
    """Async-обёртка. yt-dlp blocking, поэтому через to_thread."""
    log.info("ytdlp_start", url=url, output=str(output_path), format=format_str)
    info = await asyncio.to_thread(
        _run_ytdlp, url, output_path, format_str=format_str, cookies_file=cookies_file
    )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise YtDlpError(f"output_missing_or_empty: {output_path}")
    log.info(
        "ytdlp_done",
        url=url,
        size=output_path.stat().st_size,
        duration=info.get("duration_sec"),
    )
    return info
