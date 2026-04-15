from contextlib import asynccontextmanager

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from admin_keys import router as admin_keys_router
from admin_templates import router as admin_templates_router
from config import Settings, get_settings
from logging_setup import get_logger, setup_logging
from router import router as script_router
from state import state
from storage import VersionStore
from templates import TemplateStore, bootstrap_builtin_templates
from viral_llm.keys.bootstrap import LLMBootstrapConfig, bootstrap_from_config
from viral_llm.keys.crypto import KeyCrypto
from viral_llm.keys.store import KeyStore

setup_logging()
log = get_logger()


def llm_bootstrap_config(settings: Settings) -> LLMBootstrapConfig:
    """Собрать конфиг для viral_llm.keys.bootstrap из script Settings."""
    return LLMBootstrapConfig(
        anthropic_api_key=settings.bootstrap_anthropic_api_key,
        openai_api_key=settings.bootstrap_openai_api_key,
    )


def _ensure_encryption_key(settings: Settings) -> str:
    """В dev-режиме генерируем Fernet-ключ если не задан — чтобы контейнер не падал."""
    if settings.script_key_encryption_key:
        return settings.script_key_encryption_key
    # Для dev удобнее сгенерировать эпемерный ключ. В prod нужно явно задать.
    log.warning("script_key_encryption_key_missing_using_ephemeral")
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
    created = bootstrap_builtin_templates(state.template_store)
    if created:
        log.info("templates_bootstrapped", count=created)

    state.version_store = VersionStore(settings.db_dir / "scripts.db")

    log.info(
        "script_startup",
        db_dir=str(settings.db_dir),
        fake_llm=settings.script_fake_llm,
        default_provider=settings.default_text_provider,
    )
    try:
        yield
    finally:
        log.info("script_shutdown")


app = FastAPI(
    title="viral-script",
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


# Healthz должен быть объявлен ДО include_router(script_router), иначе параметрический
# роут /script/{version_id} перехватит запросы /script/healthz первым (FastAPI matches
# routes in definition order).
@app.get("/script/healthz")
async def healthz():
    settings = get_settings()
    active_keys = state.key_store.count_active() if state.key_store else {}
    return {
        "status": "ok",
        "db_dir": str(settings.db_dir),
        "fake_llm": settings.script_fake_llm,
        "default_provider": settings.default_text_provider,
        "active_keys_vision": active_keys.get("vision", 0),
    }


app.include_router(script_router)
app.include_router(admin_keys_router)
app.include_router(admin_templates_router)
