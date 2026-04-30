"""
Shell = статический фронт (admin UI на `/` + consumer UI на `/app/`)
       + gateway/BFF: `/api/<module>/*` → внутренний модуль с серверной подстановкой токена.
       + orchestrator: `/api/orchestrator/*` — pipeline download → analyze (→ generate)

Admin-эндпоинты (напр. `/profile/seed`, `/monitor/admin/*`) НЕ проксируются
на consumer-origin — требуют X-Admin-Token, который остаётся у admin UI.
"""
import asyncio
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from orchestrator.cleanup import run_cleanup_loop
from orchestrator.clients.downloader import DownloaderClient
from orchestrator.clients.monitor import MonitorClient
from orchestrator.clients.processor import ProcessorClient
from orchestrator.clients.profile import ProfileClient
from orchestrator.clients.script import ScriptClient
from orchestrator.config import get_orchestrator_settings
from orchestrator.logging_setup import get_logger, setup_logging
from orchestrator.recovery import recover_stalled_runs
from orchestrator.runs.router import router as orchestrator_router
from orchestrator.runs.runner import RunRunner
from orchestrator.runs.store import RunStore
from orchestrator.state import state as orch_state
from media.router import router as media_router

setup_logging()
log = get_logger("shell")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_orchestrator_settings()
    settings.ensure_dirs()

    orch_state.settings = settings
    orch_state.run_store = RunStore(settings.db_dir / "runs.db")

    # Одноразовая миграция: удаляем legacy done/failed runs до Track A2
    # (без strategy-шага в steps_json). Активные runs не трогаются.
    purged_legacy = orch_state.run_store.purge_legacy_runs_without_strategy()
    if purged_legacy:
        log.info("legacy_runs_purged", count=purged_legacy)

    recovered = recover_stalled_runs(
        orch_state.run_store, settings.orchestrator_stalled_timeout_sec
    )

    downloader = DownloaderClient(
        settings.downloader_url,
        settings.downloader_token,
        poll_interval_sec=settings.orchestrator_poll_interval_sec,
    )
    processor = ProcessorClient(
        settings.processor_url,
        settings.processor_token,
        poll_interval_sec=settings.orchestrator_poll_interval_sec,
    )
    orch_state.monitor_client = MonitorClient(
        settings.monitor_url, settings.monitor_token
    )
    profile_client = ProfileClient(settings.profile_url, settings.profile_token)
    script_client = (
        ScriptClient(settings.script_url, settings.script_token)
        if settings.script_url
        else None
    )
    # Сохраняем для on-demand script generation через "Создать аналог"
    orch_state.script_client = script_client
    orch_state.profile_client = profile_client
    orch_state.runner = RunRunner(
        settings, orch_state.run_store, downloader, processor,
        profile=profile_client,
        script=None,  # генерация — отдельный шаг «Создать аналог», не часть «Разобрать»
    )

    log.info(
        "shell_startup",
        downloader_url=settings.downloader_url,
        processor_url=settings.processor_url,
        recovered_stalled_runs=recovered,
    )

    # TTL cleanup для terminal runs — фоновый loop, грейс-shutdown
    cleanup_task = asyncio.create_task(
        run_cleanup_loop(orch_state.run_store, settings),
        name="runs-cleanup",
    )

    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
        # Отменяем все незавершённые run-задачи (важно для изоляции в тестах)
        for task in list(orch_state.runner._tasks):
            task.cancel()
        log.info("shell_shutdown")


app = FastAPI(title="viral-shell", version="0.1.0", lifespan=lifespan)

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
# Orchestrator (pipeline download → analyze → ...)
# Эндпоинты POST /api/orchestrator/runs, GET /api/orchestrator/runs/{id}
# ---------------------------------------------------------------- #

app.include_router(orchestrator_router)

# ---------------------------------------------------------------- #
# Media (frame thumbnails / audio served from /media volume, read-only)
# ---------------------------------------------------------------- #

app.include_router(media_router)


# ---------------------------------------------------------------- #
# Static UIs
#   /        → admin UI
#   /app/    → consumer UI (VIRA Dashboard)
# ---------------------------------------------------------------- #

static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="shell")
