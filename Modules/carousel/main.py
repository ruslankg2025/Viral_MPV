from contextlib import asynccontextmanager

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from admin_keys import router as admin_keys_router
from config import Settings, get_settings
from logging_setup import get_logger, setup_logging
from router import router as carousel_router
from state import state
from storage import CarouselStore, CodewordStore, TemplateStore
from viral_llm.keys.bootstrap import LLMBootstrapConfig, bootstrap_from_config
from viral_llm.keys.crypto import KeyCrypto
from viral_llm.keys.store import KeyStore

setup_logging()
log = get_logger()


def llm_bootstrap_config(settings: Settings) -> LLMBootstrapConfig:
    return LLMBootstrapConfig(
        anthropic_api_key=settings.bootstrap_anthropic_api_key,
        openai_api_key=settings.bootstrap_openai_api_key,
    )


def _ensure_encryption_key(settings: Settings) -> str:
    if settings.carousel_key_encryption_key:
        return settings.carousel_key_encryption_key
    log.warning("carousel_key_encryption_key_missing_using_ephemeral")
    return Fernet.generate_key().decode()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    state.settings = settings

    crypto = KeyCrypto(_ensure_encryption_key(settings))
    state.key_store = KeyStore(settings.db_dir / "keys.db", crypto)
    bootstrap_from_config(llm_bootstrap_config(settings), state.key_store)

    state.template_store = TemplateStore(settings.db_dir / "templates.db")
    state.carousel_store = CarouselStore(settings.db_dir / "carousels.db")
    state.codeword_store = CodewordStore(settings.db_dir / "codewords.db")
    seeded = state.codeword_store.seed_if_empty()
    if seeded:
        log.info("codewords_seeded", count=seeded)

    log.info(
        "carousel_startup",
        db_dir=str(settings.db_dir),
        media_dir=str(settings.media_dir),
        fake_llm=settings.carousel_fake_llm,
    )
    try:
        yield
    finally:
        log.info("carousel_shutdown")


app = FastAPI(title="viral-carousel", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# Healthz объявлен ДО include_router (параметрический /carousel/{carousel_id}
# иначе перехватит /carousel/healthz — FastAPI матчит роуты по порядку).
@app.get("/carousel/healthz")
async def healthz():
    settings = get_settings()
    active = state.key_store.count_active() if state.key_store else {}
    return {
        "status": "ok",
        "db_dir": str(settings.db_dir),
        "fake_llm": settings.carousel_fake_llm,
        "templates": len(state.template_store.list_all()) if state.template_store else 0,
        "active_keys_vision": active.get("vision", 0),
    }


app.include_router(carousel_router)
app.include_router(admin_keys_router)
