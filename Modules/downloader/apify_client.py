"""Apify helper — один httpx-вызов run-sync-get-dataset-items.

Adapted from Modules/monitor/platforms/apify_client.py (тот же паттерн —
не ретраим 4xx, повторяем 5xx/timeout с backoff). Изолированная копия,
чтобы downloader не зависел от monitor.
"""
import asyncio

import httpx

APIFY_BASE = "https://api.apify.com/v2"


class ApifyError(Exception):
    pass


class ApifyTransientError(ApifyError):
    """Сетевая ошибка / 5xx — стоит ретраить."""


async def run_actor_sync(
    *,
    actor_id: str,
    token: str,
    input_body: dict,
    timeout_sec: int = 180,
    max_retries: int = 3,
) -> list[dict]:
    """Вызвать актёра синхронно и получить items из default dataset.

    Actor IDs в Apify URL пишутся через `~` вместо `/`
    (например `apify~instagram-reel-scraper`).
    """
    if not token:
        raise ApifyError("apify_token_missing")

    url = f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items"
    params = {"token": token}
    backoff = [2, 8]
    last_exc: Exception | None = None

    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        for attempt in range(max_retries):
            try:
                r = await client.post(url, params=params, json=input_body)
                if r.status_code in (200, 201):
                    try:
                        data = r.json()
                    except Exception as e:
                        raise ApifyError(f"invalid_json: {e}")
                    if not isinstance(data, list):
                        raise ApifyError(
                            f"unexpected_shape: {type(data).__name__}"
                        )
                    return data
                if r.status_code in (401, 403):
                    raise ApifyError(
                        f"auth_failed: {r.status_code}: {r.text[:200]}"
                    )
                if r.status_code == 404:
                    raise ApifyError(f"actor_not_found: {actor_id}")
                if 400 <= r.status_code < 500:
                    raise ApifyError(f"{r.status_code}: {r.text[:200]}")
                if 500 <= r.status_code < 600:
                    last_exc = ApifyTransientError(
                        f"{r.status_code}: {r.text[:200]}"
                    )
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as e:
                last_exc = ApifyTransientError(f"{type(e).__name__}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(backoff[attempt])

    if last_exc:
        raise last_exc
    raise ApifyError("unknown_error")
