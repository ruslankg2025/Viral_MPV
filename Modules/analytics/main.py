"""analytics-сервис: cross-platform analytics для VIRA.

Читает публикации из publisher по REST (при недоступности — mock-фикстуры),
тянет метрики платформ (Phase 1 — VK), агрегирует на pandas, отдаёт сводки.

Контейнер слушает порт 8000 внутри. Все роуты с префиксом /analytics.
SQLite синхронный (sqlite3 + WAL), async — только httpx и FastAPI-хендлеры.
"""
import asyncio
import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from config import get_settings
from fetcher import build_adapters, fetcher_loop
from publisher_client import PublisherClient
from router import router as analytics_router
from state import state
from storage import AnalyticsStore


structlog.configure(
    processors=[structlog.processors.JSONRenderer()],
    logger_factory=structlog.stdlib.LoggerFactory(),
)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    state.settings = settings

    state.store = AnalyticsStore(settings.db_dir / "analytics.db")

    # publisher-токен: отдельный PUBLISHER_TOKEN или общий ANALYTICS_TOKEN.
    publisher_token = settings.publisher_token or settings.analytics_token
    state.publisher_client = PublisherClient(
        base_url=settings.publisher_url, token=publisher_token,
    )

    # VK access token (если задан — ходим в реальный VK, иначе mock-метрики).
    state.vk_token = os.getenv("VK_TOKEN", "").strip() or None
    adapters = build_adapters(settings, state.vk_token)

    log.info(
        "analytics_startup",
        db=str(settings.db_dir / "analytics.db"),
        publisher_url=settings.publisher_url,
        use_mock=settings.analytics_use_mock,
        vk_token_configured=bool(state.vk_token),
        fetcher=settings.enable_fetcher,
    )

    fetcher_task = None
    if settings.enable_fetcher:
        fetcher_task = asyncio.create_task(
            fetcher_loop(
                store=state.store,
                adapters=adapters,
                client=state.publisher_client,
                settings=settings,
                vk_token=state.vk_token,
            )
        )
    try:
        yield
    finally:
        if fetcher_task is not None:
            fetcher_task.cancel()
            try:
                await fetcher_task
            except asyncio.CancelledError:
                pass
        log.info("analytics_shutdown")


app = FastAPI(title="viral-analytics", version="0.1.0", lifespan=lifespan)


# Healthz объявлен ДО include_router (как в carousel — чтобы параметрический
# /analytics/{...}-маршрут случайно не перехватил /analytics/healthz).
@app.get("/analytics/healthz")
async def healthz():
    s = state.store.stats() if state.store else {}
    return {
        "status": "ok",
        "use_mock": state.settings.analytics_use_mock if state.settings else None,
        **s,
    }


app.include_router(analytics_router)
