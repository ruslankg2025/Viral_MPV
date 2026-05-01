"""SQLite-store для метрик от ментор-бота (ручной ввод от Алины).

Store по образцу orchestrator/runs/store.py: WAL, busy_timeout, JSON-blob
для метрик. Дедуп — по (respondent, account_id, template_code, response_date),
повторный POST за тот же день обновляет data_json (UPSERT).

Multi-account safe: NULL account_id нормализован к '' чтобы UNIQUE INDEX
работал предсказуемо (SQLite NULL != NULL в UNIQUE).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_insights (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    template_code TEXT NOT NULL,
    respondent    TEXT NOT NULL,
    account_id    TEXT NOT NULL DEFAULT '',
    response_date TEXT NOT NULL,
    responded_at  TEXT,
    data_json     TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK(response_date GLOB '????-??-??')
);
CREATE INDEX IF NOT EXISTS idx_insights_code_date
    ON manual_insights(template_code, response_date DESC);
CREATE INDEX IF NOT EXISTS idx_insights_account
    ON manual_insights(account_id, template_code, response_date DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_insights_resp_acct_code_date
    ON manual_insights(respondent, account_id, template_code, response_date);
"""


class InsightsStore:
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
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def upsert_blog_daily(
        self,
        *,
        respondent: str,
        account_id: str | None,
        response_date: str,
        responded_at: str | None,
        data: dict[str, Any],
    ) -> tuple[int, bool]:
        """UPSERT по (respondent, account_id, 'blog_daily', response_date).

        Возвращает (id, created): created=True если новая запись,
        False если был update.
        """
        return self._upsert("blog_daily", respondent, account_id,
                            response_date, responded_at, data)

    def _upsert(
        self,
        template_code: str,
        respondent: str,
        account_id: str | None,
        response_date: str,
        responded_at: str | None,
        data: dict[str, Any],
    ) -> tuple[int, bool]:
        acc = (account_id or "").strip()
        data_json = json.dumps(data, ensure_ascii=False)
        with self._conn() as c:
            existing = c.execute(
                """SELECT id FROM manual_insights
                   WHERE respondent=? AND account_id=?
                     AND template_code=? AND response_date=?""",
                (respondent, acc, template_code, response_date),
            ).fetchone()
            if existing is not None:
                c.execute(
                    """UPDATE manual_insights
                       SET responded_at=?, data_json=?
                       WHERE id=?""",
                    (responded_at, data_json, existing["id"]),
                )
                return existing["id"], False
            cur = c.execute(
                """INSERT INTO manual_insights
                   (template_code, respondent, account_id, response_date,
                    responded_at, data_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (template_code, respondent, acc, response_date,
                 responded_at, data_json),
            )
            return cur.lastrowid, True

    def list_blog_daily(
        self,
        *,
        days: int = 30,
        respondent: str | None = None,
        account_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Возвращает развёрнутые ряды (распарсенный data_json) за последние
        N дней. Сортировка по response_date ASC (для chart timeline)."""
        params: list[Any] = ["blog_daily", f"-{int(days)} days"]
        extra = ""
        if respondent:
            extra += " AND respondent = ?"
            params.append(respondent)
        if account_id is not None:
            extra += " AND account_id = ?"
            params.append((account_id or "").strip())
        sql = f"""
            SELECT id, respondent, account_id, response_date, responded_at,
                   data_json, created_at
            FROM manual_insights
            WHERE template_code = ?
              AND response_date >= date('now', ?)
              {extra}
            ORDER BY response_date ASC
        """
        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                data = json.loads(r["data_json"]) or {}
            except json.JSONDecodeError:
                data = {}
            out.append({
                "id": r["id"],
                "respondent": r["respondent"],
                "account_id": r["account_id"] or None,
                "response_date": r["response_date"],
                "responded_at": r["responded_at"],
                "data": data,
            })
        return out

    def get_health(self) -> dict[str, Any]:
        """{'latest_post': ISO|None, 'stale_days': int|None, 'total_rows': int}.

        latest_post — самая свежая responded_at (или created_at если
        responded_at NULL).
        stale_days — сколько дней прошло с последнего апдейта.
        """
        with self._conn() as c:
            row = c.execute(
                """SELECT
                    COUNT(*) AS n,
                    MAX(COALESCE(responded_at, created_at)) AS latest
                FROM manual_insights"""
            ).fetchone()
        n = row["n"] or 0
        latest = row["latest"]
        if not n or not latest:
            return {"latest_post": None, "stale_days": None, "total_rows": 0}
        try:
            # SQLite latest может быть с таймзоной или без. Парсим
            # обе версии. Если без TZ — считаем UTC.
            latest_norm = latest.replace("Z", "+00:00")
            if "+" not in latest_norm and "T" in latest_norm:
                # 'YYYY-MM-DDTHH:MM:SS' без TZ
                dt = datetime.fromisoformat(latest_norm).replace(
                    tzinfo=timezone.utc
                )
            else:
                # 'YYYY-MM-DD HH:MM:SS' (sqlite default) или ISO с TZ
                # Подставляем 'T' если разделитель пробел
                dt = datetime.fromisoformat(latest_norm.replace(" ", "T"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return {
                "latest_post": latest,
                "stale_days": None,
                "total_rows": n,
            }
        delta = datetime.now(timezone.utc) - dt
        return {
            "latest_post": dt.isoformat(),
            "stale_days": max(0, delta.days),
            "total_rows": n,
        }
