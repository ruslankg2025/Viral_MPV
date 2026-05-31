"""SQLite-store для analytics-сервиса (cross-platform analytics).

СИНХРОННЫЙ sqlite3 (НЕ aiosqlite) — паттерн как в knowledge/carousel:
WAL, isolation_level=None, busy_timeout=5000, row_factory=Row.

Две таблицы:
- platform_metrics — сырые срезы метрик публикации (по одной строке на fetch).
- daily_snapshots — агрегаты за день по платформе (счётчик подписчиков и т.д.).

Миграции идемпотентные (PRAGMA table_info перед ALTER); индекс по колонке —
ПОСЛЕ её ALTER.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


# Базовая схема. Создаётся целиком при первом старте (CREATE IF NOT EXISTS).
SCHEMA = """
CREATE TABLE IF NOT EXISTS platform_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    views INTEGER DEFAULT 0,
    reach INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    saves INTEGER DEFAULT 0,
    click_through_to_external INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pm_pub ON platform_metrics(publication_id, fetched_at);
CREATE INDEX IF NOT EXISTS idx_pm_platform ON platform_metrics(platform, fetched_at);

CREATE TABLE IF NOT EXISTS daily_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE,
    platform TEXT,
    total_views INTEGER,
    total_reach INTEGER,
    new_followers INTEGER,
    click_through INTEGER,
    publications_count INTEGER,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ds_date ON daily_snapshots(date, platform);
"""

# Колонки, добавленные после первой версии (для миграции существующих БД).
# Пример формата (пока пусто — схема ещё не эволюционировала):
# _PM_MIGRATIONS = {"new_col": "ALTER TABLE platform_metrics ADD COLUMN new_col INTEGER DEFAULT 0"}
_PM_MIGRATIONS: dict[str, str] = {}
_DS_MIGRATIONS: dict[str, str] = {}

# Числовые метрики, которые умеет суммировать/агрегировать сервис.
METRIC_COLUMNS = (
    "views", "reach", "likes", "comments",
    "shares", "saves", "click_through_to_external",
)


class AnalyticsStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _migrate(self) -> None:
        """Идемпотентные миграции: PRAGMA table_info перед ALTER, индексы после."""
        with self._conn() as c:
            pm_cols = {r["name"] for r in c.execute("PRAGMA table_info(platform_metrics)").fetchall()}
            for col, ddl in _PM_MIGRATIONS.items():
                if col not in pm_cols:
                    c.execute(ddl)
            ds_cols = {r["name"] for r in c.execute("PRAGMA table_info(daily_snapshots)").fetchall()}
            for col, ddl in _DS_MIGRATIONS.items():
                if col not in ds_cols:
                    c.execute(ddl)

    # ── platform_metrics ─────────────────────────────────────────────

    def insert_metrics(
        self,
        *,
        publication_id: str,
        platform: str,
        views: int = 0,
        reach: int = 0,
        likes: int = 0,
        comments: int = 0,
        shares: int = 0,
        saves: int = 0,
        click_through_to_external: int = 0,
        fetched_at: str | None = None,
    ) -> int:
        """Вставляет один срез метрик. Возвращает rowid."""
        cols = [
            "publication_id", "platform", "views", "reach", "likes",
            "comments", "shares", "saves", "click_through_to_external",
        ]
        vals: list[Any] = [
            publication_id, platform, views, reach, likes,
            comments, shares, saves, click_through_to_external,
        ]
        if fetched_at is not None:
            cols.append("fetched_at")
            vals.append(fetched_at)
        placeholders = ", ".join("?" for _ in cols)
        with self._conn() as c:
            cur = c.execute(
                f"INSERT INTO platform_metrics ({', '.join(cols)}) VALUES ({placeholders})",
                vals,
            )
            return int(cur.lastrowid)

    def fetch_metrics(
        self,
        *,
        publication_id: str | None = None,
        platform: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """Сырые строки platform_metrics с фильтрами. since — ISO/'YYYY-MM-DD...'."""
        clauses, params = [], []
        if publication_id is not None:
            clauses.append("publication_id = ?")
            params.append(publication_id)
        if platform is not None:
            clauses.append("platform = ?")
            params.append(platform)
        if since is not None:
            clauses.append("fetched_at >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM platform_metrics {where} ORDER BY fetched_at ASC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_per_publication(
        self, *, platform: str | None = None, since: str | None = None,
    ) -> list[dict[str, Any]]:
        """Последний срез метрик для каждой публикации (по max fetched_at).

        Используется для агрегаций «текущее состояние», чтобы повторные fetch-и
        одной публикации не суммировались многократно.
        """
        clauses, params = [], []
        if platform is not None:
            clauses.append("platform = ?")
            params.append(platform)
        if since is not None:
            clauses.append("fetched_at >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # Берём строку с максимальным id среди max(fetched_at) на публикацию.
        sql = f"""
            SELECT pm.* FROM platform_metrics pm
            JOIN (
                SELECT publication_id, MAX(fetched_at) AS mx
                FROM platform_metrics {where}
                GROUP BY publication_id
            ) last ON last.publication_id = pm.publication_id
                  AND last.mx = pm.fetched_at
            GROUP BY pm.publication_id
        """
        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ── daily_snapshots ──────────────────────────────────────────────

    def insert_snapshot(
        self,
        *,
        date: str,
        platform: str,
        total_views: int,
        total_reach: int,
        new_followers: int,
        click_through: int,
        publications_count: int,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO daily_snapshots
                   (date, platform, total_views, total_reach, new_followers,
                    click_through, publications_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (date, platform, total_views, total_reach, new_followers,
                 click_through, publications_count),
            )
            return int(cur.lastrowid)

    def list_snapshots(
        self, *, platform: str | None = None, since: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if platform is not None:
            clauses.append("platform = ?")
            params.append(platform)
        if since is not None:
            clauses.append("date >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM daily_snapshots {where} ORDER BY date ASC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        with self._conn() as c:
            metrics = c.execute("SELECT COUNT(*) AS n FROM platform_metrics").fetchone()["n"]
            pubs = c.execute(
                "SELECT COUNT(DISTINCT publication_id) AS n FROM platform_metrics"
            ).fetchone()["n"]
            snaps = c.execute("SELECT COUNT(*) AS n FROM daily_snapshots").fetchone()["n"]
        return {"metric_rows": metrics, "publications": pubs, "snapshots": snaps}
