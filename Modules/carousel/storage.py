"""SQLite-хранилища carousel: шаблоны (подложки) и карусели."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


TEMPLATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    background_path TEXT,                 -- путь к чистой подложке (относительно media)
    layout_json     TEXT,                 -- override раскладки/стиля (NULL → дефолт)
    is_default      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tpl_default ON templates(is_default);
"""

CAROUSEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS carousels (
    id           TEXT PRIMARY KEY,
    account_id   TEXT,                    -- привязка к аккаунту (NULL → ничьё/legacy)
    template_id  TEXT NOT NULL,
    title        TEXT NOT NULL,
    text         TEXT NOT NULL,
    status       TEXT NOT NULL,           -- draft | ready | published
    content_type TEXT NOT NULL DEFAULT 'carousel',
    slides_json  TEXT NOT NULL,           -- list[SlideModel]
    rendered     INTEGER NOT NULL DEFAULT 0,
    published_at TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_car_created ON carousels(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_car_account ON carousels(account_id, status, created_at DESC);
"""

# Колонки, добавленные после первой версии (для миграции существующих БД).
_CAROUSEL_MIGRATIONS = {
    "account_id": "ALTER TABLE carousels ADD COLUMN account_id TEXT",
    "content_type": "ALTER TABLE carousels ADD COLUMN content_type TEXT NOT NULL DEFAULT 'carousel'",
    "published_at": "ALTER TABLE carousels ADD COLUMN published_at TEXT",
}


class _Base:
    def __init__(self, db_path: Path, schema: str):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(schema)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn


class TemplateStore(_Base):
    def __init__(self, db_path: Path):
        super().__init__(db_path, TEMPLATE_SCHEMA)

    def create(
        self, *, name: str, background_path: str | None,
        layout: dict[str, Any] | None = None, is_default: bool = False,
    ) -> dict[str, Any]:
        tid = _new_id()
        with self._conn() as c:
            if is_default:
                c.execute("UPDATE templates SET is_default=0")
            c.execute(
                "INSERT INTO templates (id, name, background_path, layout_json, is_default, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    tid, name, background_path,
                    json.dumps(layout, ensure_ascii=False) if layout else None,
                    1 if is_default else 0, _now(),
                ),
            )
        return self.get(tid)

    def get(self, tid: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM templates WHERE id=?", (tid,)).fetchone()
        return self._row(row) if row else None

    def get_default(self) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM templates WHERE is_default=1 ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                row = c.execute(
                    "SELECT * FROM templates ORDER BY created_at LIMIT 1"
                ).fetchone()
        return self._row(row) if row else None

    def list_all(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM templates ORDER BY created_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def set_background(self, tid: str, background_path: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE templates SET background_path=? WHERE id=?", (background_path, tid))

    def delete(self, tid: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM templates WHERE id=?", (tid,))
            return cur.rowcount > 0

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        lj = d.pop("layout_json", None)
        d["layout"] = json.loads(lj) if lj else None
        d["is_default"] = bool(d.get("is_default"))
        return d


class CarouselStore(_Base):
    def __init__(self, db_path: Path):
        super().__init__(db_path, CAROUSEL_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        with self._conn() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(carousels)").fetchall()}
            for col, ddl in _CAROUSEL_MIGRATIONS.items():
                if col not in cols:
                    c.execute(ddl)
            # legacy: старый статус 'rendered' → 'draft'
            c.execute("UPDATE carousels SET status='draft' WHERE status='rendered'")

    def create(
        self, *, account_id: str | None, template_id: str, title: str, text: str,
        slides: list[dict[str, Any]],
    ) -> dict[str, Any]:
        cid = _new_id()
        with self._conn() as c:
            c.execute(
                "INSERT INTO carousels "
                "(id, account_id, template_id, title, text, status, content_type, slides_json, rendered, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'draft', 'carousel', ?, 0, ?)",
                (cid, account_id, template_id, title, text,
                 json.dumps(slides, ensure_ascii=False), _now()),
            )
        return self.get(cid)

    def get(self, cid: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM carousels WHERE id=?", (cid,)).fetchone()
        return self._row(row) if row else None

    def query(
        self, *, account_id: str | None = None, status: str | None = None, limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if account_id:
            clauses.append("account_id = ?")
            params.append(account_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM carousels {where} ORDER BY created_at DESC LIMIT ?", params
            ).fetchall()
        return [self._row(r) for r in rows]

    def update_slides(self, cid: str, slides: list[dict[str, Any]]) -> dict[str, Any] | None:
        # Правка текста возвращает в черновик (но размещённую не трогаем).
        with self._conn() as c:
            c.execute(
                "UPDATE carousels SET slides_json=?, rendered=0, "
                "status=CASE WHEN status='published' THEN status ELSE 'draft' END WHERE id=?",
                (json.dumps(slides, ensure_ascii=False), cid),
            )
        return self.get(cid)

    def set_meta(
        self, cid: str, *, status: str | None = None, title: str | None = None,
    ) -> dict[str, Any] | None:
        sets, params = [], []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
            if status == "published":
                sets.append("published_at = ?")
                params.append(_now())
        if title is not None:
            sets.append("title = ?")
            params.append(title)
        if sets:
            params.append(cid)
            with self._conn() as c:
                c.execute(f"UPDATE carousels SET {', '.join(sets)} WHERE id=?", params)
        return self.get(cid)

    def mark_rendered(self, cid: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE carousels SET rendered=1 WHERE id=?", (cid,))

    def delete(self, cid: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM carousels WHERE id=?", (cid,))
            return cur.rowcount > 0

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["slides"] = json.loads(d.pop("slides_json"))
        d["rendered"] = bool(d.get("rendered"))
        return d
