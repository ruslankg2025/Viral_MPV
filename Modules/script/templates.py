"""Template Store для script — версионированное SQLite-хранилище промптов.

DUP: структура идентична Modules/processor/prompts/store.py. Синхронизировать
при доработке. Выделить в Modules/shared/prompts/ при сборке api-монолита.
"""
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from builtin_templates import BUILTIN_TEMPLATES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS templates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    version        TEXT NOT NULL,
    body           TEXT NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 0,
    metadata_json  TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE(name, version)
);
CREATE INDEX IF NOT EXISTS idx_templates_name_active ON templates(name, is_active);
"""


@dataclass(frozen=True)
class TemplateRecord:
    name: str
    version: str
    body: str
    is_active: bool = True

    @property
    def full_version(self) -> str:
        return f"{self.name}:{self.version}"


class TemplateStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def get(self, name: str, version: str | None = None) -> TemplateRecord | None:
        with self._conn() as c:
            if version is None:
                row = c.execute(
                    "SELECT * FROM templates WHERE name=? AND is_active=1 ORDER BY id DESC LIMIT 1",
                    (name,),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT * FROM templates WHERE name=? AND version=?",
                    (name, version),
                ).fetchone()
        if row is None:
            return None
        return TemplateRecord(
            name=row["name"],
            version=row["version"],
            body=row["body"],
            is_active=bool(row["is_active"]),
        )

    def list_all(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, name, version, is_active, created_at FROM templates ORDER BY name, id"
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
                "FROM templates WHERE name=? ORDER BY id",
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
            c.execute(
                "INSERT INTO templates (name, version, body, is_active, metadata_json, created_at) "
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
                "SELECT id FROM templates WHERE name=? AND version=?", (name, version)
            ).fetchone()
            if row is None:
                return None
            c.execute(
                "UPDATE templates SET is_active=1 WHERE name=? AND version=?",
                (name, version),
            )
            self._deactivate_others(c, name, version)
        return self.get_raw(name, version)

    def delete(self, name: str, version: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT is_active FROM templates WHERE name=? AND version=?",
                (name, version),
            ).fetchone()
            if row is None:
                return False
            if row["is_active"]:
                raise ValueError("cannot_delete_active_version")
            c.execute("DELETE FROM templates WHERE name=? AND version=?", (name, version))
        return True

    def get_raw(self, name: str, version: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM templates WHERE name=? AND version=?", (name, version)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["metadata"] = json.loads(d.pop("metadata_json") or "null")
        d["is_active"] = bool(d["is_active"])
        return d

    @staticmethod
    def _deactivate_others(
        c: sqlite3.Connection, name: str, keep_version: str
    ) -> None:
        c.execute(
            "UPDATE templates SET is_active=0 WHERE name=? AND version<>?",
            (name, keep_version),
        )


def bootstrap_builtin_templates(store: TemplateStore) -> int:
    """Идемпотентная миграция: встроенные шаблоны → записи v1 в БД.
    Если для name уже есть хотя бы одна запись — пропускает."""
    created = 0
    for name, body in BUILTIN_TEMPLATES.items():
        existing = store.list_versions(name)
        if existing:
            continue
        store.create(
            name=name,
            version="v1",
            body=body,
            metadata={"source": "builtin"},
            is_active=True,
        )
        created += 1
    return created
