"""Стриминг-скачивание media-URL в файл (для случаев когда Apify-актор
вернул прямой videoUrl)."""
import httpx

from pathlib import Path

from logging_setup import get_logger

log = get_logger("strategies.httpx_download")


class HttpDownloadError(Exception):
    pass


async def stream_download(url: str, output_path: Path, *, timeout_sec: int = 120) -> int:
    """Стриминговая запись из URL в файл. Возвращает size в байтах."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        async with httpx.AsyncClient(
            timeout=timeout_sec, follow_redirects=True
        ) as client:
            async with client.stream("GET", url) as r:
                if r.status_code != 200:
                    raise HttpDownloadError(
                        f"http_{r.status_code}: {r.reason_phrase}"
                    )
                with output_path.open("wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                        f.write(chunk)
                        written += len(chunk)
    except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as e:
        raise HttpDownloadError(f"network: {type(e).__name__}: {e}") from e

    if written == 0:
        raise HttpDownloadError("empty_response")
    log.info("stream_download_done", url=url[:80], size=written)
    return written
