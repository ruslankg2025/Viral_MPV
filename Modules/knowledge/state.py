"""Singleton state knowledge-сервиса."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from embeddings import OpenAIEmbeddings
    from storage import KnowledgeStore


class AppState:
    store: "KnowledgeStore | None" = None
    embeddings: "OpenAIEmbeddings | None" = None


state = AppState()
