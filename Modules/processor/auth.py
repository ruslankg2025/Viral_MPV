from fastapi import Header, HTTPException, status

from config import get_settings


async def require_worker_token(
    x_worker_token: str | None = Header(default=None),
) -> None:
    settings = get_settings()
    if not x_worker_token or x_worker_token != settings.processor_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_worker_token",
        )


async def require_admin_token(
    x_admin_token: str | None = Header(default=None),
) -> None:
    settings = get_settings()
    if not x_admin_token or x_admin_token != settings.processor_admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_admin_token",
        )
