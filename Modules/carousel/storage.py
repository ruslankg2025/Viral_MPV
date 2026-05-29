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
    id          TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    title       TEXT NOT NULL,
    text        TEXT NOT NULL,
    status      TEXT NOT NULL,           -- draft | rendered
    slides_json TEXT NOT NULL,           -- list[SlideModel]
    rendered    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_car_created ON carousels(created_at DESC);
"""


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

    def create(
        self, *, template_id: str, title: str, text: str, slides: list[dict[str, Any]],
    ) -> dict[str, Any]:
        cid = _new_id()
        with self._conn() as c:
            c.execute(
                "INSERT INTO carousels (id, template_id, title, text, status, slides_json, rendered, created_at) "
                "VALUES (?, ?, ?, ?, 'draft', ?, 0, ?)",
                (cid, template_id, title, text, json.dumps(slides, ensure_ascii=False), _now()),
            )
        return self.get(cid)

    def get(self, cid: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM carousels WHERE id=?", (cid,)).fetchone()
        return self._row(row) if row else None

    def list_all(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM carousels ORDER BY created_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [self._row(r) for r in rows]

    def update_slides(self, cid: str, slides: list[dict[str, Any]]) -> dict[str, Any] | None:
        with self._conn() as c:
            c.execute(
                "UPDATE carousels SET slides_json=?, rendered=0, status='draft' WHERE id=?",
                (json.dumps(slides, ensure_ascii=False), cid),
            )
        return self.get(cid)

    def mark_rendered(self, cid: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE carousels SET rendered=1, status='rendered' WHERE id=?", (cid,))

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
