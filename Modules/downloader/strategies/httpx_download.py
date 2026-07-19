"""Стриминг-скачивание media-URL в файл (для случаев когда Apify-актор
вернул прямой videoUrl)."""
import asyncio

import httpx

from pathlib import Path

from logging_setup import get_logger

log = get_logger("strategies.httpx_download")


class HttpDownloadError(Exception):
    pass


async def stream_download(
    url: str,
    output_path: Path,
    *,
    timeout_sec: int = 120,
    max_retries: int = 3,
) -> int:
    """Стриминговая запись из URL в файл. Возвращает size в байтах.

    Сетевые сбои ретраятся с backoff — тем же паттерном, что в apify_client:
    разовый ConnectError до CDN ронял весь orchestrator-run целиком, хотя
    повтор проходил штатно. 4xx/5xx не ретраим: это ответ сервера, а не
    обрыв связи. Каждая попытка открывает файл заново на "wb", так что
    хвост неудачной записи затирается.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    backoff = [2, 8]
    last_exc: Exception | None = None

    for attempt in range(max_retries):
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
            break
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as e:
            last_exc = HttpDownloadError(f"network: {type(e).__name__}: {e}")
            log.warning(
                "stream_download_retry",
                url=url[:80], attempt=attempt + 1, error=f"{type(e).__name__}: {e}",
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(backoff[attempt])
    else:
        raise last_exc from None

    if written == 0:
        raise HttpDownloadError("empty_response")
    log.info("stream_download_done", url=url[:80], size=written)
    return written
