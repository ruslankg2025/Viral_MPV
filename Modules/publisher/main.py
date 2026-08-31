import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from admin import router as admin_router
from config import Settings, get_settings
from logging_setup import get_logger, setup_logging
from router import router as publisher_router
from scheduler import run_scheduler
from state import state
from storage import PublicationStore

setup_logging()
log = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = get_settings()
    settings.ensure_dirs()
    state.settings = settings
    state.publication_store = PublicationStore(settings.db_path)

    # Фоновая очередь отложенных публикаций (раз в минуту).
    scheduler_task = asyncio.create_task(
        run_scheduler(state.publication_store, settings)
    )

    log.info(
        "publisher_startup",
        db_dir=str(settings.db_dir),
        default_dry_run=settings.default_dry_run,
        vk_token_set=bool(settings.vk_access_token),
    )
    try:
        yield
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        log.info("publisher_shutdown")


app = FastAPI(title="viral-publisher", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# Healthz объявлен ДО include_router (параметрический /publisher/{...} иначе
# перехватит /publisher/healthz — FastAPI матчит роуты по порядку).
@app.get("/publisher/healthz")
async def healthz():
    settings = get_settings()
    store = state.publication_store
    total = len(store.query(limit=1000)) if store else 0
    return {
        "status": "ok",
        "db_dir": str(settings.db_dir),
        "default_dry_run": settings.default_dry_run,
        "publications": total,
    }


app.include_router(publisher_router)
app.include_router(admin_router)
