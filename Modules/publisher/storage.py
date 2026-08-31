"""SQLite-хранилище publisher: публикации в соцсети (Phase 1 — VK).

Синхронный sqlite3 (НЕ aiosqlite): WAL, isolation_level=None, busy_timeout.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Допустимые платформы / статусы (зафиксированный контракт).
PLATFORMS = ("vk_video", "vk_clips")
STATUSES = ("dry_run", "scheduled", "publishing", "published", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


PUBLICATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS publications (
    id            TEXT PRIMARY KEY,
    platform      TEXT NOT NULL,            -- vk_video | vk_clips
    external_id   TEXT,                     -- id в соцсети (или dry-run-<uuid>)
    video_path    TEXT,                     -- путь к исходному mp4
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    tags_json     TEXT NOT NULL DEFAULT '[]',
    status        TEXT NOT NULL,            -- dry_run|scheduled|publishing|published|failed
    scheduled_at  TEXT,                     -- iso | NULL
    published_at  TEXT,                     -- iso | NULL
    error_message TEXT,                     -- текст ошибки | NULL
    content_hash  TEXT,                     -- хэш заголовок+описание (anti-spam cooldown)
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pub_created ON publications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pub_status ON publications(status, created_at DESC);
"""

# Колонки, добавленные после первой версии (идемпотентная миграция).
_PUBLICATION_MIGRATIONS: dict[str, str] = {
    # Phase 4 anti-spam: хэш контента для content-cooldown.
    "content_hash": "ALTER TABLE publications ADD COLUMN content_hash TEXT",
}


class PublicationStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(PUBLICATION_SCHEMA)
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _migrate(self) -> None:
        with self._conn() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(publications)").fetchall()}
            for col, ddl in _PUBLICATION_MIGRATIONS.items():
                if col not in cols:
                    c.execute(ddl)
            # Индексы по добавленным колонкам создаём ТОЛЬКО после их ALTER.
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_pub_platform_created "
                "ON publications(platform, created_at DESC)"
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_pub_content_hash "
                "ON publications(content_hash, created_at DESC)"
            )

    # ── CRUD ──────────────────────────────────────────────────────────────────
    def create(
        self,
        *,
        platform: str,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        video_path: str | None = None,
        status: str = "scheduled",
        scheduled_at: str | None = None,
        content_hash: str | None = None,
    ) -> dict[str, Any]:
        pid = _new_id()
        with self._conn() as c:
            c.execute(
                "INSERT INTO publications "
                "(id, platform, external_id, video_path, title, description, tags_json, "
                " status, scheduled_at, published_at, error_message, content_hash, created_at) "
                "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
                (
                    pid, platform, video_path, title, description,
                    json.dumps(tags or [], ensure_ascii=False),
                    status, scheduled_at, content_hash, _now(),
                ),
            )
        return self.get(pid)

    def get(self, pid: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM publications WHERE id=?", (pid,)).fetchone()
        return self._row(row) if row else None

    def query(
        self,
        *,
        platform: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if platform:
            clauses.append("platform = ?")
            params.append(platform)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM publications {where} ORDER BY created_at DESC LIMIT ?", params
            ).fetchall()
        return [self._row(r) for r in rows]

    def due_scheduled(self, *, now: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Публикации, у которых наступило время и статус всё ещё 'scheduled'."""
        now = now or _now()
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM publications "
                "WHERE status='scheduled' AND scheduled_at IS NOT NULL AND scheduled_at <= ? "
                "ORDER BY scheduled_at ASC LIMIT ?",
                (now, int(limit)),
            ).fetchall()
        return [self._row(r) for r in rows]

    # ── anti-spam queries (Phase 4) ────────────────────────────────────────────
    def count_recent_by_platform(self, *, platform: str, since_iso: str) -> int:
        """Сколько публикаций на платформе создано не раньше since_iso.

        Считаем по created_at (момент постановки/публикации). Учитываем только
        не-провальные записи — failed/dry-run спамом не считаем для rate-cap?
        Нет: для rate-cap важна именно интенсивность попыток на площадке, поэтому
        учитываем все статусы, кроме явно провальных.
        """
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM publications "
                "WHERE platform = ? AND created_at >= ? AND status != 'failed'",
                (platform, since_iso),
            ).fetchone()
        return int(row["n"]) if row else 0

    def recent_content_hashes(self, *, since_iso: str) -> list[str]:
        """Хэши контента публикаций, созданных не раньше since_iso (не failed)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT DISTINCT content_hash FROM publications "
                "WHERE content_hash IS NOT NULL AND created_at >= ? "
                "AND status != 'failed'",
                (since_iso,),
            ).fetchall()
        return [r["content_hash"] for r in rows]

    def set_status(
        self,
        pid: str,
        *,
        status: str,
        external_id: str | None = None,
        error_message: str | None = None,
        mark_published: bool = False,
    ) -> dict[str, Any] | None:
        sets, params = ["status = ?"], [status]
        if external_id is not None:
            sets.append("external_id = ?")
            params.append(external_id)
        # error_message всегда перезаписываем (None очищает прежнюю ошибку).
        sets.append("error_message = ?")
        params.append(error_message)
        if mark_published:
            sets.append("published_at = ?")
            params.append(_now())
        params.append(pid)
        with self._conn() as c:
            c.execute(f"UPDATE publications SET {', '.join(sets)} WHERE id=?", params)
        return self.get(pid)

    def delete(self, pid: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM publications WHERE id=?", (pid,))
            return cur.rowcount > 0

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["tags"] = json.loads(d.pop("tags_json") or "[]")
        return d
