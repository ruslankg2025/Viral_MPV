"""Хранилище готовых (смонтированных) роликов — вкладка «Ролики» AI-студии.

Отдельная сущность от runs: это авторские видео, загруженные пользователем
после съёмки/монтажа, а не результат пайплайна. Файлы лежат на диске
(REELS_DIR/{id}/), метаданные — в SQLite. Владелец (account_id) обязателен —
ролики приватны для аккаунта.
"""
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS reels (
    id                TEXT PRIMARY KEY,
    account_id        TEXT,
    title             TEXT,
    note              TEXT,
    video_filename    TEXT NOT NULL,
    thumb_filename    TEXT,
    content_type      TEXT,
    size_bytes        INTEGER,
    duration_sec      REAL,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reels_account_created ON reels(account_id, created_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReelStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(r) if r is not None else None

    def create(
        self,
        *,
        account_id: str | None,
        title: str,
        note: str | None,
        video_filename: str,
        thumb_filename: str | None,
        content_type: str | None,
        size_bytes: int | None,
        duration_sec: float | None = None,
        reel_id: str | None = None,
    ) -> dict[str, Any]:
        # reel_id можно передать заранее — файлы кладутся в папку {id}/ до вставки.
        rid = reel_id or uuid.uuid4().hex
        now = _now()
        with self._conn() as c:
            c.execute(
                "INSERT INTO reels (id, account_id, title, note, video_filename, "
                "thumb_filename, content_type, size_bytes, duration_sec, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rid, account_id, title, note, video_filename, thumb_filename,
                 content_type, size_bytes, duration_sec, now),
            )
        return self.get(rid)  # type: ignore[return-value]

    def get(self, reel_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM reels WHERE id=?", (reel_id,)).fetchone()
        return self._row(r)

    def list_by_account(
        self, account_id: str | None, limit: int = 200
    ) -> list[dict[str, Any]]:
        with self._conn() as c:
            if account_id is None:
                rows = c.execute(
                    "SELECT * FROM reels ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM reels WHERE account_id=? ORDER BY created_at DESC LIMIT ?",
                    (account_id, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, reel_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM reels WHERE id=?", (reel_id,))
            return cur.rowcount > 0
