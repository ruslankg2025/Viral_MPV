import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

RunStatus = Literal["queued", "downloading", "transcribing", "analyzing", "done", "failed"]
TERMINAL_STATUSES: set[str] = {"done", "failed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                  TEXT PRIMARY KEY,
    video_id            TEXT,
    url                 TEXT NOT NULL,
    platform            TEXT NOT NULL,
    external_id         TEXT,
    account_id          TEXT,
    script_template     TEXT,
    status              TEXT NOT NULL,
    current_step        TEXT,
    steps_json          TEXT NOT NULL DEFAULT '{}',
    video_meta_json     TEXT,
    result_json         TEXT,
    scripts_json        TEXT NOT NULL DEFAULT '[]',
    error               TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    finished_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_status_updated  ON runs(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_runs_video_id_status ON runs(video_id, status);
CREATE INDEX IF NOT EXISTS idx_runs_url_status      ON runs(url, status);
CREATE INDEX IF NOT EXISTS idx_runs_created         ON runs(created_at);
"""


class RunStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            # Миграция: добавить scripts_json в существующие БД (до v0.2)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(runs)").fetchall()}
            if "scripts_json" not in cols:
                c.execute(
                    "ALTER TABLE runs ADD COLUMN scripts_json TEXT NOT NULL DEFAULT '[]'"
                )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ---------- create / update ----------

    def create(
        self,
        *,
        url: str,
        platform: str,
        external_id: str | None = None,
        video_id: str | None = None,
        account_id: str | None = None,
        script_template: str | None = None,
    ) -> str:
        run_id = uuid.uuid4().hex
        now = _now()
        with self._conn() as c:
            c.execute(
                "INSERT INTO runs (id, video_id, url, platform, external_id, account_id, "
                "script_template, status, current_step, steps_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', NULL, '{}', ?, ?)",
                (run_id, video_id, url, platform, external_id, account_id,
                 script_template, now, now),
            )
        return run_id

    def set_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        current_step: str | None = None,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        now = _now()
        finished = now if status in TERMINAL_STATUSES else None
        with self._conn() as c:
            c.execute(
                "UPDATE runs SET status=?, current_step=?, error=COALESCE(?, error), "
                "result_json=COALESCE(?, result_json), updated_at=?, "
                "finished_at=COALESCE(?, finished_at) WHERE id=?",
                (
                    status,
                    current_step,
                    error,
                    json.dumps(result, ensure_ascii=False) if result else None,
                    now,
                    finished,
                    run_id,
                ),
            )

    def set_video_meta(self, run_id: str, meta: dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE runs SET video_meta_json=?, updated_at=? WHERE id=?",
                (json.dumps(meta, ensure_ascii=False), _now(), run_id),
            )

    def pulse(self, run_id: str) -> None:
        """Heartbeat: обновить только updated_at. Используется во время длинных
        wait_done, чтобы recovery-loop не пометил активный run как зависший
        после рестарта shell-а в середине pipeline.
        """
        with self._conn() as c:
            c.execute("UPDATE runs SET updated_at=? WHERE id=?", (_now(), run_id))

    def append_script(self, run_id: str, script: dict[str, Any]) -> None:
        """Добавить script-meta в массив scripts_json. Атомарно (BEGIN IMMEDIATE).

        script — dict с полями типа {id, template, status, cost_usd, created_at, ...}.
        """
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT scripts_json FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if row is None:
                c.execute("ROLLBACK")
                return
            scripts = json.loads(row["scripts_json"] or "[]")
            scripts.append(script)
            c.execute(
                "UPDATE runs SET scripts_json=?, updated_at=? WHERE id=?",
                (json.dumps(scripts, ensure_ascii=False), _now(), run_id),
            )
            c.execute("COMMIT")

    def list_scripts(self, run_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            row = c.execute(
                "SELECT scripts_json FROM runs WHERE id=?", (run_id,)
            ).fetchone()
        if row is None:
            return []
        return json.loads(row["scripts_json"] or "[]")

    def patch_step(self, run_id: str, step: str, patch: dict[str, Any]) -> None:
        """Слить patch в steps_json[step] и проставить updated_at.

        Перечитывает current steps_json под BEGIN IMMEDIATE — атомарно.
        """
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT steps_json FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if row is None:
                c.execute("ROLLBACK")
                return
            steps = json.loads(row["steps_json"] or "{}")
            existing = steps.get(step, {})
            existing.update(patch)
            steps[step] = existing
            c.execute(
                "UPDATE runs SET steps_json=?, updated_at=? WHERE id=?",
                (json.dumps(steps, ensure_ascii=False), _now(), run_id),
            )
            c.execute("COMMIT")

    # ---------- queries ----------

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def list_by_video(
        self, video_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM runs WHERE video_id=? ORDER BY created_at DESC LIMIT ?",
                (video_id, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def find_active_by_video_id(self, video_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM runs WHERE video_id=? AND status NOT IN ('done','failed') "
                "ORDER BY created_at DESC LIMIT 1",
                (video_id,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def find_active_by_url(self, url: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM runs WHERE url=? AND status NOT IN ('done','failed') "
                "ORDER BY created_at DESC LIMIT 1",
                (url,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def purge_legacy_runs_without_strategy(self) -> int:
        """Одноразовая миграция: удалить ВСЕ done/failed runs, у которых
        нет шага strategy в steps_json (старые тестовые runs до Track A2).

        Активные runs (queued/downloading/transcribing/analyzing) не трогаются.
        Идемпотентна: повторный вызов на чистой БД вернёт 0.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, steps_json FROM runs WHERE status IN ('done','failed')"
            ).fetchall()
            ids_to_purge: list[str] = []
            for r in rows:
                steps = json.loads(r["steps_json"] or "{}")
                if "strategy" not in steps:
                    ids_to_purge.append(r["id"])
            if not ids_to_purge:
                return 0
            placeholders = ",".join("?" for _ in ids_to_purge)
            c.execute(f"DELETE FROM runs WHERE id IN ({placeholders})", ids_to_purge)
            return len(ids_to_purge)

    def delete_terminal_older_than(self, ttl_days: int) -> int:
        """Удаляет terminal runs (done/failed) старше N дней. Возвращает кол-во удалённых.

        Защита: активные runs (queued/downloading/transcribing) не трогаются никогда —
        даже если они старые (это диагностический сигнал зависания, не мусор).
        """
        if ttl_days <= 0:
            return 0
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=ttl_days)
        ).isoformat()
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM runs WHERE status IN ('done','failed') "
                "AND COALESCE(finished_at, updated_at) < ?",
                (cutoff,),
            )
            return cur.rowcount or 0

    def list_stalled(self, stalled_timeout_sec: int) -> list[dict[str, Any]]:
        """Non-terminal run-ы, где updated_at старше cutoff. Для recovery loop при старте."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=stalled_timeout_sec)
        ).isoformat()
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM runs WHERE status NOT IN ('done','failed') "
                "AND updated_at < ?",
                (cutoff,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count_by_status(self) -> dict[str, int]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT status, COUNT(*) AS n FROM runs GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["steps"] = json.loads(d.pop("steps_json") or "{}")
        vm = d.pop("video_meta_json", None)
        d["video_meta"] = json.loads(vm) if vm else None
        rj = d.pop("result_json", None)
        d["result"] = json.loads(rj) if rj else None
        sj = d.pop("scripts_json", None)
        d["scripts"] = json.loads(sj) if sj else []
        return d
