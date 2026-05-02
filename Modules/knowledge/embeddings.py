"""Embeddings provider — обёртка над OpenAI text-embedding-3-small.

Простая реализация на httpx без зависимости от openai-sdk (легче, меньше
deps в knowledge-сервисе). Batches до 100 текстов за один call.
"""
from __future__ import annotations

import asyncio
import os
from typing import Iterable

import httpx


OPENAI_EMBED_URL = "https://api.openai.com/v1/embeddings"
DEFAULT_MODEL = "text-embedding-3-small"
BATCH_SIZE = 100  # OpenAI лимит 2048, но 100 ок и контролирует cost
EMBED_DIM = 1536  # dim для text-embedding-3-small


class EmbeddingsError(RuntimeError):
    pass


class OpenAIEmbeddings:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 30.0,
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "").strip()
        self.model = model
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def dim(self) -> int:
        return EMBED_DIM if self.model == DEFAULT_MODEL else 1536

    async def embed_one(self, text: str) -> list[float]:
        vecs = await self.embed_batch([text])
        if not vecs:
            raise EmbeddingsError("empty_response")
        return vecs[0]

    async def embed_batch(self, texts: Iterable[str]) -> list[list[float]]:
        if not self.api_key:
            raise EmbeddingsError("openai_api_key_not_set")
        items = [t for t in texts if t and t.strip()]
        if not items:
            return []
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for i in range(0, len(items), BATCH_SIZE):
                batch = items[i:i + BATCH_SIZE]
                try:
                    resp = await client.post(
                        OPENAI_EMBED_URL,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json={"model": self.model, "input": batch},
                    )
                except httpx.RequestError as e:
                    raise EmbeddingsError(f"network: {e}") from e
                if resp.status_code != 200:
                    raise EmbeddingsError(
                        f"openai_http_{resp.status_code}: {resp.text[:300]}"
                    )
                try:
                    data = resp.json()
                except Exception as e:  # noqa: BLE001
                    raise EmbeddingsError(f"openai_parse: {e}") from e
                # Гарантируем порядок — sort by 'index'
                rows = sorted(
                    data.get("data") or [], key=lambda r: r.get("index", 0)
                )
                for r in rows:
                    vec = r.get("embedding")
                    if not isinstance(vec, list):
                        raise EmbeddingsError("malformed_embedding_row")
                    out.append([float(x) for x in vec])
        return out
