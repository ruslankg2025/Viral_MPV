from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from logging_setup import get_logger, setup_logging
from router import admin_router, router
from seed import load_example_account, load_taxonomy
from state import state
from storage import ProfileStore

setup_logging()
log = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()

    state.settings = settings
    state.profile_store = ProfileStore(settings.db_dir / "profile.db")

    # Seed taxonomy (идемпотентно)
    added = load_taxonomy(state.profile_store)
    if added:
        log.info("taxonomy_seeded", count=added)

    # Опциональный seed example аккаунта
    if settings.bootstrap_example:
        created = load_example_account(state.profile_store)
        log.info("example_account_seeded", created=created)

    log.info("profile_startup", db_dir=str(settings.db_dir))
    yield
    log.info("profile_shutdown")


app = FastAPI(title="profile", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(router)
app.include_router(admin_router)


@app.get("/profile/healthz")
async def healthz():
    return {"status": "ok"}
