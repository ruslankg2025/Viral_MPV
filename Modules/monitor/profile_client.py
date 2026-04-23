"""HTTP client для профайл-сервиса (A7). Не критичный, с graceful fallback."""
import httpx

from logging_setup import get_logger

log = get_logger("monitor.profile_client")


async def validate_account(base_url: str, token: str, account_id: str) -> bool:
    """Проверить, существует ли account_id в profile-сервисе.

    Возвращает False при любой ошибке (timeout, connect error, 404, 500).
    Никогда не бросает исключение — критичная зависимость на profile нежелательна.
    """
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(
                f"{base_url}/profile/accounts/{account_id}",
                headers={"X-Token": token},
            )
            return r.status_code == 200
    except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as e:
        log.warning("profile_client_unreachable", error=str(e), account_id=account_id)
        return False
    except Exception as e:
        log.error("profile_client_unexpected", error=str(e), account_id=account_id)
        return False
