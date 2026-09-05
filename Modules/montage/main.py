import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import Settings, get_settings
from logging_setup import get_logger, setup_logging
from router import router as montage_router
from state import state
from storage import JobStore
from worker import mem_available_mb, run_worker

setup_logging()
log = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = get_settings()
    settings.ensure_dirs()
    state.settings = settings
    state.job_store = JobStore(settings.db_path)

    worker_task = asyncio.create_task(run_worker(state.job_store, settings))

    log.info("montage_startup", work_dir=str(settings.work_dir),
             openmontage_root=str(settings.openmontage_root),
             night=[settings.night_start_hour, settings.night_end_hour],
             min_avail_mb=settings.min_avail_mb)
    try:
        yield
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        log.info("montage_shutdown")


app = FastAPI(title="viral-montage", version="0.1.0", lifespan=lifespan)


# Healthz объявлен ДО include_router (иначе параметрический /montage/{...} его
# перехватит — FastAPI матчит роуты по порядку). Без auth (для healthcheck).
@app.get("/montage/healthz")
async def healthz():
    settings = get_settings()
    store = state.job_store
    return {
        "status": "ok",
        "work_dir": str(settings.work_dir),
        "avail_mb": mem_available_mb(),
        "min_avail_mb": settings.min_avail_mb,
        "queued": store.count(status="queued") if store else 0,
        "running": store.count(status="running") if store else 0,
    }


app.include_router(montage_router)
