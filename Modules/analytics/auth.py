"""Auth для analytics-сервиса — паттерн как в knowledge/downloader/script."""
import os

from fastapi import Header, HTTPException, status


async def require_worker_token(x_worker_token: str = Header(default="")) -> None:
    expected = os.getenv("ANALYTICS_TOKEN", "").strip()
    if not expected or x_worker_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_or_missing_worker_token",
        )
