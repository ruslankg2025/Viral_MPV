"""SQLite-store для knowledge base (PLAN_SELF_LEARNING_AGENT этап 4).

Простая RAG-инфраструктура:
- documents: 1 запись на файл (PDF/MD/TXT), привязан к account_id
- chunks: ~500-token куски с embedding-vector в BLOB
- query: cosine similarity на python, top-K по account-у

Для текущих объёмов (10-100 файлов на пользователя × 100-500 chunks)
производительность ОК без sqlite-vss / FAISS.
"""
from __future__ import annotations

import json
import sqlite3
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _vec_to_blob(vec: list[float]) -> bytes:
    """float32 little-endian — компактно и быстро парсится numpy/struct."""
    return struct.pack(f"<{len(vec)}f", *vec)


def _vec_from_blob(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id            TEXT PRIMARY KEY,
    account_id    TEXT NOT NULL,
    filename      TEXT NOT NULL,
    content_type  TEXT,                  -- 'application/pdf' | 'text/markdown' | ...
    size_bytes    INTEGER,
    chunks_count  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kd_account ON knowledge_documents(account_id, created_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id        TEXT NOT NULL,
    account_id    TEXT NOT NULL,         -- денормализация для быстрого фильтра по тенанту
    chunk_index   INTEGER NOT NULL,      -- порядковый номер в документе
    text          TEXT NOT NULL,
    token_count   INTEGER NOT NULL,
    embedding     BLOB NOT NULL,         -- float32-вектор embedding
    embedding_dim INTEGER NOT NULL,      -- 1536 для text-embedding-3-small
    created_at    TEXT NOT NULL,
    FOREIGN KEY (doc_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_kc_account ON knowledge_chunks(account_id);
CREATE INDEX IF NOT EXISTS idx_kc_doc ON knowledge_chunks(doc_id, chunk_index);
"""


class KnowledgeStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ── Documents ────────────────────────────────────────────────────

    def create_document(
        self, *, account_id: str, filename: str,
        content_type: str | None, size_bytes: int,
    ) -> str:
        doc_id = _new_id()
        with self._conn() as c:
            c.execute(
                """INSERT INTO knowledge_documents
                   (id, account_id, filename, content_type, size_bytes,
                    chunks_count, created_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (doc_id, account_id, filename, content_type, size_bytes, _now()),
            )
        return doc_id

    def list_documents(self, account_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT id, filename, content_type, size_bytes,
                          chunks_count, created_at
                   FROM knowledge_documents
                   WHERE account_id = ?
                   ORDER BY created_at DESC""",
                (account_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_document(self, doc_id: str, account_id: str) -> bool:
        """CASCADE удалит chunks автоматически (FK ON DELETE)."""
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM knowledge_documents WHERE id=? AND account_id=?",
                (doc_id, account_id),
            )
            return cur.rowcount > 0

    # ── Chunks ──────────────────────────────────────────────────────

    def insert_chunks(
        self,
        *,
        doc_id: str,
        account_id: str,
        chunks: list[tuple[str, list[float], int]],  # (text, vector, token_count)
        embedding_dim: int,
    ) -> int:
        """Bulk-вставка chunks. Обновляет chunks_count в documents."""
        if not chunks:
            return 0
        ts = _now()
        rows = [
            (doc_id, account_id, idx, text, token_count,
             _vec_to_blob(vec), embedding_dim, ts)
            for idx, (text, vec, token_count) in enumerate(chunks)
        ]
        with self._conn() as c:
            c.executemany(
                """INSERT INTO knowledge_chunks
                   (doc_id, account_id, chunk_index, text, token_count,
                    embedding, embedding_dim, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            c.execute(
                "UPDATE knowledge_documents SET chunks_count=? WHERE id=?",
                (len(chunks), doc_id),
            )
        return len(chunks)

    def query(
        self, *, account_id: str, query_vec: list[float], top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Возвращает top-K chunks по cosine similarity для аккаунта.

        Грузит ВСЕ chunks этого account-а в память — для small N это ОК.
        """
        with self._conn() as c:
            rows = c.execute(
                """SELECT c.id, c.doc_id, c.chunk_index, c.text, c.embedding,
                          d.filename
                   FROM knowledge_chunks c
                   JOIN knowledge_documents d ON d.id = c.doc_id
                   WHERE c.account_id = ?""",
                (account_id,),
            ).fetchall()
        if not rows:
            return []

        # Cosine: dot(a,b) / (|a| * |b|). query_vec мы нормализуем заранее.
        import math
        q_norm = math.sqrt(sum(x * x for x in query_vec)) or 1.0
        q_unit = [x / q_norm for x in query_vec]

        scored = []
        for r in rows:
            vec = _vec_from_blob(r["embedding"])
            v_norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            dot = sum(a * b for a, b in zip(q_unit, vec))
            score = dot / v_norm
            scored.append((score, r))

        scored.sort(key=lambda t: t[0], reverse=True)
        out = []
        for score, r in scored[:top_k]:
            out.append({
                "chunk_id": r["id"],
                "doc_id": r["doc_id"],
                "filename": r["filename"],
                "chunk_index": r["chunk_index"],
                "text": r["text"],
                "score": round(score, 4),
            })
        return out

    # ── Stats ───────────────────────────────────────────────────────

    def stats(self, account_id: str) -> dict[str, Any]:
        with self._conn() as c:
            row = c.execute(
                """SELECT COUNT(DISTINCT d.id) as docs,
                          COUNT(c.id) as chunks
                   FROM knowledge_documents d
                   LEFT JOIN knowledge_chunks c ON c.doc_id = d.id
                   WHERE d.account_id = ?""",
                (account_id,),
            ).fetchone()
        return {
            "documents": row["docs"] or 0,
            "chunks": row["chunks"] or 0,
        }
