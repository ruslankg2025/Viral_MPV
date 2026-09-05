# DUP: аналог Modules/publisher/auth.py со своим токеном.
from fastapi import Header, HTTPException, status

from config import get_settings


async def require_worker_token(x_worker_token: str = Header(default="")) -> None:
    expected = get_settings().montage_token
    if not expected or x_worker_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_or_missing_worker_token",
        )


def account_from_header(x_account_id: str = Header(default="")) -> str | None:
    """account_id прокидывает shell-прокси (он держит worker-токен). Пусто →
    режим без enforce (dev/локально) — фильтр по владельцу не применяется."""
    return x_account_id.strip() or None
