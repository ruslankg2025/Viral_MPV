import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI

from cache.store import CacheStore
from config import get_settings
from jobs.queue import JobQueue
from jobs.router import router as jobs_router
from jobs.store import JobStore
from keys.bootstrap import bootstrap_from_env
from keys.crypto import KeyCrypto
from keys.router import router as admin_router
from keys.store import KeyStore
from logging_setup import get_logger, setup_logging
from state import state
from tasks.handlers import stub_handler

setup_logging()
log = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()

    state.settings = settings
    state.job_store = JobStore(settings.db_dir / "jobs.db")
    state.cache_store = CacheStore(settings.db_dir / "cache.db")

    crypto = KeyCrypto(settings.processor_key_encryption_key)
    state.key_store = KeyStore(settings.db_dir / "keys.db", crypto)
    bootstrap_from_env(settings, state.key_store)

    handlers = {
        "transcribe": stub_handler,
        "extract_frames": stub_handler,
        "vision_analyze": stub_handler,
        "full_analysis": stub_handler,
    }
    state.queue = JobQueue(
        store=state.job_store,
        limits={
            "transcribe": settings.max_concurrent_transcribe,
            "vision": settings.max_concurrent_vision,
            "frames": 2,
            "full": 2,
            "default": 1,
        },
        handlers=handlers,
    )
    await state.queue.start()

    log.info(
        "processor_startup",
        media_dir=str(settings.media_dir),
        db_dir=str(settings.db_dir),
        test_ui_enabled=settings.test_ui_enabled,
    )
    try:
        yield
    finally:
        await state.queue.stop()
        log.info("processor_shutdown")


app = FastAPI(
    title="video-processor",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(jobs_router)
app.include_router(admin_router)


@app.get("/healthz")
async def healthz():
    settings = get_settings()
    ffmpeg_path = shutil.which("ffmpeg")
    disk = None
    try:
        disk = shutil.disk_usage(settings.media_dir)
    except Exception:
        pass

    queue_stats = state.queue.stats() if hasattr(state, "queue") and state.queue else {
        "queue_depth": 0,
        "active_jobs": 0,
    }
    cache_size = state.cache_store.size() if hasattr(state, "cache_store") else 0
    counts = state.job_store.count_by_status() if hasattr(state, "job_store") else {}
    active_keys = (
        state.key_store.count_active() if state.key_store is not None else {}
    )

    return {
        "status": "ok",
        "ffmpeg_available": bool(ffmpeg_path),
        "media_dir": str(settings.media_dir),
        "db_dir": str(settings.db_dir),
        "disk_free_gb": round(disk.free / 1024**3, 2) if disk else None,
        "queue_depth": queue_stats.get("queue_depth", 0),
        "active_jobs": queue_stats.get("active_jobs", 0),
        "cache_size": cache_size,
        "jobs_by_status": counts,
        "active_keys": {
            "transcription": active_keys.get("transcription", 0),
            "vision": active_keys.get("vision", 0),
        },
    }
