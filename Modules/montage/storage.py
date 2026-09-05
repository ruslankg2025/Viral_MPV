"""SQLite-хранилище джобов автомонтажа.

Синхронный sqlite3 (как в publisher/monitor): WAL, isolation_level=None,
busy_timeout. Один воркер обрабатывает по одному джобу — гонок за строку нет,
но WAL держим ради конкуррентных читателей (API отвечает во время рендера).
"""
# NB: метод .list() затеняет builtin list внутри тела класса — без отложенных
# аннотаций `-> list[...]` у методов ниже падало бы. from __future__ спасает.
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# queued  — в очереди, ждёт окна/памяти
# running — воркер запустил рендер (в норме таких ≤1)
# done    — мастер готов
# failed  — ошибка/таймаут/прерывание (dead-letter)
STATUSES = ("queued", "running", "done", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


JOB_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    account_id      TEXT,
    title           TEXT,
    status          TEXT NOT NULL,
    source_filename TEXT,
    source_path     TEXT,
    out_dir         TEXT,
    result_path     TEXT,
    model_size      TEXT,
    width           INTEGER,
    height          INTEGER,
    language        TEXT,
    force           INTEGER NOT NULL DEFAULT 0,
    qc_passed       INTEGER,          -- 1/0/NULL
    qc_issues_json  TEXT,
    error_message   TEXT,
    log_tail        TEXT,
    duration_s      REAL,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_account ON jobs(account_id, created_at DESC);
"""


class JobStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(JOB_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ── CRUD ──────────────────────────────────────────────────────────────────
    def create(
        self,
        *,
        job_id: str | None = None,
        account_id: str | None,
        title: str | None,
        source_filename: str,
        source_path: str,
        out_dir: str,
        model_size: str,
        width: int,
        height: int,
        language: str | None,
        force: bool,
    ) -> dict[str, Any]:
        jid = job_id or _new_id()
        with self._conn() as c:
            c.execute(
                "INSERT INTO jobs "
                "(id, account_id, title, status, source_filename, source_path, out_dir, "
                " result_path, model_size, width, height, language, force, created_at) "
                "VALUES (?, ?, ?, 'queued', ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                (
                    jid, account_id, title, source_filename, source_path, out_dir,
                    model_size, int(width), int(height), language, 1 if force else 0, _now(),
                ),
            )
        return self.get(jid)

    def get(self, jid: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        return self._row(row) if row else None

    def list(
        self, *, account_id: str | None = None, status: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if account_id is not None:
            clauses.append("account_id = ?")
            params.append(account_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ?", params
            ).fetchall()
        return [self._row(r) for r in rows]

    def next_queued(self) -> dict[str, Any] | None:
        """Старейший джоб в очереди (FIFO)."""
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
        return self._row(row) if row else None

    def next_runnable(self, *, in_window: bool) -> dict[str, Any] | None:
        """Старейший запускаемый сейчас джоб. В ночном окне — любой; вне окна —
        только помеченный force (on-demand с явной проверкой памяти)."""
        with self._conn() as c:
            if in_window:
                row = c.execute(
                    "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT * FROM jobs WHERE status='queued' AND force=1 "
                    "ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
        return self._row(row) if row else None

    def mark_running(self, jid: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE jobs SET status='running', started_at=? WHERE id=?", (_now(), jid)
            )

    def finish(
        self,
        jid: str,
        *,
        status: str,
        result_path: str | None = None,
        qc_passed: bool | None = None,
        qc_issues: Any = None,
        error_message: str | None = None,
        log_tail: str | None = None,
        duration_s: float | None = None,
    ) -> dict[str, Any] | None:
        qc = None if qc_passed is None else (1 if qc_passed else 0)
        with self._conn() as c:
            c.execute(
                "UPDATE jobs SET status=?, result_path=?, qc_passed=?, qc_issues_json=?, "
                "error_message=?, log_tail=?, duration_s=?, finished_at=? WHERE id=?",
                (
                    status, result_path, qc,
                    json.dumps(qc_issues, ensure_ascii=False) if qc_issues is not None else None,
                    error_message, log_tail, duration_s, _now(), jid,
                ),
            )
        return self.get(jid)

    def reset_orphans(self) -> int:
        """Джобы в 'running' после рестарта контейнера — их субпроцесс мёртв.
        НЕ перезапускаем автоматически (риск OOM-петли) — помечаем failed."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE jobs SET status='failed', error_message='interrupted (service restart)', "
                "finished_at=? WHERE status='running'",
                (_now(),),
            )
            return cur.rowcount

    def delete(self, jid: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM jobs WHERE id=?", (jid,))
            return cur.rowcount > 0

    def count(self, *, status: str | None = None) -> int:
        with self._conn() as c:
            if status:
                row = c.execute("SELECT COUNT(*) n FROM jobs WHERE status=?", (status,)).fetchone()
            else:
                row = c.execute("SELECT COUNT(*) n FROM jobs").fetchone()
        return int(row["n"]) if row else 0

    def old_finished(self, *, before_iso: str, limit: int = 100) -> list[dict[str, Any]]:
        """Завершённые (done/failed) джобы старше before_iso — под автоочистку диска."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM jobs WHERE status IN ('done','failed') "
                "AND COALESCE(finished_at, created_at) < ? ORDER BY created_at ASC LIMIT ?",
                (before_iso, int(limit)),
            ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["force"] = bool(d.get("force"))
        if d.get("qc_passed") is not None:
            d["qc_passed"] = bool(d["qc_passed"])
        d["qc_issues"] = json.loads(d.pop("qc_issues_json") or "null")
        return d
