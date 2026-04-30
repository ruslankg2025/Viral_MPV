import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.admin_keys import router as admin_router
from cache.store import CacheStore
from config import Settings, get_settings
from jobs.queue import JobQueue
from jobs.router import router as jobs_router
from jobs.store import JobStore
from logging_setup import get_logger, setup_logging
from prompts.router import router as prompts_router
from prompts.store import PromptStore, bootstrap_builtin_prompts
from state import state
from tasks.extract_frames import run_extract_frames
from tasks.full_analysis import run_full_analysis
from tasks.analyze_strategy import run_analyze_strategy
from tasks.transcribe import run_transcribe
from tasks.vision_analyze import run_vision_analyze
from ui.router import public_router as ui_public_router
from ui.router import router as ui_files_router
from viral_llm.keys.bootstrap import LLMBootstrapConfig, bootstrap_from_config
from viral_llm.keys.crypto import KeyCrypto
from viral_llm.keys.store import KeyStore

setup_logging()
log = get_logger()


def llm_bootstrap_config(settings: Settings) -> LLMBootstrapConfig:
    """Собрать конфиг для viral_llm.keys.bootstrap из processor Settings."""
    return LLMBootstrapConfig(
        assemblyai_api_key=settings.bootstrap_assemblyai_api_key,
        deepgram_api_key=settings.bootstrap_deepgram_api_key,
        openai_whisper_api_key=settings.bootstrap_openai_whisper_api_key,
        groq_api_key=settings.bootstrap_groq_api_key,
        anthropic_api_key=settings.bootstrap_anthropic_api_key,
        openai_api_key=settings.bootstrap_openai_api_key,
        google_gemini_api_key=settings.bootstrap_google_gemini_api_key,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()

    state.settings = settings
    state.job_store = JobStore(settings.db_dir / "jobs.db")
    state.cache_store = CacheStore(settings.db_dir / "cache.db")

    crypto = KeyCrypto(settings.processor_key_encryption_key)
    state.key_store = KeyStore(settings.db_dir / "keys.db", crypto)
    bootstrap_from_config(llm_bootstrap_config(settings), state.key_store)

    # v2: Prompts Registry
    state.prompt_store = PromptStore(settings.db_dir / "prompts.db")
    migrated = bootstrap_builtin_prompts(state.prompt_store)
    if migrated:
        log.info("prompts_bootstrapped", count=migrated)

    handlers = {
        "transcribe": run_transcribe,
        "extract_frames": run_extract_frames,
        "vision_analyze": run_vision_analyze,
        "full_analysis": run_full_analysis,
        "analyze_strategy": run_analyze_strategy,
    }
    state.queue = JobQueue(
        store=state.job_store,
        limits={
            "transcribe": settings.max_concurrent_transcribe,
            "vision": settings.max_concurrent_vision,
            "frames": 2,
            "full": 2,
            "strategy": 2,
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(jobs_router)
app.include_router(admin_router)
app.include_router(prompts_router)
app.include_router(ui_files_router)
app.include_router(ui_public_router)

# Static UI (опционально отключаемый в prod)
if get_settings().test_ui_enabled:
    ui_static = Path(__file__).parent / "ui" / "static"
    if ui_static.exists():
        app.mount("/ui", StaticFiles(directory=str(ui_static), html=True), name="ui")


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
