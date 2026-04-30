import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

JobStatus = Literal["queued", "running", "done", "failed"]
JobKind = Literal["download"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    status          TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    result_json     TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_finished ON jobs(finished_at);
"""


class JobStore:
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

    def create(self, kind: JobKind, payload: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex
        with self._conn() as c:
            c.execute(
                "INSERT INTO jobs (id, kind, status, payload_json, created_at) "
                "VALUES (?, ?, 'queued', ?, ?)",
                (job_id, kind, json.dumps(payload, ensure_ascii=False), _now()),
            )
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def mark_running(self, job_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE jobs SET status='running', started_at=? WHERE id=?",
                (_now(), job_id),
            )

    def mark_done(self, job_id: str, result: dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE jobs SET status='done', result_json=?, finished_at=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), _now(), job_id),
            )

    def mark_failed(self, job_id: str, error: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
                (error, _now(), job_id),
            )

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count_by_status(self) -> dict[str, int]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def list_failed_older_than(self, iso_ts: str) -> list[dict[str, Any]]:
        """Для cleanup-loop: failed-job-ы, завершённые до iso_ts."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM jobs WHERE status='failed' AND finished_at IS NOT NULL "
                "AND finished_at < ?",
                (iso_ts,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json") or "{}")
        result_json = data.pop("result_json", None)
        data["result"] = json.loads(result_json) if result_json else None
        return data
