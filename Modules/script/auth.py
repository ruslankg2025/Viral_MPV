# DUP: аналог Modules/processor/auth.py со своими токенами
from fastapi import Header, HTTPException, status

from config import get_settings


async def require_worker_token(x_worker_token: str = Header(default="")) -> None:
    expected = get_settings().script_token
    if not expected or x_worker_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_or_missing_worker_token",
        )


async def require_admin_token(x_admin_token: str = Header(default="")) -> None:
    expected = get_settings().script_admin_token
    if not expected or x_admin_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_or_missing_admin_token",
        )
