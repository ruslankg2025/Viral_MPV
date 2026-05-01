"""Auth-зависимость для POST /api/insights/*.

Паттерн как в Modules/script/auth.py — header X-Worker-Token, FastAPI
Depends. Семантика:
  - INSIGHTS_WRITE_TOKEN не задан → 503 (write API выключен)
  - заголовок отсутствует / не совпадает → 401
"""
from __future__ import annotations

import os

from fastapi import Header, HTTPException, status


async def require_insights_write_token(
    x_worker_token: str = Header(default=""),
) -> None:
    expected = os.getenv("INSIGHTS_WRITE_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="insights_write_disabled",
        )
    if x_worker_token != expected:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="invalid_or_missing_worker_token",
        )
