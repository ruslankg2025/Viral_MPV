"""Хранилище обложек (вкладка «Обложки» AI-студии) — две сущности:

- photos: библиотека фото автора (ракурсы/эмоции), база для обложки.
- covers: сохранённые обложки (spec — нормализованная раскладка слоёв + тексты
  + настройки; экспортированные PNG на диске по форматам).

Рендер обложек — на клиенте (canvas), сервер только хранит/отдаёт. Файлы на
диске (COVERS_DIR/{photos,covers}/…), метаданные — SQLite на общем shell_db.
Владелец (account_id) обязателен — обложки приватны. Паттерн повторяет reels.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Целевые форматы экспорта: ключ → (width, height).
FORMATS: dict[str, tuple[int, int]] = {
    "9x16": (1080, 1920),   # reels / stories / shorts / tiktok
    "4x5": (1080, 1350),    # лента IG / подложка карусели
    "1x1": (1080, 1080),    # квадрат
    "16x9": (1920, 1080),   # youtube
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS cover_photos (
    id            TEXT PRIMARY KEY,
    account_id    TEXT,
    filename      TEXT NOT NULL,
    content_type  TEXT,
    size_bytes    INTEGER,
    width         INTEGER,
    height        INTEGER,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cover_photos_acct ON cover_photos(account_id, created_at DESC);

CREATE TABLE IF NOT EXISTS covers (
    id            TEXT PRIMARY KEY,
    account_id    TEXT,
    run_id        TEXT,
    reel_id       TEXT,
    title         TEXT,
    format        TEXT NOT NULL DEFAULT '9x16',
    spec_json     TEXT NOT NULL DEFAULT '{}',
    renders_json  TEXT NOT NULL DEFAULT '{}',   -- {format: filename}
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_covers_acct ON covers(account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_covers_run  ON covers(account_id, run_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CoverStore:
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

    # ── photos ──────────────────────────────────────────────────────────────
    def create_photo(
        self, *, account_id: str | None, filename: str, content_type: str | None,
        size_bytes: int | None, width: int | None = None, height: int | None = None,
        photo_id: str | None = None,
    ) -> dict[str, Any]:
        pid = photo_id or uuid.uuid4().hex
        with self._conn() as c:
            c.execute(
                "INSERT INTO cover_photos (id, account_id, filename, content_type, "
                "size_bytes, width, height, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (pid, account_id, filename, content_type, size_bytes, width, height, _now()),
            )
        return self.get_photo(pid)  # type: ignore[return-value]

    def get_photo(self, photo_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM cover_photos WHERE id=?", (photo_id,)).fetchone()
        return dict(r) if r else None

    def list_photos(self, account_id: str | None, limit: int = 300) -> list[dict[str, Any]]:
        with self._conn() as c:
            if account_id is None:
                rows = c.execute(
                    "SELECT * FROM cover_photos ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM cover_photos WHERE account_id=? ORDER BY created_at DESC LIMIT ?",
                    (account_id, limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def delete_photo(self, photo_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM cover_photos WHERE id=?", (photo_id,))
            return cur.rowcount > 0

    # ── covers ──────────────────────────────────────────────────────────────
    def create_cover(
        self, *, account_id: str | None, title: str | None, run_id: str | None,
        reel_id: str | None, fmt: str, spec: dict[str, Any], cover_id: str | None = None,
    ) -> dict[str, Any]:
        cid = cover_id or uuid.uuid4().hex
        now = _now()
        with self._conn() as c:
            c.execute(
                "INSERT INTO covers (id, account_id, run_id, reel_id, title, format, "
                "spec_json, renders_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,'{}',?,?)",
                (cid, account_id, run_id, reel_id, title, fmt,
                 json.dumps(spec, ensure_ascii=False), now, now),
            )
        return self.get_cover(cid)  # type: ignore[return-value]

    def get_cover(self, cover_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM covers WHERE id=?", (cover_id,)).fetchone()
        return self._cover_row(r) if r else None

    def list_covers(
        self, account_id: str | None, *, run_id: str | None = None, limit: int = 300
    ) -> list[dict[str, Any]]:
        clauses, args = [], []
        if account_id is not None:
            clauses.append("account_id=?")
            args.append(account_id)
        if run_id is not None:
            clauses.append("run_id=?")
            args.append(run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(limit)
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM covers {where} ORDER BY created_at DESC LIMIT ?", args
            ).fetchall()
        return [self._cover_row(r) for r in rows]

    def update_cover(
        self, cover_id: str, *, title: str | None = None, fmt: str | None = None,
        spec: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        sets, args = [], []
        if title is not None:
            sets.append("title=?"); args.append(title)
        if fmt is not None:
            sets.append("format=?"); args.append(fmt)
        if spec is not None:
            sets.append("spec_json=?"); args.append(json.dumps(spec, ensure_ascii=False))
        if not sets:
            return self.get_cover(cover_id)
        sets.append("updated_at=?"); args.append(_now())
        args.append(cover_id)
        with self._conn() as c:
            c.execute(f"UPDATE covers SET {', '.join(sets)} WHERE id=?", args)
        return self.get_cover(cover_id)

    def set_render(self, cover_id: str, fmt: str, filename: str) -> None:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT renders_json FROM covers WHERE id=?", (cover_id,)).fetchone()
            if row is None:
                c.execute("ROLLBACK"); return
            renders = json.loads(row["renders_json"] or "{}")
            renders[fmt] = filename
            c.execute(
                "UPDATE covers SET renders_json=?, updated_at=? WHERE id=?",
                (json.dumps(renders, ensure_ascii=False), _now(), cover_id),
            )
            c.execute("COMMIT")

    def delete_cover(self, cover_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM covers WHERE id=?", (cover_id,))
            return cur.rowcount > 0

    @staticmethod
    def _cover_row(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["spec"] = json.loads(d.pop("spec_json") or "{}")
        d["renders"] = json.loads(d.pop("renders_json") or "{}")
        return d
