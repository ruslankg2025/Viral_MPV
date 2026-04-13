import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_cache_key(base: str | None, **extras: Any) -> str | None:
    """Стабильный составной ключ кеша.

    base — opaque-ключ от вызывающей стороны (обычно `{platform}:{external_id}`).
    extras — параметры, влияющие на результат (prompt_version, model, profile, language).
    Пустые/None-значения игнорируются, ключи сортируются — порядок не важен."""
    if not base:
        return None
    parts = [base]
    for k in sorted(extras):
        v = extras[k]
        if v is None or v == "":
            continue
        parts.append(f"{k}={v}")
    return "|".join(parts)


SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    cache_key    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    result_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    PRIMARY KEY (cache_key, kind)
);
CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at);
"""


class CacheStore:
    def __init__(self, db_path: Path, ttl_days: int = 30):
        self.db_path = db_path
        self.ttl_days = ttl_days
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
        return conn

    def get(self, cache_key: str, kind: str) -> dict[str, Any] | None:
        if not cache_key:
            return None
        with self._conn() as c:
            row = c.execute(
                "SELECT result_json, expires_at FROM cache WHERE cache_key=? AND kind=?",
                (cache_key, kind),
            ).fetchone()
        if row is None:
            return None
        if datetime.fromisoformat(row["expires_at"]) < _now():
            self.delete(cache_key, kind)
            return None
        return json.loads(row["result_json"])

    def set(self, cache_key: str, kind: str, result: dict[str, Any]) -> None:
        if not cache_key:
            return
        expires = (_now() + timedelta(days=self.ttl_days)).isoformat()
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO cache (cache_key, kind, result_json, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (cache_key, kind, json.dumps(result, ensure_ascii=False), _now().isoformat(), expires),
            )

    def delete(self, cache_key: str, kind: str | None = None) -> int:
        with self._conn() as c:
            if kind:
                cur = c.execute(
                    "DELETE FROM cache WHERE cache_key=? AND kind=?", (cache_key, kind)
                )
            else:
                cur = c.execute("DELETE FROM cache WHERE cache_key=?", (cache_key,))
            return cur.rowcount

    def size(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
