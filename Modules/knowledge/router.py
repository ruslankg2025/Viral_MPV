"""FastAPI router для knowledge-сервиса (RAG)."""
from __future__ import annotations

from typing import Any

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, UploadFile, status,
)
from pydantic import BaseModel, Field

from auth import require_worker_token
from embeddings import EmbeddingsError
from parser import chunk_text, extract_text
from state import state


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _store():
    if state.store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "store_not_ready")
    return state.store


def _embeddings():
    if state.embeddings is None or not state.embeddings.configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "embeddings_not_configured: set OPENAI_API_KEY",
        )
    return state.embeddings


@router.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "embeddings_configured": (state.embeddings is not None
                                   and state.embeddings.configured),
        "embedding_model": (state.embeddings.model
                            if state.embeddings else None),
    }


# ────────────────────────────────────────────────────────────────────
# Upload / list / delete
# ────────────────────────────────────────────────────────────────────

@router.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_worker_token)],
)
async def upload(
    account_id: str = Form(..., min_length=1),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Загружает документ, парсит, чанкует, embed-ит, сохраняет."""
    store = _store()
    embed = _embeddings()

    content = await file.read()
    if not content:
        raise HTTPException(400, "empty_file")
    if len(content) > 20 * 1024 * 1024:  # 20MB hard cap
        raise HTTPException(413, "file_too_large_max_20mb")

    try:
        text = extract_text(content, file.content_type, file.filename or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not text.strip():
        raise HTTPException(400, "extracted_text_empty")

    chunks = list(chunk_text(text, max_tokens=500, overlap_tokens=50))
    if not chunks:
        raise HTTPException(400, "no_chunks_extracted")

    chunk_texts = [t for t, _ in chunks]
    try:
        vectors = await embed.embed_batch(chunk_texts)
    except EmbeddingsError as e:
        raise HTTPException(502, f"embeddings_failed: {e}")

    if len(vectors) != len(chunks):
        raise HTTPException(502, "embeddings_count_mismatch")

    doc_id = store.create_document(
        account_id=account_id,
        filename=file.filename or "untitled",
        content_type=file.content_type,
        size_bytes=len(content),
    )
    saved = store.insert_chunks(
        doc_id=doc_id,
        account_id=account_id,
        chunks=[(t, v, tc) for (t, tc), v in zip(chunks, vectors)],
        embedding_dim=embed.dim,
    )
    return {
        "id": doc_id,
        "filename": file.filename,
        "chunks": saved,
        "size_bytes": len(content),
    }


@router.get("/documents")
async def list_documents(account_id: str) -> list[dict[str, Any]]:
    return _store().list_documents(account_id)


@router.delete(
    "/documents/{doc_id}",
    dependencies=[Depends(require_worker_token)],
)
async def delete_document(doc_id: str, account_id: str) -> dict[str, bool]:
    ok = _store().delete_document(doc_id, account_id)
    if not ok:
        raise HTTPException(404, "doc_not_found")
    return {"ok": True}


# ────────────────────────────────────────────────────────────────────
# Query
# ────────────────────────────────────────────────────────────────────

class QueryReq(BaseModel):
    account_id: str
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)


@router.post("/query")
async def query(req: QueryReq) -> dict[str, Any]:
    store = _store()
    embed = _embeddings()
    try:
        q_vec = await embed.embed_one(req.query)
    except EmbeddingsError as e:
        raise HTTPException(502, f"embeddings_failed: {e}")
    chunks = store.query(
        account_id=req.account_id, query_vec=q_vec, top_k=req.top_k,
    )
    return {"chunks": chunks, "model": embed.model}


@router.get("/stats")
async def stats(account_id: str) -> dict[str, Any]:
    return _store().stats(account_id)
