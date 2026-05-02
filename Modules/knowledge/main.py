"""knowledge-сервис: RAG для VIRA — пользователь грузит PDF/MD/TXT,
сценарист подмешивает релевантные куски в prompt при генерации.
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI

from embeddings import OpenAIEmbeddings
from router import router as knowledge_router
from state import state
from storage import KnowledgeStore


structlog.configure(
    processors=[structlog.processors.JSONRenderer()],
    logger_factory=structlog.stdlib.LoggerFactory(),
)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_dir = Path(os.getenv("DB_DIR", "/db"))
    db_dir.mkdir(parents=True, exist_ok=True)
    state.store = KnowledgeStore(db_dir / "knowledge.db")
    state.embeddings = OpenAIEmbeddings(
        api_key=os.getenv("OPENAI_API_KEY", "").strip() or None,
    )
    log.info(
        "knowledge_startup",
        db=str(db_dir / "knowledge.db"),
        embeddings_configured=state.embeddings.configured,
    )
    try:
        yield
    finally:
        log.info("knowledge_shutdown")


app = FastAPI(title="viral-knowledge", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "embeddings_configured": (state.embeddings is not None
                                   and state.embeddings.configured),
    }


app.include_router(knowledge_router)
