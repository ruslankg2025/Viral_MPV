import asyncio
import shutil
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from cleanup import run_cleanup_loop
from config import get_settings
from files_router import router as files_router
from jobs.queue import JobQueue
from jobs.router import router as jobs_router
from jobs.store import JobStore
from logging_setup import get_logger, setup_logging
from state import state
from tasks.download import run_download

setup_logging()
log = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()

    state.settings = settings
    state.job_store = JobStore(settings.db_dir / "jobs.db")
    state.queue = JobQueue(
        store=state.job_store,
        max_concurrent=settings.max_concurrent,
        handlers={"download": run_download},
        job_timeout_sec=settings.job_timeout_sec,
    )
    await state.queue.start()
    cleanup_task = asyncio.create_task(
        run_cleanup_loop(state.job_store), name="cleanup-loop"
    )

    fixture_ok = settings.fixture_path.exists() if settings.stub_mode else None
    log.info(
        "downloader_startup",
        media_dir=str(settings.media_dir),
        db_dir=str(settings.db_dir),
        stub_mode=settings.stub_mode,
        fixture_present=fixture_ok,
        max_concurrent=settings.max_concurrent,
    )
    if settings.stub_mode and not fixture_ok:
        log.warning(
            "stub_fixture_missing",
            path=str(settings.fixture_path),
            hint="STUB_MODE=true но фикстура не найдена — все скачки будут падать",
        )
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
        await state.queue.stop()
        log.info("downloader_shutdown")


app = FastAPI(
    title="video-downloader",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(jobs_router)
app.include_router(files_router)


@app.get("/healthz")
async def healthz():
    settings = get_settings()
    disk = None
    try:
        disk = shutil.disk_usage(settings.media_dir)
    except Exception:
        pass

    queue_stats = (
        state.queue.stats()
        if hasattr(state, "queue") and state.queue
        else {"queue_depth": 0, "active_jobs": 0, "max_concurrent": 0}
    )
    counts = state.job_store.count_by_status() if hasattr(state, "job_store") else {}

    return {
        "status": "ok",
        "stub_mode": settings.stub_mode,
        "fixture_present": (
            settings.fixture_path.exists() if settings.stub_mode else None
        ),
        "apify_token_set": bool(settings.apify_token),
        "media_dir": str(settings.media_dir),
        "db_dir": str(settings.db_dir),
        "disk_free_gb": round(disk.free / 1024**3, 2) if disk else None,
        "queue_depth": queue_stats["queue_depth"],
        "active_jobs": queue_stats["active_jobs"],
        "max_concurrent": queue_stats["max_concurrent"],
        "jobs_by_status": counts,
        "ttl_failed_hours": settings.ttl_failed_hours,
    }
