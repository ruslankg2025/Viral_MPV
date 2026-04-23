"""
Shell = статический фронт (admin UI на `/` + consumer UI на `/app/`)
       + gateway/BFF: `/api/<module>/*` → внутренний модуль с серверной подстановкой токена.

Admin-эндпоинты (напр. `/profile/seed`, `/monitor/admin/*`) НЕ проксируются
на consumer-origin — требуют X-Admin-Token, который остаётся у admin UI.
"""
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="viral-shell", version="0.1.0")

# ---------------------------------------------------------------- #
# Gateway config (env-driven)
# ---------------------------------------------------------------- #

PROFILE_URL = os.getenv("PROFILE_URL", "http://profile:8000").rstrip("/")
PROFILE_TOKEN = os.getenv("PROFILE_TOKEN", "dev-token-change-me")

MONITOR_URL = os.getenv("MONITOR_URL", "http://monitor:8000").rstrip("/")
MONITOR_TOKEN = os.getenv("MONITOR_TOKEN", "dev-token-change-me")

# Hop-by-hop заголовки httpx/starlette — не пропускать обратно клиенту
_HOP_BY_HOP = {
    "content-encoding", "content-length", "transfer-encoding",
    "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailer", "upgrade",
}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


async def _proxy(
    request: Request,
    path: str,
    upstream_base: str,
    token: str,
    blocked_first_segments: set[str],
) -> Response:
    """Generic reverse proxy with server-side X-Token injection.

    upstream_base: e.g. "http://profile:8000/profile" — path is appended as "/{path}".
    blocked_first_segments: первый segment path, который нельзя проксировать
      с consumer-origin (обычно admin-эндпоинты).
    """
    first_seg = path.split("/", 1)[0] if path else ""
    if first_seg in blocked_first_segments:
        return Response(
            content=b'{"detail":"admin_endpoint_not_proxied"}',
            status_code=403,
            media_type="application/json",
        )

    upstream = f"{upstream_base}/{path}" if path else upstream_base

    headers: dict[str, str] = {"X-Token": token}
    if (ct := request.headers.get("content-type")):
        headers["Content-Type"] = ct
    if (acc := request.headers.get("accept")):
        headers["Accept"] = acc

    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                request.method,
                upstream,
                params=request.query_params,
                content=body,
                headers=headers,
            )
    except httpx.RequestError as exc:
        return Response(
            content=f'{{"detail":"upstream_unreachable","error":"{type(exc).__name__}"}}'.encode(),
            status_code=502,
            media_type="application/json",
        )

    out_headers = {
        k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP
    }
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=out_headers,
    )


# ---------------------------------------------------------------- #
# Profile gateway: /api/profile/* → <PROFILE_URL>/profile/*
# Блокируем: /seed (admin)
# ---------------------------------------------------------------- #

@app.api_route(
    "/api/profile/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy_profile(path: str, request: Request):
    return await _proxy(
        request, path,
        upstream_base=f"{PROFILE_URL}/profile",
        token=PROFILE_TOKEN,
        blocked_first_segments={"seed"},
    )


# ---------------------------------------------------------------- #
# Monitor gateway: /api/monitor/* → <MONITOR_URL>/monitor/*
# Блокируем: /admin/* (требует X-Admin-Token)
# ---------------------------------------------------------------- #

@app.api_route(
    "/api/monitor/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy_monitor(path: str, request: Request):
    return await _proxy(
        request, path,
        upstream_base=f"{MONITOR_URL}/monitor",
        token=MONITOR_TOKEN,
        blocked_first_segments={"admin"},
    )


# ---------------------------------------------------------------- #
# Static UIs
#   /        → admin UI
#   /app/    → consumer UI (VIRA Dashboard)
# ---------------------------------------------------------------- #

static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="shell")
