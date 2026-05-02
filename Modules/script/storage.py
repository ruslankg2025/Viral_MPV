"""SQLite-хранилище версий сценариев. fork через parent_id, root_id для индекса дерева."""
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


SCHEMA = """
CREATE TABLE IF NOT EXISTS script_versions (
    id                      TEXT PRIMARY KEY,
    parent_id               TEXT,
    root_id                 TEXT NOT NULL,
    template                TEXT NOT NULL,
    template_version        TEXT NOT NULL,
    schema_version          TEXT NOT NULL,
    status                  TEXT NOT NULL,
    body_json               TEXT NOT NULL,
    params_json             TEXT NOT NULL,
    profile_json            TEXT NOT NULL,
    constraints_report_json TEXT,
    cost_usd                REAL NOT NULL DEFAULT 0,
    input_tokens            INTEGER,
    output_tokens           INTEGER,
    latency_ms              INTEGER,
    provider                TEXT NOT NULL,
    model                   TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES script_versions(id)
);
CREATE INDEX IF NOT EXISTS idx_script_parent ON script_versions(parent_id);
CREATE INDEX IF NOT EXISTS idx_script_root ON script_versions(root_id);

-- Self-learning agent: фундамент для feedback ★/🔥/💧 + комментарии.
-- Используется few-shot context builder и continuous improvement loop
-- (см. plan/PLAN_SELF_LEARNING_AGENT.md этапы 1, 3, 5, 6).
CREATE TABLE IF NOT EXISTS script_feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id       TEXT NOT NULL,           -- ссылка на script_versions.id
    account_id      TEXT,                     -- кто оценил (NULL → анонимно/системно)
    rating          INTEGER,                  -- 1-5 (NULL допустим)
    vote            TEXT,                     -- 'fire' | 'water' | NULL
    comment         TEXT,                     -- произвольный текст-комментарий
    refine_request  TEXT,                     -- что переделать ("Усилить hook", "Сократить")
    created_at      TEXT NOT NULL,
    CHECK(rating IS NULL OR (rating BETWEEN 1 AND 5)),
    CHECK(vote IS NULL OR vote IN ('fire','water')),
    FOREIGN KEY (script_id) REFERENCES script_versions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_feedback_script   ON script_feedback(script_id);
CREATE INDEX IF NOT EXISTS idx_feedback_account  ON script_feedback(account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_rating   ON script_feedback(account_id, rating);
"""


class VersionStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def create(
        self,
        *,
        parent_id: str | None,
        template: str,
        template_version: str,
        schema_version: str,
        status: str,
        body: dict[str, Any],
        params: dict[str, Any],
        profile: dict[str, Any],
        constraints_report: dict[str, Any] | None,
        cost_usd: float,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int | None,
        provider: str,
        model: str,
    ) -> dict[str, Any]:
        version_id = _new_id()

        if parent_id is None:
            root_id = version_id
        else:
            parent = self.get(parent_id)
            if parent is None:
                raise ValueError(f"parent_not_found: {parent_id}")
            root_id = parent["root_id"]

        with self._conn() as c:
            c.execute(
                "INSERT INTO script_versions "
                "(id, parent_id, root_id, template, template_version, schema_version, status, "
                " body_json, params_json, profile_json, constraints_report_json, cost_usd, "
                " input_tokens, output_tokens, latency_ms, provider, model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    version_id,
                    parent_id,
                    root_id,
                    template,
                    template_version,
                    schema_version,
                    status,
                    json.dumps(body, ensure_ascii=False),
                    json.dumps(params, ensure_ascii=False),
                    json.dumps(profile, ensure_ascii=False),
                    json.dumps(constraints_report, ensure_ascii=False) if constraints_report else None,
                    cost_usd,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                    provider,
                    model,
                    _now(),
                ),
            )
        return self.get(version_id)

    def get(self, version_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM script_versions WHERE id=?", (version_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_public(row)

    def get_children(self, parent_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM script_versions WHERE parent_id=? ORDER BY created_at",
                (parent_id,),
            ).fetchall()
        return [self._row_to_public(r) for r in rows]

    def list_tree(self, root_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM script_versions WHERE root_id=? ORDER BY created_at",
                (root_id,),
            ).fetchall()
        return [self._row_to_public(r) for r in rows]

    def delete(self, version_id: str) -> bool:
        """Удаляет версию. Если есть дети — raise ValueError."""
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM script_versions WHERE parent_id=?", (version_id,)
            ).fetchone()
            if row is not None:
                raise ValueError("cannot_delete_version_with_children")
            cur = c.execute("DELETE FROM script_versions WHERE id=?", (version_id,))
            return cur.rowcount > 0

    @staticmethod
    def _row_to_public(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["body"] = json.loads(d.pop("body_json"))
        d["params"] = json.loads(d.pop("params_json"))
        d["profile"] = json.loads(d.pop("profile_json"))
        crj = d.pop("constraints_report_json", None)
        d["constraints_report"] = json.loads(crj) if crj else None
        return d

    # ────────────────────────────────────────────────────────────────
    # Feedback (PLAN_SELF_LEARNING_AGENT этап 1)
    # ────────────────────────────────────────────────────────────────

    def save_feedback(
        self,
        *,
        script_id: str,
        account_id: str | None = None,
        rating: int | None = None,
        vote: str | None = None,
        comment: str | None = None,
        refine_request: str | None = None,
    ) -> int:
        """Сохраняет один feedback-event. Возвращает id записи.

        Несколько событий на один script разрешены (история «обновил оценку»,
        «переписал и снова оценил»). Для агрегации use list_for_script /
        top_rated_for_account.
        """
        if rating is not None and not (1 <= rating <= 5):
            raise ValueError("rating_out_of_range")
        if vote is not None and vote not in ("fire", "water"):
            raise ValueError("invalid_vote")
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO script_feedback
                   (script_id, account_id, rating, vote, comment, refine_request, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (script_id, account_id, rating, vote, comment, refine_request, _now()),
            )
            return cur.lastrowid

    def list_for_script(self, script_id: str) -> list[dict[str, Any]]:
        """Все feedback-события на данный script (история)."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT id, script_id, account_id, rating, vote, comment,
                          refine_request, created_at
                   FROM script_feedback
                   WHERE script_id = ?
                   ORDER BY created_at DESC""",
                (script_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_for_account(
        self,
        account_id: str,
        *,
        days: int = 30,
        min_rating: int | None = None,
        max_rating: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Все feedback-события по аккаунту с фильтрами по периоду и рейтингу.

        Используется continuous-loop агентом и context-builder-ом.
        """
        clauses = ["account_id = ?", "created_at >= datetime('now', ?)"]
        params: list[Any] = [account_id, f"-{int(days)} days"]
        if min_rating is not None:
            clauses.append("rating >= ?")
            params.append(min_rating)
        if max_rating is not None:
            clauses.append("rating <= ?")
            params.append(max_rating)
        sql = f"""SELECT id, script_id, account_id, rating, vote, comment,
                         refine_request, created_at
                  FROM script_feedback
                  WHERE {' AND '.join(clauses)}
                  ORDER BY created_at DESC
                  LIMIT ?"""
        params.append(int(limit))
        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def top_rated_for_account(
        self, account_id: str, *, limit: int = 3, min_rating: int = 4,
    ) -> list[dict[str, Any]]:
        """Топ-N high-rated скриптов для context-builder-а (few-shot positive).

        Возвращает: id (script), rating, comment, body (полный) — поэтому
        агент может вшить hook.text / scenes в prompt.
        """
        with self._conn() as c:
            rows = c.execute(
                """SELECT sv.id, sv.body_json, f.rating, f.comment, f.created_at
                   FROM script_feedback f
                   JOIN script_versions sv ON sv.id = f.script_id
                   WHERE f.account_id = ? AND f.rating >= ? AND sv.status = 'ok'
                   ORDER BY f.rating DESC, f.created_at DESC
                   LIMIT ?""",
                (account_id, min_rating, int(limit)),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["body"] = json.loads(d.pop("body_json"))
            out.append(d)
        return out

    def bottom_rated_for_account(
        self, account_id: str, *, limit: int = 3, max_rating: int = 2,
    ) -> list[dict[str, Any]]:
        """Топ-N low-rated скриптов для context-builder-а (few-shot negative)."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT sv.id, sv.body_json, f.rating, f.comment, f.created_at
                   FROM script_feedback f
                   JOIN script_versions sv ON sv.id = f.script_id
                   WHERE f.account_id = ? AND f.rating <= ? AND sv.status = 'ok'
                   ORDER BY f.rating ASC, f.created_at DESC
                   LIMIT ?""",
                (account_id, max_rating, int(limit)),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["body"] = json.loads(d.pop("body_json"))
            out.append(d)
        return out
