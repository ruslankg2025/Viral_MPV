import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .crypto import KeyCrypto, mask_secret
from .pricing import provider_kind

KeyKind = Literal["transcription", "vision"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _month_start() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    provider           TEXT NOT NULL,
    kind               TEXT NOT NULL,
    label              TEXT,
    secret_enc         BLOB NOT NULL,
    secret_masked      TEXT NOT NULL,
    is_active          INTEGER NOT NULL DEFAULT 1,
    priority           INTEGER NOT NULL DEFAULT 100,
    monthly_limit_usd  REAL,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    last_used_at       TEXT,
    UNIQUE(label)
);
CREATE INDEX IF NOT EXISTS idx_keys_kind_priority ON api_keys(kind, is_active, priority);

CREATE TABLE IF NOT EXISTS api_key_usage (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id         INTEGER NOT NULL,
    job_id         TEXT NOT NULL,
    ts             TEXT NOT NULL,
    operation      TEXT NOT NULL,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    audio_seconds  REAL,
    frames         INTEGER,
    latency_ms     INTEGER,
    status         TEXT NOT NULL,
    error          TEXT,
    cost_usd       REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (key_id) REFERENCES api_keys(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_usage_key_ts ON api_key_usage(key_id, ts);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON api_key_usage(ts);

CREATE TABLE IF NOT EXISTS bootstrap_meta (
    provider       TEXT PRIMARY KEY,
    consumed_at    TEXT NOT NULL
);
"""


class KeyStore:
    def __init__(self, db_path: Path, crypto: KeyCrypto):
        self.db_path = db_path
        self.crypto = crypto
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ---------- CRUD ----------
    def create(
        self,
        *,
        provider: str,
        label: str | None,
        secret: str,
        priority: int = 100,
        monthly_limit_usd: float | None = None,
        is_active: bool = True,
    ) -> dict[str, Any]:
        kind = provider_kind(provider)
        enc = self.crypto.encrypt(secret)
        masked = mask_secret(secret)
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO api_keys "
                "(provider, kind, label, secret_enc, secret_masked, is_active, priority, "
                " monthly_limit_usd, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    provider, kind, label, enc, masked,
                    1 if is_active else 0, priority, monthly_limit_usd,
                    _now(), _now(),
                ),
            )
            key_id = cur.lastrowid
        return self.get(key_id)

    def update(
        self,
        key_id: int,
        *,
        label: str | None = None,
        priority: int | None = None,
        is_active: bool | None = None,
        monthly_limit_usd: float | None = None,
        secret: str | None = None,
    ) -> dict[str, Any] | None:
        fields, params = [], []
        if label is not None:
            fields.append("label=?"); params.append(label)
        if priority is not None:
            fields.append("priority=?"); params.append(priority)
        if is_active is not None:
            fields.append("is_active=?"); params.append(1 if is_active else 0)
        if monthly_limit_usd is not None:
            fields.append("monthly_limit_usd=?"); params.append(monthly_limit_usd)
        if secret is not None:
            fields.append("secret_enc=?"); params.append(self.crypto.encrypt(secret))
            fields.append("secret_masked=?"); params.append(mask_secret(secret))
        if not fields:
            return self.get(key_id)
        fields.append("updated_at=?"); params.append(_now())
        params.append(key_id)
        with self._conn() as c:
            c.execute(f"UPDATE api_keys SET {', '.join(fields)} WHERE id=?", params)
        return self.get(key_id)

    def delete(self, key_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
            return cur.rowcount > 0

    def get(self, key_id: int) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
        return self._row_to_public(row) if row else None

    def get_secret(self, key_id: int) -> str | None:
        """Отдаёт расшифрованный секрет. Только для внутреннего использования (job worker)."""
        with self._conn() as c:
            row = c.execute("SELECT secret_enc FROM api_keys WHERE id=?", (key_id,)).fetchone()
        if row is None:
            return None
        return self.crypto.decrypt(row["secret_enc"])

    def list_all(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM api_keys ORDER BY kind, priority, id").fetchall()
        return [self._row_to_public(r) for r in rows]

    def list_active(self, kind: KeyKind, provider: str | None = None) -> list[dict[str, Any]]:
        """Активные ключи (kind) отсортированные по priority ASC. Фильтр по provider опционален."""
        with self._conn() as c:
            if provider:
                rows = c.execute(
                    "SELECT * FROM api_keys WHERE kind=? AND is_active=1 AND provider=? "
                    "ORDER BY priority, id",
                    (kind, provider),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM api_keys WHERE kind=? AND is_active=1 ORDER BY priority, id",
                    (kind,),
                ).fetchall()
        return [self._row_to_public(r) for r in rows]

    def count_active(self) -> dict[str, int]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT kind, COUNT(*) AS n FROM api_keys WHERE is_active=1 GROUP BY kind"
            ).fetchall()
        return {r["kind"]: r["n"] for r in rows}

    # ---------- usage ----------
    def record_usage(
        self,
        *,
        key_id: int,
        job_id: str,
        operation: str,
        provider: str,
        model: str,
        status: str,
        cost_usd: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        audio_seconds: float | None = None,
        frames: int | None = None,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO api_key_usage "
                "(key_id, job_id, ts, operation, provider, model, input_tokens, output_tokens, "
                " audio_seconds, frames, latency_ms, status, error, cost_usd) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key_id, job_id, _now(), operation, provider, model,
                    input_tokens, output_tokens, audio_seconds, frames,
                    latency_ms, status, error, cost_usd,
                ),
            )
            if status == "ok":
                c.execute(
                    "UPDATE api_keys SET last_used_at=? WHERE id=?", (_now(), key_id)
                )

    def month_cost(self, key_id: int) -> float:
        start = _month_start()
        with self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM api_key_usage "
                "WHERE key_id=? AND ts >= ? AND status='ok'",
                (key_id, start),
            ).fetchone()
        return float(row["total"] or 0)

    def usage_30d_summary(self, key_id: int) -> dict[str, Any]:
        from datetime import timedelta
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS calls, "
                " SUM(CASE WHEN status='ok' THEN cost_usd ELSE 0 END) AS cost, "
                " SUM(CASE WHEN status!='ok' THEN 1 ELSE 0 END) AS errors "
                "FROM api_key_usage WHERE key_id=? AND ts >= ?",
                (key_id, since),
            ).fetchone()
        return {
            "calls": row["calls"] or 0,
            "cost_usd": round(row["cost"] or 0, 6),
            "errors": row["errors"] or 0,
        }

    def usage_aggregate(
        self, *, since: str | None = None, until: str | None = None
    ) -> dict[str, Any]:
        where = ["1=1"]
        params: list[Any] = []
        if since:
            where.append("ts >= ?"); params.append(since)
        if until:
            where.append("ts <= ?"); params.append(until)
        where_sql = " AND ".join(where)

        with self._conn() as c:
            total_row = c.execute(
                f"SELECT COUNT(*) AS calls, "
                f" SUM(CASE WHEN status='ok' THEN cost_usd ELSE 0 END) AS cost, "
                f" SUM(CASE WHEN status!='ok' THEN 1 ELSE 0 END) AS errors "
                f"FROM api_key_usage WHERE {where_sql}",
                params,
            ).fetchone()
            by_provider = c.execute(
                f"SELECT provider, COUNT(*) AS calls, "
                f" SUM(CASE WHEN status='ok' THEN cost_usd ELSE 0 END) AS cost, "
                f" AVG(latency_ms) AS avg_lat, "
                f" SUM(CASE WHEN status!='ok' THEN 1 ELSE 0 END) AS errors "
                f"FROM api_key_usage WHERE {where_sql} GROUP BY provider",
                params,
            ).fetchall()
            by_day = c.execute(
                f"SELECT substr(ts, 1, 10) AS day, COUNT(*) AS calls, "
                f" SUM(CASE WHEN status='ok' THEN cost_usd ELSE 0 END) AS cost "
                f"FROM api_key_usage WHERE {where_sql} GROUP BY day ORDER BY day",
                params,
            ).fetchall()
        return {
            "total": {
                "calls": total_row["calls"] or 0,
                "cost_usd": round(total_row["cost"] or 0, 6),
                "errors": total_row["errors"] or 0,
            },
            "by_provider": [
                {
                    "provider": r["provider"],
                    "calls": r["calls"],
                    "cost_usd": round(r["cost"] or 0, 6),
                    "avg_latency_ms": round(r["avg_lat"] or 0, 1),
                    "errors": r["errors"] or 0,
                }
                for r in by_provider
            ],
            "by_day": [
                {"day": r["day"], "calls": r["calls"], "cost_usd": round(r["cost"] or 0, 6)}
                for r in by_day
            ],
        }

    # ---------- bootstrap ----------
    def mark_bootstrap_consumed(self, provider: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO bootstrap_meta (provider, consumed_at) VALUES (?, ?)",
                (provider, _now()),
            )

    def is_bootstrap_consumed(self, provider: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM bootstrap_meta WHERE provider=?", (provider,)
            ).fetchone()
        return row is not None

    # ---------- helpers ----------
    @staticmethod
    def _row_to_public(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d.pop("secret_enc", None)
        d["is_active"] = bool(d.get("is_active"))
        return d
