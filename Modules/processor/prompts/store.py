"""Prompt Registry v2 — SQLite-хранилище версионированных промптов.

Поведение:
  - Таблица `prompts(id, name, version, body, is_active, metadata_json, created_at)`
    с UNIQUE(name, version).
  - На первом старте, если таблица пуста, заливает встроенные промпты из
    `prompts.BUILTIN_PROMPTS` как `v1` с `is_active=1`.
  - `get(name, version=None)` — если version не задан, вернёт активную версию;
    если задан — конкретную.
  - Активную версию нельзя удалить. Активация новой версии автоматически
    деактивирует предыдущую активную с тем же `name` (инвариант: для каждого
    `name` активна ровно одна версия).
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prompts import PromptRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS prompts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    version        TEXT NOT NULL,
    body           TEXT NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 0,
    metadata_json  TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE(name, version)
);
CREATE INDEX IF NOT EXISTS idx_prompts_name_active ON prompts(name, is_active);
"""


class PromptStore:
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

    # --- public API --------------------------------------------------------

    def get(self, name: str, version: str | None = None) -> PromptRecord | None:
        """Возвращает запись промпта. Если version=None — активную версию."""
        with self._conn() as c:
            if version is None:
                row = c.execute(
                    "SELECT * FROM prompts WHERE name=? AND is_active=1 ORDER BY id DESC LIMIT 1",
                    (name,),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT * FROM prompts WHERE name=? AND version=?",
                    (name, version),
                ).fetchone()
        if row is None:
            return None
        return PromptRecord(
            name=row["name"],
            version=row["version"],
            body=row["body"],
            is_active=bool(row["is_active"]),
        )

    def list_all(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, name, version, is_active, created_at FROM prompts ORDER BY name, id"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["is_active"] = bool(d["is_active"])
            out.append(d)
        return out

    def list_versions(self, name: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, name, version, body, is_active, metadata_json, created_at "
                "FROM prompts WHERE name=? ORDER BY id",
                (name,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["metadata"] = json.loads(d.pop("metadata_json") or "null")
            d["is_active"] = bool(d["is_active"])
            out.append(d)
        return out

    def create(
        self,
        name: str,
        version: str,
        body: str,
        metadata: dict[str, Any] | None = None,
        is_active: bool = False,
    ) -> dict[str, Any]:
        with self._conn() as c:
            # Если есть конфликт (name, version) — ошибка наверх
            c.execute(
                "INSERT INTO prompts (name, version, body, is_active, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    name,
                    version,
                    body,
                    1 if is_active else 0,
                    json.dumps(metadata) if metadata else None,
                    _now(),
                ),
            )
            if is_active:
                self._deactivate_others(c, name, version)
        return self.get_raw(name, version)

    def activate(self, name: str, version: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT id FROM prompts WHERE name=? AND version=?", (name, version)
            ).fetchone()
            if row is None:
                return None
            c.execute(
                "UPDATE prompts SET is_active=1 WHERE name=? AND version=?",
                (name, version),
            )
            self._deactivate_others(c, name, version)
        return self.get_raw(name, version)

    def delete(self, name: str, version: str) -> bool:
        """Удаляет версию. Активную версию удалить нельзя."""
        with self._conn() as c:
            row = c.execute(
                "SELECT is_active FROM prompts WHERE name=? AND version=?",
                (name, version),
            ).fetchone()
            if row is None:
                return False
            if row["is_active"]:
                raise ValueError("cannot_delete_active_version")
            c.execute("DELETE FROM prompts WHERE name=? AND version=?", (name, version))
        return True

    def get_raw(self, name: str, version: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM prompts WHERE name=? AND version=?", (name, version)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["metadata"] = json.loads(d.pop("metadata_json") or "null")
        d["is_active"] = bool(d["is_active"])
        return d

    def count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _deactivate_others(
        c: sqlite3.Connection, name: str, keep_version: str
    ) -> None:
        c.execute(
            "UPDATE prompts SET is_active=0 WHERE name=? AND version<>?",
            (name, keep_version),
        )


def bootstrap_builtin_prompts(store: PromptStore) -> int:
    """Первичная миграция: встроенные константы → записи v1 в БД.

    Идемпотентна: если для `name` уже есть хотя бы одна запись, пропускаем.
    Возвращает число созданных записей.
    """
    from prompts import BUILTIN_PROMPTS

    created = 0
    for name, body in BUILTIN_PROMPTS.items():
        existing = store.list_versions(name)
        if existing:
            continue
        store.create(
            name=name,
            version="v1",
            body=body,
            metadata={"source": "builtin", "migrated": True},
            is_active=True,
        )
        created += 1
    return created
