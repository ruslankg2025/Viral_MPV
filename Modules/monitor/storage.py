"""
MonitorStore — SQLite хранилище с миграциями через PRAGMA user_version.

Таблицы:
  sources          — реестр источников
  videos           — видео источников
  metric_snapshots — временные снимки метрик
  trending_scores  — рассчитанные trending-оценки
  crawl_log        — лог обходов
  youtube_quota    — счётчик units по датам (PT)
"""

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

_HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS sources (
    id                TEXT PRIMARY KEY,
    account_id        TEXT NOT NULL,
    platform          TEXT NOT NULL,
    channel_url       TEXT NOT NULL,
    external_id       TEXT NOT NULL,
    channel_name      TEXT,
    niche_slug        TEXT,
    tags_json         TEXT NOT NULL DEFAULT '[]',
    priority          INTEGER NOT NULL DEFAULT 100,
    interval_min      INTEGER NOT NULL DEFAULT 60,
    is_active         INTEGER NOT NULL DEFAULT 1,
    profile_validated INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT,
    added_at          TEXT NOT NULL,
    last_crawled_at   TEXT,
    UNIQUE(account_id, platform, external_id)
);

CREATE TABLE IF NOT EXISTS videos (
    id             TEXT PRIMARY KEY,
    source_id      TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    platform       TEXT NOT NULL,
    external_id    TEXT NOT NULL,
    url            TEXT NOT NULL,
    title          TEXT,
    description    TEXT,
    thumbnail_url  TEXT,
    duration_sec   INTEGER,
    published_at   TEXT,
    first_seen_at  TEXT NOT NULL,
    UNIQUE(platform, external_id)
);
CREATE INDEX IF NOT EXISTS idx_videos_source_published ON videos(source_id, published_at DESC);

CREATE TABLE IF NOT EXISTS metric_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id         TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    captured_at      TEXT NOT NULL,
    views            INTEGER NOT NULL DEFAULT 0,
    likes            INTEGER NOT NULL DEFAULT 0,
    comments         INTEGER NOT NULL DEFAULT 0,
    engagement_rate  REAL,
    UNIQUE(video_id, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_video_time ON metric_snapshots(video_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS trending_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    computed_at     TEXT NOT NULL,
    zscore_24h      REAL,
    growth_rate_24h REAL,
    is_trending     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(video_id, computed_at)
);
CREATE INDEX IF NOT EXISTS idx_trending_flag ON trending_scores(is_trending, computed_at DESC);

CREATE TABLE IF NOT EXISTS crawl_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,
    videos_new      INTEGER NOT NULL DEFAULT 0,
    videos_updated  INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_crawl_log_source_time ON crawl_log(source_id, started_at DESC);

CREATE TABLE IF NOT EXISTS youtube_quota (
    date        TEXT PRIMARY KEY,
    units_used  INTEGER NOT NULL DEFAULT 0
);
"""

SCHEMA_V2 = """
ALTER TABLE videos ADD COLUMN is_short INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS apify_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    platform    TEXT NOT NULL,
    runs        INTEGER NOT NULL DEFAULT 0,
    items       INTEGER NOT NULL DEFAULT 0,
    UNIQUE(date, platform)
);
"""

# Plan / tariff limits — singleton row, позже станет admin tariff management.
# Значения по умолчанию соответствуют self-плану: 50 источников, 6ч интервал,
# 5 последних роликов на обход, crawl-anchor 00:00 UTC.
SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS plan_limits (
    id                 INTEGER PRIMARY KEY CHECK (id = 1),
    plan_name          TEXT NOT NULL,
    max_sources_total  INTEGER NOT NULL,
    min_interval_min   INTEGER NOT NULL,
    max_results_limit  INTEGER NOT NULL,
    crawl_anchor_utc   TEXT NOT NULL DEFAULT '00:00',
    updated_at         TEXT NOT NULL
);

INSERT OR IGNORE INTO plan_limits
    (id, plan_name, max_sources_total, min_interval_min, max_results_limit, crawl_anchor_utc, updated_at)
VALUES
    (1, 'self', 50, 360, 5, '00:00', datetime('now'));
"""

# Per-source override лимита постов за один обход Apify (для Instagram/TikTok).
# NULL = использовать plan.max_results_limit (глобальный дефолт). Не применимо к YouTube.
SCHEMA_V4 = """
ALTER TABLE sources ADD COLUMN max_results_limit INTEGER;
"""

# Trending v2: velocity (views/hour) + is_rising (velocity-производная > 0).
# velocity — ключевой сигнал «идеи в моменте»: независимо от абсолютных просмотров,
# показывает скорость накопления.
SCHEMA_V5 = """
ALTER TABLE trending_scores ADD COLUMN velocity REAL;
ALTER TABLE trending_scores ADD COLUMN is_rising INTEGER NOT NULL DEFAULT 0;
"""

# Watchlist v1: «на мониторинге» — ежедневный auto-отбор top-N рилсов per source
# с TTL, снимком baseline и финальным статусом (hit|miss|stalled). Отдельная
# таблица (а не колонка is_watched в videos), чтобы хранить baseline/final verdict
# и позволять ролику повторно попадать в watchlist на следующем цикле.
SCHEMA_V6 = """
CREATE TABLE IF NOT EXISTS watchlist (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id          TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    source_id         TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    added_at          TEXT NOT NULL,
    expires_at        TEXT NOT NULL,
    initial_views     INTEGER NOT NULL DEFAULT 0,
    initial_velocity  REAL,
    reason            TEXT NOT NULL DEFAULT 'daily_topn',
    status            TEXT NOT NULL DEFAULT 'active',
    graduated_at      TEXT,
    hit_reason        TEXT,
    closed_at         TEXT,
    UNIQUE(video_id, added_at)
);
CREATE INDEX IF NOT EXISTS idx_watchlist_status_expires ON watchlist(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_watchlist_source_status ON watchlist(source_id, status);
CREATE INDEX IF NOT EXISTS idx_watchlist_video_status ON watchlist(video_id, status);
"""

SCHEMA_V7 = """
ALTER TABLE sources ADD COLUMN full_name TEXT;
ALTER TABLE sources ADD COLUMN followers_count INTEGER;
ALTER TABLE sources ADD COLUMN posts_count INTEGER;
ALTER TABLE sources ADD COLUMN avatar_url TEXT;
ALTER TABLE sources ADD COLUMN is_verified INTEGER;
ALTER TABLE sources ADD COLUMN is_private INTEGER;
ALTER TABLE sources ADD COLUMN business_category TEXT;
ALTER TABLE sources ADD COLUMN profile_fetched_at TEXT;
"""

SCHEMA_V8 = """
DELETE FROM watchlist
WHERE status = 'active'
  AND id NOT IN (
      SELECT MIN(id) FROM watchlist WHERE status = 'active' GROUP BY video_id
  );
CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlist_video_active
    ON watchlist(video_id) WHERE status = 'active';
"""

SCHEMA_V9 = """
ALTER TABLE videos ADD COLUMN niche_slug TEXT;
"""

# Daily snapshots профиля автора — для графика «followers over time» в Аналитике.
# Один снимок в сутки на source (UNIQUE date+source). Захватывается из
# update_profile при каждом успешном fetch_profile (cooldown 10 мин уже
# действует — поэтому не более 144 апдейтов в сутки на handle).
SCHEMA_V10 = """
CREATE TABLE IF NOT EXISTS profile_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    captured_date   TEXT NOT NULL,
    followers_count INTEGER,
    posts_count     INTEGER,
    UNIQUE(source_id, captured_date)
);
CREATE INDEX IF NOT EXISTS idx_profile_snap_source ON profile_snapshots(source_id, captured_date DESC);
"""

# Флаг «это мой собственный аккаунт» — один на account_id (партиальный
# UNIQUE индекс). Используется в Аналитике: страница автоматически
# открывает self-source, не предлагая селектор из 30 конкурентов.
SCHEMA_V11 = """
ALTER TABLE sources ADD COLUMN is_self INTEGER NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_is_self
    ON sources(account_id) WHERE is_self = 1;
"""

# Денормализованный индекс хэштегов рилсов — питает страницу «Хэштеги»
# (поиск по тегу, динамика, авторы) и ускоряет per-tag агрегаты в Аналитике.
# Заполняется из videos.description при upsert_video и backfill-миграцией.
SCHEMA_V12 = """
CREATE TABLE IF NOT EXISTS video_hashtags (
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    tag      TEXT NOT NULL,
    PRIMARY KEY (video_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_video_hashtags_tag ON video_hashtags(tag);
"""


def _backfill_hashtags(conn: sqlite3.Connection) -> None:
    """Извлечь #теги из существующих videos.description при миграции v12."""
    rows = conn.execute(
        "SELECT id, description FROM videos "
        "WHERE description IS NOT NULL AND description != ''"
    ).fetchall()
    inserts: list[tuple[str, str]] = []
    for r in rows:
        for tag in _HASHTAG_RE.findall(r["description"]):
            inserts.append((r["id"], tag.lower()))
    if inserts:
        conn.executemany(
            "INSERT OR IGNORE INTO video_hashtags (video_id, tag) VALUES (?, ?)",
            inserts,
        )


MIGRATIONS: dict[int, str] = {
    1: SCHEMA_V1,
    2: SCHEMA_V2,
    3: SCHEMA_V3,
    4: SCHEMA_V4,
    5: SCHEMA_V5,
    6: SCHEMA_V6,
    7: SCHEMA_V7,
    8: SCHEMA_V8,
    9: SCHEMA_V9,
    10: SCHEMA_V10,
    11: SCHEMA_V11,
    12: SCHEMA_V12,
}

# Python-side hooks, вызываемые после соответствующего SCHEMA_V* (для backfill,
# который не делается чистым SQL — например, regex по существующим строкам).
POST_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    12: _backfill_hashtags,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_pt() -> str:
    """YouTube quota сбрасывается в 00:00 Pacific Time (UTC-7 или UTC-8)."""
    pt_offset = timedelta(hours=-8)  # PST, для упрощения; реально меняется с PDT
    return (datetime.now(timezone.utc) + pt_offset).strftime("%Y-%m-%d")


# ------------------------------------------------------------------ #
# Dataclasses
# ------------------------------------------------------------------ #

@dataclass
class SourceRow:
    id: str
    account_id: str
    platform: str
    channel_url: str
    external_id: str
    channel_name: str | None
    niche_slug: str | None
    tags: list[str]
    priority: int
    interval_min: int
    is_active: bool
    profile_validated: bool
    last_error: str | None
    added_at: str
    last_crawled_at: str | None
    max_results_limit: int | None = None  # None → fallback to plan.max_results_limit
    full_name: str | None = None
    followers_count: int | None = None
    posts_count: int | None = None
    avatar_url: str | None = None
    is_verified: bool | None = None
    is_private: bool | None = None
    business_category: str | None = None
    profile_fetched_at: str | None = None
    is_self: bool = False


@dataclass
class VideoRow:
    id: str
    source_id: str
    platform: str
    external_id: str
    url: str
    title: str | None
    description: str | None
    thumbnail_url: str | None
    duration_sec: int | None
    published_at: str | None
    first_seen_at: str
    is_short: bool = False
    niche_slug: str | None = None


@dataclass
class SnapshotRow:
    id: int
    video_id: str
    captured_at: str
    views: int
    likes: int
    comments: int
    engagement_rate: float | None


@dataclass
class TrendingRow:
    id: int
    video_id: str
    computed_at: str
    zscore_24h: float | None
    growth_rate_24h: float | None
    is_trending: bool
    velocity: float | None = None
    is_rising: bool = False


@dataclass
class CrawlLogRow:
    id: int
    source_id: str
    started_at: str
    finished_at: str | None
    status: str
    videos_new: int
    videos_updated: int
    error: str | None


@dataclass
class PlanRow:
    plan_name: str
    max_sources_total: int
    min_interval_min: int
    max_results_limit: int
    crawl_anchor_utc: str
    updated_at: str


@dataclass
class WatchlistRow:
    id: int
    video_id: str
    source_id: str
    added_at: str
    expires_at: str
    initial_views: int
    initial_velocity: float | None
    reason: str
    status: str  # active|hit|miss|stalled|closed
    graduated_at: str | None
    hit_reason: str | None
    closed_at: str | None


# ------------------------------------------------------------------ #
# Store
# ------------------------------------------------------------------ #

class MonitorStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            self._migrate(c)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        target = max(MIGRATIONS)
        if current >= target:
            return
        for v in sorted(MIGRATIONS):
            if v > current:
                conn.executescript(MIGRATIONS[v])
                hook = POST_MIGRATIONS.get(v)
                if hook is not None:
                    hook(conn)
                conn.execute(f"PRAGMA user_version = {v}")

    # ------------------------------------------------------------------ #
    # Sources
    # ------------------------------------------------------------------ #

    def create_source(
        self,
        *,
        account_id: str,
        platform: str,
        channel_url: str,
        external_id: str,
        channel_name: str | None = None,
        niche_slug: str | None = None,
        tags: list[str] | None = None,
        priority: int = 100,
        interval_min: int = 60,
        profile_validated: bool = False,
        max_results_limit: int | None = None,
    ) -> SourceRow:
        source_id = str(uuid.uuid4())
        now = _now()
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO sources
                (id, account_id, platform, channel_url, external_id, channel_name,
                 niche_slug, tags_json, priority, interval_min, is_active,
                 profile_validated, added_at, max_results_limit)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    source_id,
                    account_id,
                    platform,
                    channel_url,
                    external_id,
                    channel_name,
                    niche_slug,
                    json.dumps(tags or []),
                    priority,
                    interval_min,
                    1 if profile_validated else 0,
                    now,
                    max_results_limit,
                ),
            )
        return self.get_source(source_id)  # type: ignore[return-value]

    def get_source(self, source_id: str) -> SourceRow | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return self._row_to_source(row) if row else None

    def list_sources(self, account_id: str | None = None, active_only: bool = False) -> list[SourceRow]:
        q = "SELECT * FROM sources"
        params: tuple = ()
        conds = []
        if account_id:
            conds.append("account_id = ?")
            params = params + (account_id,)
        if active_only:
            conds.append("is_active = 1")
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY added_at DESC"
        with self._conn() as c:
            rows = c.execute(q, params).fetchall()
        return [self._row_to_source(r) for r in rows]

    def update_source(
        self,
        source_id: str,
        *,
        priority: int | None = None,
        interval_min: int | None = None,
        tags: list[str] | None = None,
        is_active: bool | None = None,
        niche_slug: str | None = None,
        last_crawled_at: str | None = None,
        last_error: str | None = None,
        profile_validated: bool | None = None,
        max_results_limit: int | None = None,
    ) -> SourceRow | None:
        updates: list[str] = []
        params: list = []
        if priority is not None:
            updates.append("priority = ?")
            params.append(priority)
        if interval_min is not None:
            updates.append("interval_min = ?")
            params.append(interval_min)
        if tags is not None:
            updates.append("tags_json = ?")
            params.append(json.dumps(tags))
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if is_active else 0)
        if niche_slug is not None:
            updates.append("niche_slug = ?")
            params.append(niche_slug)
        if last_crawled_at is not None:
            updates.append("last_crawled_at = ?")
            params.append(last_crawled_at)
        if last_error is not None:
            updates.append("last_error = ?")
            params.append(last_error)
        if profile_validated is not None:
            updates.append("profile_validated = ?")
            params.append(1 if profile_validated else 0)
        if max_results_limit is not None:
            updates.append("max_results_limit = ?")
            params.append(max_results_limit)
        if not updates:
            return self.get_source(source_id)
        params.append(source_id)
        with self._conn() as c:
            c.execute(f"UPDATE sources SET {', '.join(updates)} WHERE id = ?", params)
        return self.get_source(source_id)

    def delete_source(self, source_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        return cur.rowcount > 0

    def set_self_source(self, source_id: str) -> SourceRow | None:
        """Пометить источник как 'это я'. Снимает флаг с других источников
        того же account_id (партиальный UNIQUE индекс гарантирует один self
        на аккаунт, но мы ещё и явно очищаем — чтобы UPDATE не упал)."""
        src = self.get_source(source_id)
        if src is None:
            return None
        with self._conn() as c:
            c.execute(
                "UPDATE sources SET is_self = 0 WHERE account_id = ? AND id != ?",
                (src.account_id, source_id),
            )
            c.execute("UPDATE sources SET is_self = 1 WHERE id = ?", (source_id,))
        return self.get_source(source_id)

    def clear_self_source(self, source_id: str) -> SourceRow | None:
        """Снять флаг 'это я' с источника."""
        with self._conn() as c:
            c.execute("UPDATE sources SET is_self = 0 WHERE id = ?", (source_id,))
        return self.get_source(source_id)

    def get_self_source(self, account_id: str) -> SourceRow | None:
        """Текущий self-source аккаунта (None если не помечен)."""
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM sources WHERE account_id = ? AND is_self = 1 LIMIT 1",
                (account_id,),
            ).fetchone()
        return self._row_to_source(row) if row else None

    # ------------------------------------------------------------------ #
    # Vitality helpers (для классификации «жив/спит/мёртв»)
    # ------------------------------------------------------------------ #

    def count_videos(self, source_id: str) -> int:
        """Всего видео в БД для источника."""
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM videos WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return int(row["n"])

    def count_successful_crawls(self, source_id: str) -> int:
        """Сколько раз crawl_log.status='ok' для источника."""
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM crawl_log WHERE source_id = ? AND status = 'ok'",
                (source_id,),
            ).fetchone()
        return int(row["n"])

    def days_since_latest_video(self, source_id: str) -> float | None:
        """Дни с последнего published_at видео источника. None если видео нет."""
        with self._conn() as c:
            row = c.execute(
                """SELECT MAX(published_at) AS pub FROM videos
                   WHERE source_id = ? AND published_at IS NOT NULL""",
                (source_id,),
            ).fetchone()
        if row is None or not row["pub"]:
            return None
        try:
            dt = datetime.fromisoformat(row["pub"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return round(delta.total_seconds() / 86400.0, 2)

    def is_just_resumed(self, source_id: str) -> bool:
        """True если автор вышел из молчания — последнее видео <2 дней,
        предыдущее >7 дней (большой gap = автор «вернулся»). Используется
        для UI-индикатора 🔔 «только что начал постить»."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT published_at FROM videos
                   WHERE source_id = ? AND published_at IS NOT NULL
                   ORDER BY published_at DESC LIMIT 2""",
                (source_id,),
            ).fetchall()
        if not rows:
            return False
        try:
            latest = datetime.fromisoformat(rows[0]["published_at"].replace("Z", "+00:00"))
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return False
        latest_age_days = (datetime.now(timezone.utc) - latest).total_seconds() / 86400.0
        if latest_age_days >= 2:
            return False
        if len(rows) < 2:
            return False  # один пост — не «вернулся», просто стартовал
        try:
            prev = datetime.fromisoformat(rows[1]["published_at"].replace("Z", "+00:00"))
            if prev.tzinfo is None:
                prev = prev.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return False
        prev_age_days = (datetime.now(timezone.utc) - prev).total_seconds() / 86400.0
        # Gap между последним и предыдущим ≥7 дней — автор «вернулся»
        return (prev_age_days - latest_age_days) >= 7.0

    def _row_to_source(self, row: sqlite3.Row) -> SourceRow:
        keys = row.keys() if hasattr(row, "keys") else []
        max_results = row["max_results_limit"] if "max_results_limit" in keys else None
        return SourceRow(
            id=row["id"],
            account_id=row["account_id"],
            platform=row["platform"],
            channel_url=row["channel_url"],
            external_id=row["external_id"],
            channel_name=row["channel_name"],
            niche_slug=row["niche_slug"],
            tags=json.loads(row["tags_json"] or "[]"),
            priority=row["priority"],
            interval_min=row["interval_min"],
            is_active=bool(row["is_active"]),
            profile_validated=bool(row["profile_validated"]),
            last_error=row["last_error"],
            added_at=row["added_at"],
            last_crawled_at=row["last_crawled_at"],
            max_results_limit=max_results,
            full_name=row["full_name"] if "full_name" in keys else None,
            followers_count=row["followers_count"] if "followers_count" in keys else None,
            posts_count=row["posts_count"] if "posts_count" in keys else None,
            avatar_url=row["avatar_url"] if "avatar_url" in keys else None,
            is_verified=bool(row["is_verified"]) if "is_verified" in keys and row["is_verified"] is not None else None,
            is_private=bool(row["is_private"]) if "is_private" in keys and row["is_private"] is not None else None,
            business_category=row["business_category"] if "business_category" in keys else None,
            profile_fetched_at=row["profile_fetched_at"] if "profile_fetched_at" in keys else None,
            is_self=bool(row["is_self"]) if "is_self" in keys and row["is_self"] is not None else False,
        )

    def update_profile(
        self,
        source_id: str,
        *,
        full_name: str | None = None,
        followers_count: int | None = None,
        posts_count: int | None = None,
        avatar_url: str | None = None,
        is_verified: bool | None = None,
        is_private: bool | None = None,
        business_category: str | None = None,
        now_iso: str | None = None,
    ) -> None:
        now = now_iso or _now()
        updates: list[str] = []
        params: list = []
        if full_name is not None:
            updates.append("full_name = ?")
            params.append(full_name)
        if followers_count is not None:
            updates.append("followers_count = ?")
            params.append(followers_count)
        if posts_count is not None:
            updates.append("posts_count = ?")
            params.append(posts_count)
        if avatar_url is not None:
            updates.append("avatar_url = ?")
            params.append(avatar_url)
        if is_verified is not None:
            updates.append("is_verified = ?")
            params.append(1 if is_verified else 0)
        if is_private is not None:
            updates.append("is_private = ?")
            params.append(1 if is_private else 0)
        if business_category is not None:
            updates.append("business_category = ?")
            params.append(business_category)
        updates.append("profile_fetched_at = ?")
        params.append(now)
        params.append(source_id)
        with self._conn() as c:
            c.execute(
                f"UPDATE sources SET {', '.join(updates)} WHERE id = ?", params
            )
            # Daily snapshot — один на сутки на source. Помещаем сырые
            # значения; UNIQUE(source_id, captured_date) защищает от дублей.
            today = now[:10]
            if followers_count is not None or posts_count is not None:
                c.execute(
                    """INSERT INTO profile_snapshots
                       (source_id, captured_date, followers_count, posts_count)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(source_id, captured_date) DO UPDATE
                       SET followers_count = COALESCE(excluded.followers_count, followers_count),
                           posts_count     = COALESCE(excluded.posts_count, posts_count)""",
                    (source_id, today, followers_count, posts_count),
                )

    def snapshot_all_active_profiles(self, today_iso: str | None = None) -> int:
        """Записать profile_snapshot за сегодняшний день для всех активных
        источников, где есть followers_count или posts_count. Один INSERT...
        SELECT — atomic. ON CONFLICT держит свежее значение, если пользователь
        уже жал «Обновить» сегодня. Возвращает количество вставленных строк."""
        today = (today_iso or _now())[:10]
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO profile_snapshots
                       (source_id, captured_date, followers_count, posts_count)
                   SELECT id, ?, followers_count, posts_count
                   FROM sources
                   WHERE is_active = 1
                     AND (followers_count IS NOT NULL OR posts_count IS NOT NULL)
                   ON CONFLICT(source_id, captured_date) DO UPDATE
                   SET followers_count = COALESCE(excluded.followers_count, followers_count),
                       posts_count     = COALESCE(excluded.posts_count, posts_count)""",
                (today,),
            )
            return cur.rowcount

    def list_profile_snapshots(
        self, source_id: str, *, days: int = 90
    ) -> list[tuple[str, int | None, int | None]]:
        """Список снимков профиля за последние N дней. [(date, followers, posts), ...]"""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        with self._conn() as c:
            rows = c.execute(
                """SELECT captured_date, followers_count, posts_count
                   FROM profile_snapshots
                   WHERE source_id = ? AND captured_date >= ?
                   ORDER BY captured_date ASC""",
                (source_id, cutoff),
            ).fetchall()
        return [(r["captured_date"], r["followers_count"], r["posts_count"]) for r in rows]

    def list_videos_with_metrics_for_source(
        self, source_id: str, *, limit: int = 200
    ) -> list[tuple[VideoRow, "TrendingRow | None", "SnapshotRow | None"]]:
        """Все видео источника с latest trending и latest snapshot.
        Сортировка по published_at DESC. Используется в Аналитике для
        отрисовки полной ленты рилсов автора без фильтра по времени."""
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT v.*,
                       t.id as t_id, t.computed_at, t.zscore_24h, t.growth_rate_24h,
                       t.is_trending, t.velocity as t_velocity, t.is_rising as t_rising,
                       m.id as m_id, m.captured_at as m_captured, m.views as m_views,
                       m.likes as m_likes, m.comments as m_comments,
                       m.engagement_rate as m_er
                FROM videos v
                LEFT JOIN trending_scores t ON t.id = (
                    SELECT MAX(id) FROM trending_scores WHERE video_id = v.id
                )
                LEFT JOIN metric_snapshots m ON m.id = (
                    SELECT MAX(id) FROM metric_snapshots WHERE video_id = v.id
                )
                WHERE v.source_id = ?
                ORDER BY v.published_at DESC, v.first_seen_at DESC
                LIMIT ?
                """,
                (source_id, limit),
            ).fetchall()
        result: list[tuple[VideoRow, "TrendingRow | None", "SnapshotRow | None"]] = []
        for r in rows:
            video = self._row_to_video(r)
            trending = None
            if r["t_id"] is not None:
                trending = TrendingRow(
                    id=r["t_id"], video_id=r["id"],
                    computed_at=r["computed_at"],
                    zscore_24h=r["zscore_24h"],
                    growth_rate_24h=r["growth_rate_24h"],
                    is_trending=bool(r["is_trending"]),
                    velocity=r["t_velocity"],
                    is_rising=bool(r["t_rising"]) if r["t_rising"] is not None else False,
                )
            snap = None
            if r["m_id"] is not None:
                snap = SnapshotRow(
                    id=r["m_id"], video_id=r["id"],
                    captured_at=r["m_captured"],
                    views=r["m_views"], likes=r["m_likes"],
                    comments=r["m_comments"], engagement_rate=r["m_er"],
                )
            result.append((video, trending, snap))
        return result

    def reel_stats_for_source(
        self, source_id: str, *, days: int = 30
    ) -> dict:
        """Агрегаты по рилсам автора: posts_per_week, avg ER, median velocity,
        avg duration, top hashtags. Берём последние N дней по published_at.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._conn() as c:
            rows = c.execute(
                """SELECT v.id, v.published_at, v.duration_sec, v.description,
                          v.url,
                          m.views, m.likes, m.comments, m.engagement_rate,
                          t.velocity
                   FROM videos v
                   LEFT JOIN metric_snapshots m ON m.id = (
                     SELECT MAX(id) FROM metric_snapshots WHERE video_id = v.id
                   )
                   LEFT JOIN trending_scores t ON t.id = (
                     SELECT MAX(id) FROM trending_scores WHERE video_id = v.id
                   )
                   WHERE v.source_id = ?
                     AND v.published_at IS NOT NULL
                     AND v.published_at >= ?
                   ORDER BY v.published_at DESC""",
                (source_id, cutoff),
            ).fetchall()
        return {"days": days, "rows": [dict(r) for r in rows]}

    def posting_heatmap_for_source(
        self, source_id: str, *, days: int = 180
    ) -> list[dict]:
        """Группировка рилсов автора по (день_недели, час) UTC с агрегатами
        velocity / views. Используется в Аналитике для heatmap «лучшее время
        публикации». Берём последние N дней по published_at.

        Возвращает список ячеек только для тех слотов, где есть хотя бы 1 рилс
        — фронт сам разворачивает в 7×24 grid и красит пустые ячейки.

        dow: 0=воскресенье..6=суббота (как в strftime('%w'))
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._conn() as c:
            rows = c.execute(
                """SELECT
                       CAST(strftime('%w', v.published_at) AS INTEGER) AS dow,
                       CAST(strftime('%H', v.published_at) AS INTEGER) AS hour,
                       COUNT(*) AS posts,
                       AVG(t.velocity) AS avg_velocity,
                       AVG(m.views)    AS avg_views,
                       AVG(m.engagement_rate) AS avg_er
                   FROM videos v
                   LEFT JOIN trending_scores t ON t.id = (
                     SELECT MAX(id) FROM trending_scores WHERE video_id = v.id
                   )
                   LEFT JOIN metric_snapshots m ON m.id = (
                     SELECT MAX(id) FROM metric_snapshots WHERE video_id = v.id
                   )
                   WHERE v.source_id = ?
                     AND v.published_at IS NOT NULL
                     AND v.published_at >= ?
                   GROUP BY dow, hour
                   ORDER BY dow, hour""",
                (source_id, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    def er_trend_for_source(
        self,
        source_id: str,
        *,
        days: int = 90,
        granularity: str = "week",
    ) -> list[dict]:
        """Динамика среднего ER автора за период (week|day). Для каждой
        недели/дня берём последний snapshot на видео в этом интервале (чтобы
        одно видео не задавало бэкграунд несколько раз) и усредняем ER по
        видео.

        Возвращает [{period_start, avg_er, posts_in_period}, ...] от старого к новому.
        """
        if granularity not in ("week", "day"):
            granularity = "week"
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        bucket_fmt = "%Y-%W" if granularity == "week" else "%Y-%m-%d"
        with self._conn() as c:
            rows = c.execute(
                f"""WITH ranked AS (
                       SELECT m.video_id,
                              m.captured_at,
                              m.engagement_rate,
                              strftime('{bucket_fmt}', m.captured_at) AS bucket,
                              ROW_NUMBER() OVER (
                                  PARTITION BY m.video_id,
                                               strftime('{bucket_fmt}', m.captured_at)
                                  ORDER BY m.captured_at DESC
                              ) AS rn
                       FROM metric_snapshots m
                       JOIN videos v ON v.id = m.video_id
                       WHERE v.source_id = ?
                         AND m.captured_at >= ?
                         AND m.engagement_rate IS NOT NULL
                   )
                   SELECT bucket,
                          MIN(date(captured_at)) AS period_start,
                          AVG(engagement_rate)   AS avg_er,
                          COUNT(DISTINCT video_id) AS posts_in_period
                   FROM ranked
                   WHERE rn = 1
                   GROUP BY bucket
                   ORDER BY bucket""",
                (source_id, cutoff),
            ).fetchall()
        return [
            {
                "period_start": r["period_start"],
                "avg_er": r["avg_er"],
                "posts_in_period": int(r["posts_in_period"]),
            }
            for r in rows
        ]

    def hashtag_stats_for_account(
        self,
        account_id: str,
        *,
        niche: str | None = None,
        q: str | None = None,
        days: int = 30,
        sort: str = "count",
        limit: int = 50,
    ) -> list[dict]:
        """Агрегаты по хэштегам всех источников аккаунта.
        Источник правды — таблица `video_hashtags`, заполняемая хуком
        upsert_video и backfill-миграцией V12.

        sort: count | growth | avg_views | avg_er

        Возвращает список словарей с метриками per-tag.
        """
        sort = sort if sort in ("count", "growth", "avg_views", "avg_er") else "count"
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=days)).isoformat()
        week_cutoff = (now - timedelta(days=7)).isoformat()
        prev_week_start = (now - timedelta(days=14)).isoformat()

        where = ["s.account_id = ?", "v.published_at IS NOT NULL", "v.published_at >= ?"]
        params: list = [account_id, cutoff]
        if niche:
            where.append("s.niche_slug = ?")
            params.append(niche)
        if q:
            where.append("h.tag LIKE ?")
            params.append(f"%{q.lower().lstrip('#')}%")
        where_sql = " AND ".join(where)

        sort_expr = {
            "count": "posts_count DESC",
            "growth": "weekly_growth DESC NULLS LAST, posts_last_week DESC",
            "avg_views": "avg_views DESC NULLS LAST",
            "avg_er": "avg_er DESC NULLS LAST",
        }[sort]

        with self._conn() as c:
            rows = c.execute(
                f"""SELECT h.tag,
                          COUNT(DISTINCT h.video_id) AS posts_count,
                          COUNT(DISTINCT s.id)      AS authors_using,
                          AVG(m.views)              AS avg_views,
                          AVG(m.engagement_rate)    AS avg_er,
                          SUM(CASE WHEN v.published_at >= ? THEN 1 ELSE 0 END)
                              AS posts_last_week,
                          SUM(CASE WHEN v.published_at >= ? AND v.published_at < ?
                                   THEN 1 ELSE 0 END)
                              AS prev_week
                   FROM video_hashtags h
                   JOIN videos v  ON v.id = h.video_id
                   JOIN sources s ON s.id = v.source_id
                   LEFT JOIN metric_snapshots m ON m.id = (
                     SELECT MAX(id) FROM metric_snapshots WHERE video_id = v.id
                   )
                   WHERE {where_sql}
                   GROUP BY h.tag
                   ORDER BY {sort_expr}
                   LIMIT ?""",
                (week_cutoff, prev_week_start, week_cutoff, *params, limit),
            ).fetchall()

        out: list[dict] = []
        for r in rows:
            this_w = int(r["posts_last_week"] or 0)
            prev_w = int(r["prev_week"] or 0)
            growth: float | None = None
            if prev_w > 0:
                growth = (this_w - prev_w) / prev_w
            elif this_w > 0:
                growth = None  # rolling start, нет базы для сравнения
            out.append({
                "tag": r["tag"],
                "posts_count": int(r["posts_count"]),
                "authors_using": int(r["authors_using"]),
                "avg_views": r["avg_views"],
                "avg_er": r["avg_er"],
                "posts_last_week": this_w,
                "prev_week": prev_w,
                "weekly_growth": growth,
            })
        # Если sort=growth — пересортируем в Python (SQLite NULLS LAST поведение
        # неконсистентно; делаем явный stable sort).
        if sort == "growth":
            out.sort(
                key=lambda x: (
                    x["weekly_growth"] if x["weekly_growth"] is not None else -1.0,
                    x["posts_last_week"],
                ),
                reverse=True,
            )
        return out

    def videos_by_hashtag(
        self,
        account_id: str,
        tag: str,
        *,
        days: int = 30,
        limit: int = 20,
    ) -> list[dict]:
        """Drill-down: топ рилсов с заданным хэштегом за N дней по аккаунту.
        Сортировка по просмотрам DESC."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._conn() as c:
            rows = c.execute(
                """SELECT v.id           AS video_id,
                          v.platform,
                          v.url,
                          v.title,
                          v.description,
                          v.thumbnail_url,
                          v.duration_sec,
                          v.published_at,
                          v.niche_slug,
                          s.id           AS source_id,
                          s.external_id  AS handle,
                          s.channel_name AS channel_name,
                          s.is_self      AS is_self,
                          m.views        AS current_views,
                          m.likes        AS current_likes,
                          m.comments     AS current_comments,
                          m.engagement_rate AS engagement_rate,
                          t.velocity     AS velocity
                   FROM video_hashtags h
                   JOIN videos v  ON v.id = h.video_id
                   JOIN sources s ON s.id = v.source_id
                   LEFT JOIN metric_snapshots m ON m.id = (
                     SELECT MAX(id) FROM metric_snapshots WHERE video_id = v.id
                   )
                   LEFT JOIN trending_scores t ON t.id = (
                     SELECT MAX(id) FROM trending_scores WHERE video_id = v.id
                   )
                   WHERE h.tag = ?
                     AND s.account_id = ?
                     AND v.published_at IS NOT NULL
                     AND v.published_at >= ?
                   ORDER BY COALESCE(m.views, 0) DESC, v.published_at DESC
                   LIMIT ?""",
                (tag.lower().lstrip("#"), account_id, cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def find_source_by_external_id(self, external_id: str) -> "SourceRow | None":
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM sources WHERE external_id = ? LIMIT 1",
                (external_id,),
            ).fetchone()
        return self._row_to_source(row) if row else None

    # ------------------------------------------------------------------ #
    # Videos
    # ------------------------------------------------------------------ #

    def upsert_video(
        self,
        *,
        source_id: str,
        platform: str,
        external_id: str,
        url: str,
        title: str | None = None,
        description: str | None = None,
        thumbnail_url: str | None = None,
        duration_sec: int | None = None,
        published_at: str | None = None,
        is_short: bool | None = None,
        niche_slug: str | None = None,
    ) -> tuple[VideoRow, bool]:
        """Возвращает (row, is_new). is_new=True если видео вставлено впервые."""
        is_short_val = 1 if is_short else 0
        with self._conn() as c:
            existing = c.execute(
                "SELECT id FROM videos WHERE platform = ? AND external_id = ?",
                (platform, external_id),
            ).fetchone()
            if existing:
                video_id = existing["id"]
                c.execute(
                    """
                    UPDATE videos SET title = COALESCE(?, title),
                                      description = COALESCE(?, description),
                                      thumbnail_url = COALESCE(?, thumbnail_url),
                                      duration_sec = COALESCE(?, duration_sec),
                                      niche_slug = COALESCE(?, niche_slug)
                                      """ + (", is_short = ?" if is_short is not None else "") + """
                    WHERE id = ?
                    """,
                    (title, description, thumbnail_url, duration_sec, niche_slug)
                    + ((is_short_val,) if is_short is not None else ())
                    + (video_id,),
                )
                is_new = False
            else:
                video_id = str(uuid.uuid4())
                c.execute(
                    """
                    INSERT INTO videos
                    (id, source_id, platform, external_id, url, title, description,
                     thumbnail_url, duration_sec, published_at, first_seen_at, is_short,
                     niche_slug)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        video_id, source_id, platform, external_id, url, title,
                        description, thumbnail_url, duration_sec, published_at,
                        _now(), is_short_val, niche_slug,
                    ),
                )
                is_new = True
            # Sync video_hashtags если description предоставлен. Только тогда —
            # иначе update без description ($COALESCE keep существующий) ошибочно
            # обнулил бы теги.
            if description is not None:
                self._sync_video_hashtags(c, video_id, description)
        return self.get_video(video_id), is_new  # type: ignore[return-value]

    @staticmethod
    def _sync_video_hashtags(
        conn: sqlite3.Connection, video_id: str, description: str | None
    ) -> None:
        """Перезаписать строки video_hashtags для видео из description.
        Идемпотентно: DELETE+INSERT в одной транзакции."""
        conn.execute("DELETE FROM video_hashtags WHERE video_id = ?", (video_id,))
        if not description:
            return
        tags = {t.lower() for t in _HASHTAG_RE.findall(description)}
        if tags:
            conn.executemany(
                "INSERT OR IGNORE INTO video_hashtags (video_id, tag) VALUES (?, ?)",
                [(video_id, t) for t in tags],
            )

    def update_video_duration(
        self, video_id: str, duration_sec: int, is_short: bool | None = None
    ) -> None:
        """Обновить длительность (+ опц. флаг is_short). Вызывается из crawler
        после fetch_metrics, если платформа вернула duration."""
        if is_short is None:
            is_short = 0 < duration_sec <= 60
        with self._conn() as c:
            c.execute(
                "UPDATE videos SET duration_sec = ?, is_short = ? WHERE id = ?",
                (duration_sec, 1 if is_short else 0, video_id),
            )

    def get_video(self, video_id: str) -> VideoRow | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return self._row_to_video(row) if row else None

    def list_videos(self, source_id: str, limit: int = 50) -> list[VideoRow]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM videos WHERE source_id = ?
                   ORDER BY COALESCE(published_at, first_seen_at) DESC LIMIT ?""",
                (source_id, limit),
            ).fetchall()
        return [self._row_to_video(r) for r in rows]

    def list_recent_videos(self, source_id: str, since: str) -> list[VideoRow]:
        """Видео источника с published_at >= since (ISO)."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM videos WHERE source_id = ? AND published_at >= ?
                   ORDER BY published_at DESC""",
                (source_id, since),
            ).fetchall()
        return [self._row_to_video(r) for r in rows]

    def _row_to_video(self, row: sqlite3.Row) -> VideoRow:
        # is_short / niche_slug добавлены в более поздних схемах; читаем с fallback
        try:
            is_short = bool(row["is_short"])
        except (IndexError, KeyError):
            is_short = False
        try:
            niche_slug = row["niche_slug"]
        except (IndexError, KeyError):
            niche_slug = None
        return VideoRow(
            id=row["id"],
            source_id=row["source_id"],
            platform=row["platform"],
            external_id=row["external_id"],
            url=row["url"],
            title=row["title"],
            description=row["description"],
            thumbnail_url=row["thumbnail_url"],
            duration_sec=row["duration_sec"],
            published_at=row["published_at"],
            first_seen_at=row["first_seen_at"],
            is_short=is_short,
            niche_slug=niche_slug,
        )

    # ------------------------------------------------------------------ #
    # Snapshots
    # ------------------------------------------------------------------ #

    def insert_snapshot(
        self,
        *,
        video_id: str,
        views: int,
        likes: int,
        comments: int,
        captured_at: str | None = None,
    ) -> SnapshotRow:
        captured_at = captured_at or _now()
        engagement_rate = None
        if views > 0:
            engagement_rate = (likes + comments) / views
        with self._conn() as c:
            cur = c.execute(
                """INSERT OR IGNORE INTO metric_snapshots
                   (video_id, captured_at, views, likes, comments, engagement_rate)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (video_id, captured_at, views, likes, comments, engagement_rate),
            )
            snap_id = cur.lastrowid
            row = c.execute(
                "SELECT * FROM metric_snapshots WHERE id = ?", (snap_id,)
            ).fetchone()
        return self._row_to_snapshot(row)  # type: ignore[arg-type]

    def get_snapshot_at_least_hours_ago(
        self, video_id: str, hours: float
    ) -> SnapshotRow | None:
        """Самый свежий snapshot, которому исполнилось ≥ hours часов.
        Используется для views_N_hours_ago, независимо от частоты crawl-ов.
        Возвращает None если таких snapshots нет.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self._conn() as c:
            row = c.execute(
                """SELECT * FROM metric_snapshots
                   WHERE video_id = ? AND captured_at <= ?
                   ORDER BY captured_at DESC LIMIT 1""",
                (video_id, cutoff),
            ).fetchone()
        return self._row_to_snapshot(row) if row else None

    def compute_niche_velocity_percentile(
        self, niche_slug: str, velocity: float
    ) -> float | None:
        """Доля видео в той же нише (across ALL accounts) с velocity <= заданному.
        Возвращает float в [0, 1]. None если в нише < 10 видео с вычисленной velocity.
        """
        if velocity is None or velocity <= 0:
            return None
        with self._conn() as c:
            rows = c.execute(
                """SELECT t.velocity FROM trending_scores t
                   JOIN (
                     SELECT MAX(id) AS max_id FROM trending_scores GROUP BY video_id
                   ) latest ON t.id = latest.max_id
                   JOIN videos v ON v.id = t.video_id
                   JOIN sources s ON s.id = v.source_id
                   WHERE s.niche_slug = ? AND t.velocity IS NOT NULL AND t.velocity > 0""",
                (niche_slug,),
            ).fetchall()
        values = [r[0] for r in rows]
        if len(values) < 10:
            return None
        below = sum(1 for v in values if v <= velocity)
        return below / len(values)

    def list_snapshots(self, video_id: str, limit: int = 100) -> list[SnapshotRow]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT * FROM metric_snapshots WHERE video_id = ?
                   ORDER BY captured_at DESC LIMIT ?""",
                (video_id, limit),
            ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def _row_to_snapshot(self, row: sqlite3.Row) -> SnapshotRow:
        return SnapshotRow(
            id=row["id"],
            video_id=row["video_id"],
            captured_at=row["captured_at"],
            views=row["views"],
            likes=row["likes"],
            comments=row["comments"],
            engagement_rate=row["engagement_rate"],
        )

    def latest_snapshot(self, video_id: str) -> SnapshotRow | None:
        with self._conn() as c:
            row = c.execute(
                """SELECT * FROM metric_snapshots WHERE video_id = ?
                   ORDER BY captured_at DESC LIMIT 1""",
                (video_id,),
            ).fetchone()
        return self._row_to_snapshot(row) if row else None

    # ------------------------------------------------------------------ #
    # Trending
    # ------------------------------------------------------------------ #

    def upsert_trending(
        self,
        *,
        video_id: str,
        zscore_24h: float | None,
        growth_rate_24h: float | None,
        is_trending: bool,
        velocity: float | None = None,
        is_rising: bool = False,
        computed_at: str | None = None,
    ) -> TrendingRow:
        computed_at = computed_at or _now()
        with self._conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO trending_scores
                   (video_id, computed_at, zscore_24h, growth_rate_24h, is_trending,
                    velocity, is_rising)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (video_id, computed_at, zscore_24h, growth_rate_24h,
                 1 if is_trending else 0, velocity, 1 if is_rising else 0),
            )
            row = c.execute(
                """SELECT * FROM trending_scores WHERE video_id = ? AND computed_at = ?""",
                (video_id, computed_at),
            ).fetchone()
        return self._row_to_trending(row)  # type: ignore[arg-type]

    def latest_trending(self, video_id: str) -> TrendingRow | None:
        with self._conn() as c:
            row = c.execute(
                """SELECT * FROM trending_scores WHERE video_id = ?
                   ORDER BY computed_at DESC LIMIT 1""",
                (video_id,),
            ).fetchone()
        return self._row_to_trending(row) if row else None

    def list_trending_for_account(
        self, account_id: str, limit: int = 20
    ) -> list[tuple[VideoRow, TrendingRow, SourceRow]]:
        """Топ trending видео по всем source аккаунта — последние computed_at per video."""
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT v.*, s.channel_name as source_channel_name, s.account_id as src_account_id,
                       t.id as t_id, t.computed_at, t.zscore_24h, t.growth_rate_24h, t.is_trending,
                       s.id as src_id, s.platform as src_platform, s.channel_url as src_channel_url,
                       s.external_id as src_external_id, s.niche_slug as src_niche_slug,
                       s.tags_json as src_tags, s.priority as src_priority,
                       s.interval_min as src_interval, s.is_active as src_active,
                       s.profile_validated as src_pv, s.last_error as src_error,
                       s.added_at as src_added, s.last_crawled_at as src_lc,
                       t.velocity as t_velocity, t.is_rising as t_rising,
                       s.full_name as src_full_name, s.followers_count as src_followers,
                       s.posts_count as src_posts, s.avatar_url as src_avatar,
                       s.is_verified as src_verified, s.is_private as src_is_private,
                       s.business_category as src_biz_cat, s.profile_fetched_at as src_profile_at
                FROM trending_scores t
                JOIN videos v ON v.id = t.video_id
                JOIN sources s ON s.id = v.source_id
                WHERE s.account_id = ? AND t.is_trending = 1
                  AND t.id IN (
                      SELECT MAX(id) FROM trending_scores GROUP BY video_id
                  )
                ORDER BY t.velocity DESC
                LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
        result: list[tuple[VideoRow, TrendingRow, SourceRow]] = []
        for r in rows:
            video = self._row_to_video(r)
            trending = TrendingRow(
                id=r["t_id"],
                video_id=r["id"],
                computed_at=r["computed_at"],
                zscore_24h=r["zscore_24h"],
                growth_rate_24h=r["growth_rate_24h"],
                is_trending=bool(r["is_trending"]),
                velocity=r["t_velocity"],
                is_rising=bool(r["t_rising"]) if r["t_rising"] is not None else False,
            )
            source = SourceRow(
                id=r["src_id"],
                account_id=r["src_account_id"],
                platform=r["src_platform"],
                channel_url=r["src_channel_url"],
                external_id=r["src_external_id"],
                channel_name=r["source_channel_name"],
                niche_slug=r["src_niche_slug"],
                tags=json.loads(r["src_tags"] or "[]"),
                priority=r["src_priority"],
                interval_min=r["src_interval"],
                is_active=bool(r["src_active"]),
                profile_validated=bool(r["src_pv"]),
                last_error=r["src_error"],
                added_at=r["src_added"],
                last_crawled_at=r["src_lc"],
                full_name=r["src_full_name"],
                followers_count=r["src_followers"],
                posts_count=r["src_posts"],
                avatar_url=r["src_avatar"],
                is_verified=bool(r["src_verified"]) if r["src_verified"] is not None else None,
                is_private=bool(r["src_is_private"]) if r["src_is_private"] is not None else None,
                business_category=r["src_biz_cat"],
                profile_fetched_at=r["src_profile_at"],
            )
            result.append((video, trending, source))
        return result

    def list_recent_videos_for_account(
        self,
        account_id: str,
        limit: int = 50,
        days: int | None = None,
    ) -> list[tuple[VideoRow, "TrendingRow | None", SourceRow]]:
        """Все недавние видео аккаунта (по всем source), с последним trending-score если есть.
        Используется для Monitor-таба когда нужно показать всё, а не только is_trending=1.

        days: если задан, ограничивает видео опубликованными/впервые увиденными за N дней.
        """
        params: tuple = (account_id,)
        where_time = ""
        if days is not None and days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            where_time = " AND COALESCE(v.published_at, v.first_seen_at) >= ?"
            params = (account_id, cutoff)
        with self._conn() as c:
            rows = c.execute(
                f"""
                SELECT v.*, s.channel_name as source_channel_name, s.account_id as src_account_id,
                       t.id as t_id, t.computed_at, t.zscore_24h, t.growth_rate_24h, t.is_trending,
                       t.velocity as t_velocity, t.is_rising as t_rising,
                       s.id as src_id, s.platform as src_platform, s.channel_url as src_channel_url,
                       s.external_id as src_external_id, s.niche_slug as src_niche_slug,
                       s.tags_json as src_tags, s.priority as src_priority,
                       s.interval_min as src_interval, s.is_active as src_active,
                       s.profile_validated as src_pv, s.last_error as src_error,
                       s.added_at as src_added, s.last_crawled_at as src_lc,
                       s.max_results_limit as src_max_results,
                       s.full_name as src_full_name, s.followers_count as src_followers,
                       s.posts_count as src_posts, s.avatar_url as src_avatar,
                       s.is_verified as src_verified, s.is_private as src_is_private,
                       s.business_category as src_biz_cat, s.profile_fetched_at as src_profile_at
                FROM videos v
                JOIN sources s ON s.id = v.source_id
                LEFT JOIN trending_scores t ON t.id = (
                    SELECT MAX(id) FROM trending_scores WHERE video_id = v.id
                )
                WHERE s.account_id = ?{where_time}
                ORDER BY v.published_at DESC, v.first_seen_at DESC
                LIMIT ?
                """,
                params + (limit,),
            ).fetchall()
        result: list[tuple[VideoRow, "TrendingRow | None", SourceRow]] = []
        for r in rows:
            video = self._row_to_video(r)
            trending = None
            if r["t_id"] is not None:
                trending = TrendingRow(
                    id=r["t_id"],
                    video_id=r["id"],
                    computed_at=r["computed_at"],
                    zscore_24h=r["zscore_24h"],
                    growth_rate_24h=r["growth_rate_24h"],
                    is_trending=bool(r["is_trending"]),
                    velocity=r["t_velocity"],
                    is_rising=bool(r["t_rising"]) if r["t_rising"] is not None else False,
                )
            source = SourceRow(
                id=r["src_id"],
                account_id=r["src_account_id"],
                platform=r["src_platform"],
                channel_url=r["src_channel_url"],
                external_id=r["src_external_id"],
                channel_name=r["source_channel_name"],
                niche_slug=r["src_niche_slug"],
                tags=json.loads(r["src_tags"] or "[]"),
                priority=r["src_priority"],
                interval_min=r["src_interval"],
                is_active=bool(r["src_active"]),
                profile_validated=bool(r["src_pv"]),
                last_error=r["src_error"],
                added_at=r["src_added"],
                last_crawled_at=r["src_lc"],
                max_results_limit=r["src_max_results"],
                full_name=r["src_full_name"],
                followers_count=r["src_followers"],
                posts_count=r["src_posts"],
                avatar_url=r["src_avatar"],
                is_verified=bool(r["src_verified"]) if r["src_verified"] is not None else None,
                is_private=bool(r["src_is_private"]) if r["src_is_private"] is not None else None,
                business_category=r["src_biz_cat"],
                profile_fetched_at=r["src_profile_at"],
            )
            result.append((video, trending, source))
        return result

    def _row_to_trending(self, row: sqlite3.Row) -> TrendingRow:
        keys = row.keys() if hasattr(row, "keys") else []
        velocity = row["velocity"] if "velocity" in keys else None
        is_rising = bool(row["is_rising"]) if "is_rising" in keys and row["is_rising"] is not None else False
        return TrendingRow(
            id=row["id"],
            video_id=row["video_id"],
            computed_at=row["computed_at"],
            zscore_24h=row["zscore_24h"],
            growth_rate_24h=row["growth_rate_24h"],
            is_trending=bool(row["is_trending"]),
            velocity=velocity,
            is_rising=is_rising,
        )

    # ------------------------------------------------------------------ #
    # Crawl log
    # ------------------------------------------------------------------ #

    def start_crawl(self, source_id: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO crawl_log (source_id, started_at, status)
                   VALUES (?, ?, 'running')""",
                (source_id, _now()),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def finish_crawl(
        self,
        log_id: int,
        *,
        status: str,
        videos_new: int = 0,
        videos_updated: int = 0,
        error: str | None = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                """UPDATE crawl_log
                   SET finished_at = ?, status = ?, videos_new = ?, videos_updated = ?, error = ?
                   WHERE id = ?""",
                (_now(), status, videos_new, videos_updated, error, log_id),
            )

    def list_crawl_log(self, source_id: str | None = None, limit: int = 50) -> list[CrawlLogRow]:
        with self._conn() as c:
            if source_id:
                rows = c.execute(
                    """SELECT * FROM crawl_log WHERE source_id = ?
                       ORDER BY started_at DESC LIMIT ?""",
                    (source_id, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    """SELECT * FROM crawl_log
                       ORDER BY started_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return [self._row_to_crawl_log(r) for r in rows]

    def mark_stale_crawls_as_failed(self, older_than_minutes: int = 10) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)).isoformat()
        with self._conn() as c:
            cur = c.execute(
                """UPDATE crawl_log
                   SET status = 'failed', finished_at = ?, error = 'stale_at_startup'
                   WHERE status = 'running' AND started_at < ?""",
                (_now(), cutoff),
            )
            return cur.rowcount

    def count_running_crawls(self) -> int:
        with self._conn() as c:
            row = c.execute("SELECT COUNT(*) as n FROM crawl_log WHERE status = 'running'").fetchone()
        return int(row["n"])

    def _row_to_crawl_log(self, row: sqlite3.Row) -> CrawlLogRow:
        return CrawlLogRow(
            id=row["id"],
            source_id=row["source_id"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            status=row["status"],
            videos_new=row["videos_new"],
            videos_updated=row["videos_updated"],
            error=row["error"],
        )

    # ------------------------------------------------------------------ #
    # YouTube quota
    # ------------------------------------------------------------------ #

    def increment_quota(self, units: int, date: str | None = None) -> int:
        date = date or _today_pt()
        with self._conn() as c:
            c.execute(
                """INSERT INTO youtube_quota (date, units_used) VALUES (?, ?)
                   ON CONFLICT(date) DO UPDATE SET units_used = units_used + ?""",
                (date, units, units),
            )
            row = c.execute(
                "SELECT units_used FROM youtube_quota WHERE date = ?", (date,)
            ).fetchone()
        return int(row["units_used"])

    def get_quota(self, date: str | None = None) -> int:
        date = date or _today_pt()
        with self._conn() as c:
            row = c.execute(
                "SELECT units_used FROM youtube_quota WHERE date = ?", (date,)
            ).fetchone()
        return int(row["units_used"]) if row else 0

    # ------------------------------------------------------------------ #
    # Apify usage
    # ------------------------------------------------------------------ #

    def record_apify_run(
        self, platform: str, items: int, *, actor_kind: str = "reel", date: str | None = None
    ) -> None:
        """Учёт одного Apify-запуска для платформы.
        actor_kind позволяет различать reel vs profile запуски в биллинге."""
        date = date or _today_pt()
        billing_key = f"{platform}-{actor_kind}" if actor_kind != "reel" else platform
        with self._conn() as c:
            c.execute(
                """INSERT INTO apify_usage (date, platform, runs, items)
                   VALUES (?, ?, 1, ?)
                   ON CONFLICT(date, platform) DO UPDATE
                     SET runs = runs + 1, items = items + excluded.items""",
                (date, billing_key, items),
            )

    def get_apify_usage(
        self, date: str | None = None
    ) -> list[tuple[str, int, int]]:
        """Возвращает [(platform, runs, items), ...] за дату."""
        date = date or _today_pt()
        with self._conn() as c:
            rows = c.execute(
                "SELECT platform, runs, items FROM apify_usage WHERE date = ? ORDER BY platform",
                (date,),
            ).fetchall()
        return [(r["platform"], int(r["runs"]), int(r["items"])) for r in rows]

    # ------------------------------------------------------------------ #
    # Plan limits (singleton row, id=1)
    # ------------------------------------------------------------------ #

    def get_plan(self) -> PlanRow:
        with self._conn() as c:
            row = c.execute("SELECT * FROM plan_limits WHERE id = 1").fetchone()
        # Миграция v3 вставляет row по умолчанию. Если её нет — паника.
        assert row is not None, "plan_limits singleton missing (migration v3 not applied)"
        return PlanRow(
            plan_name=row["plan_name"],
            max_sources_total=row["max_sources_total"],
            min_interval_min=row["min_interval_min"],
            max_results_limit=row["max_results_limit"],
            crawl_anchor_utc=row["crawl_anchor_utc"],
            updated_at=row["updated_at"],
        )

    def update_plan(
        self,
        *,
        plan_name: str | None = None,
        max_sources_total: int | None = None,
        min_interval_min: int | None = None,
        max_results_limit: int | None = None,
        crawl_anchor_utc: str | None = None,
    ) -> PlanRow:
        updates: list[str] = []
        params: list = []
        if plan_name is not None:
            updates.append("plan_name = ?")
            params.append(plan_name)
        if max_sources_total is not None:
            updates.append("max_sources_total = ?")
            params.append(max_sources_total)
        if min_interval_min is not None:
            updates.append("min_interval_min = ?")
            params.append(min_interval_min)
        if max_results_limit is not None:
            updates.append("max_results_limit = ?")
            params.append(max_results_limit)
        if crawl_anchor_utc is not None:
            updates.append("crawl_anchor_utc = ?")
            params.append(crawl_anchor_utc)
        updates.append("updated_at = ?")
        params.append(_now())
        with self._conn() as c:
            c.execute(
                f"UPDATE plan_limits SET {', '.join(updates)} WHERE id = 1", params
            )
        return self.get_plan()

    def count_sources_total(self) -> int:
        with self._conn() as c:
            row = c.execute("SELECT COUNT(*) as n FROM sources").fetchone()
        return int(row["n"])

    # ------------------------------------------------------------------ #
    # Health helpers
    # ------------------------------------------------------------------ #

    def count_active_sources(self) -> int:
        with self._conn() as c:
            row = c.execute("SELECT COUNT(*) as n FROM sources WHERE is_active = 1").fetchone()
        return int(row["n"])

    def last_crawl_time(self) -> str | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(finished_at) as t FROM crawl_log WHERE status = 'ok'"
            ).fetchone()
        return row["t"] if row else None

    # ------------------------------------------------------------------ #
    # Watchlist — «на мониторинге»
    # ------------------------------------------------------------------ #

    def list_watchlist_candidates(
        self,
        source_id: str,
        since_iso: str,
        min_age_hours: float = 2.0,
    ) -> list[tuple[VideoRow, TrendingRow, SnapshotRow]]:
        """Кандидаты для ежедневного отбора — свежие видео источника с посчитанной
        velocity, отсортированные по velocity DESC. Фильтры:
          - published_at >= since_iso (окно свежести, обычно 48ч)
          - hours_since_published >= min_age_hours (анти-шум: "5 views в 30 мин")
          - есть latest trending + latest snapshot

        Возвращает [(video, latest_trending, latest_snapshot), ...]
        """
        min_age_cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=min_age_hours)
        ).isoformat()
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT v.*,
                       t.id as t_id, t.computed_at, t.zscore_24h, t.growth_rate_24h,
                       t.is_trending, t.velocity as t_velocity, t.is_rising as t_rising,
                       m.id as m_id, m.captured_at as m_captured, m.views as m_views,
                       m.likes as m_likes, m.comments as m_comments,
                       m.engagement_rate as m_er
                FROM videos v
                JOIN trending_scores t ON t.id = (
                    SELECT MAX(id) FROM trending_scores WHERE video_id = v.id
                )
                JOIN metric_snapshots m ON m.id = (
                    SELECT MAX(id) FROM metric_snapshots WHERE video_id = v.id
                )
                WHERE v.source_id = ?
                  AND v.published_at IS NOT NULL
                  AND v.published_at >= ?
                  AND v.published_at <= ?
                  AND t.velocity IS NOT NULL
                ORDER BY t.velocity DESC
                """,
                (source_id, since_iso, min_age_cutoff),
            ).fetchall()
        result: list[tuple[VideoRow, TrendingRow, SnapshotRow]] = []
        for r in rows:
            video = self._row_to_video(r)
            trending = TrendingRow(
                id=r["t_id"],
                video_id=r["id"],
                computed_at=r["computed_at"],
                zscore_24h=r["zscore_24h"],
                growth_rate_24h=r["growth_rate_24h"],
                is_trending=bool(r["is_trending"]),
                velocity=r["t_velocity"],
                is_rising=bool(r["t_rising"]) if r["t_rising"] is not None else False,
            )
            snap = SnapshotRow(
                id=r["m_id"],
                video_id=r["id"],
                captured_at=r["m_captured"],
                views=r["m_views"],
                likes=r["m_likes"],
                comments=r["m_comments"],
                engagement_rate=r["m_er"],
            )
            result.append((video, trending, snap))
        return result

    def is_watched(self, video_id: str) -> bool:
        """True если есть активная запись watchlist для видео."""
        with self._conn() as c:
            row = c.execute(
                """SELECT 1 FROM watchlist
                   WHERE video_id = ? AND status = 'active' LIMIT 1""",
                (video_id,),
            ).fetchone()
        return row is not None

    def add_to_watchlist(
        self,
        *,
        video_id: str,
        source_id: str,
        published_at: str | None,
        initial_views: int,
        initial_velocity: float | None,
        ttl_days: int,
        reason: str = "daily_topn",
        now_iso: str | None = None,
    ) -> WatchlistRow | None:
        """Добавить видео в watchlist. Идемпотентно: если уже есть active запись —
        возвращает её без изменений. expires_at = published_at + ttl_days
        (если published_at нет — now + ttl_days). Возвращает None если insert
        провалился по UNIQUE (крайне редкий race)."""
        existing = self._get_active_watchlist_for_video(video_id)
        if existing is not None:
            return existing
        added = now_iso or _now()
        base = published_at or added
        try:
            base_dt = datetime.fromisoformat(base.replace("Z", "+00:00"))
        except ValueError:
            base_dt = datetime.now(timezone.utc)
        if base_dt.tzinfo is None:
            base_dt = base_dt.replace(tzinfo=timezone.utc)
        expires = (base_dt + timedelta(days=ttl_days)).isoformat()
        with self._conn() as c:
            try:
                cur = c.execute(
                    """INSERT INTO watchlist
                       (video_id, source_id, added_at, expires_at,
                        initial_views, initial_velocity, reason, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'active')""",
                    (video_id, source_id, added, expires,
                     initial_views, initial_velocity, reason),
                )
                row_id = cur.lastrowid
            except sqlite3.IntegrityError:
                return self._get_active_watchlist_for_video(video_id)
            row = c.execute(
                "SELECT * FROM watchlist WHERE id = ?", (row_id,)
            ).fetchone()
        return self._row_to_watchlist(row) if row else None

    def _get_active_watchlist_for_video(
        self, video_id: str
    ) -> WatchlistRow | None:
        with self._conn() as c:
            row = c.execute(
                """SELECT * FROM watchlist
                   WHERE video_id = ? AND status = 'active'
                   ORDER BY added_at DESC LIMIT 1""",
                (video_id,),
            ).fetchone()
        return self._row_to_watchlist(row) if row else None

    def list_watchlist(
        self,
        account_id: str,
        *,
        status: str | None = "active",
    ) -> list[tuple[VideoRow, WatchlistRow, SourceRow, SnapshotRow | None,
                    TrendingRow | None]]:
        """Список watchlist-записей для аккаунта. status=None → все статусы."""
        params: list = [account_id]
        where_status = ""
        if status is not None:
            where_status = " AND w.status = ?"
            params.append(status)
        with self._conn() as c:
            rows = c.execute(
                f"""
                SELECT v.*, s.account_id as src_account_id,
                       s.channel_name as source_channel_name,
                       s.id as src_id, s.platform as src_platform,
                       s.channel_url as src_channel_url, s.external_id as src_external_id,
                       s.niche_slug as src_niche_slug, s.tags_json as src_tags,
                       s.priority as src_priority, s.interval_min as src_interval,
                       s.is_active as src_active, s.profile_validated as src_pv,
                       s.last_error as src_error, s.added_at as src_added,
                       s.last_crawled_at as src_lc,
                       s.max_results_limit as src_max_results,
                       s.full_name as src_full_name, s.followers_count as src_followers,
                       s.posts_count as src_posts, s.avatar_url as src_avatar,
                       s.is_verified as src_verified, s.is_private as src_is_private,
                       s.business_category as src_biz_cat, s.profile_fetched_at as src_profile_at,
                       w.id as w_id, w.added_at as w_added_at,
                       w.expires_at as w_expires_at, w.initial_views as w_initial_views,
                       w.initial_velocity as w_initial_velocity, w.reason as w_reason,
                       w.status as w_status, w.graduated_at as w_graduated_at,
                       w.hit_reason as w_hit_reason, w.closed_at as w_closed_at,
                       m.id as m_id, m.captured_at as m_captured, m.views as m_views,
                       m.likes as m_likes, m.comments as m_comments, m.engagement_rate as m_er,
                       t.id as t_id, t.computed_at as t_computed, t.zscore_24h as t_zscore,
                       t.growth_rate_24h as t_growth, t.is_trending as t_is_trend,
                       t.velocity as t_velocity, t.is_rising as t_rising
                FROM watchlist w
                JOIN videos v ON v.id = w.video_id
                JOIN sources s ON s.id = w.source_id
                LEFT JOIN metric_snapshots m ON m.id = (
                    SELECT MAX(id) FROM metric_snapshots WHERE video_id = v.id
                )
                LEFT JOIN trending_scores t ON t.id = (
                    SELECT MAX(id) FROM trending_scores WHERE video_id = v.id
                )
                WHERE s.account_id = ?{where_status}
                  AND s.is_active = 1
                  AND w.id = (SELECT MIN(id) FROM watchlist ww WHERE ww.video_id = w.video_id AND ww.status = w.status)
                ORDER BY w.added_at DESC
                """,
                params,
            ).fetchall()
        result: list[tuple[VideoRow, WatchlistRow, SourceRow,
                           SnapshotRow | None, TrendingRow | None]] = []
        for r in rows:
            video = self._row_to_video(r)
            watchlist = WatchlistRow(
                id=r["w_id"],
                video_id=r["id"],
                source_id=r["src_id"],
                added_at=r["w_added_at"],
                expires_at=r["w_expires_at"],
                initial_views=r["w_initial_views"],
                initial_velocity=r["w_initial_velocity"],
                reason=r["w_reason"],
                status=r["w_status"],
                graduated_at=r["w_graduated_at"],
                hit_reason=r["w_hit_reason"],
                closed_at=r["w_closed_at"],
            )
            source = SourceRow(
                id=r["src_id"],
                account_id=r["src_account_id"],
                platform=r["src_platform"],
                channel_url=r["src_channel_url"],
                external_id=r["src_external_id"],
                channel_name=r["source_channel_name"],
                niche_slug=r["src_niche_slug"],
                tags=json.loads(r["src_tags"] or "[]"),
                priority=r["src_priority"],
                interval_min=r["src_interval"],
                is_active=bool(r["src_active"]),
                profile_validated=bool(r["src_pv"]),
                last_error=r["src_error"],
                added_at=r["src_added"],
                last_crawled_at=r["src_lc"],
                max_results_limit=r["src_max_results"],
                full_name=r["src_full_name"],
                followers_count=r["src_followers"],
                posts_count=r["src_posts"],
                avatar_url=r["src_avatar"],
                is_verified=bool(r["src_verified"]) if r["src_verified"] is not None else None,
                is_private=bool(r["src_is_private"]) if r["src_is_private"] is not None else None,
                business_category=r["src_biz_cat"],
                profile_fetched_at=r["src_profile_at"],
            )
            snap = None
            if r["m_id"] is not None:
                snap = SnapshotRow(
                    id=r["m_id"],
                    video_id=r["id"],
                    captured_at=r["m_captured"],
                    views=r["m_views"],
                    likes=r["m_likes"],
                    comments=r["m_comments"],
                    engagement_rate=r["m_er"],
                )
            trending = None
            if r["t_id"] is not None:
                trending = TrendingRow(
                    id=r["t_id"],
                    video_id=r["id"],
                    computed_at=r["t_computed"],
                    zscore_24h=r["t_zscore"],
                    growth_rate_24h=r["t_growth"],
                    is_trending=bool(r["t_is_trend"]),
                    velocity=r["t_velocity"],
                    is_rising=bool(r["t_rising"]) if r["t_rising"] is not None else False,
                )
            result.append((video, watchlist, source, snap, trending))
        return result

    def list_active_watchlist_all(self) -> list[WatchlistRow]:
        """Все активные watchlist записи (для batch-evaluation graduate/expire)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM watchlist WHERE status = 'active'"
            ).fetchall()
        return [self._row_to_watchlist(r) for r in rows]

    def close_source_active_watchlist(self, source_id: str) -> int:
        """Закрыть все активные watchlist-записи источника. Возвращает количество."""
        now = _now()
        with self._conn() as c:
            cur = c.execute(
                """UPDATE watchlist SET status = 'closed', closed_at = ?
                   WHERE source_id = ? AND status = 'active'""",
                (now, source_id),
            )
        return cur.rowcount

    def mark_watchlist_status(
        self,
        watchlist_id: int,
        *,
        status: str,
        hit_reason: str | None = None,
        graduated: bool = False,
        closed: bool = False,
        now_iso: str | None = None,
    ) -> None:
        """Обновить статус watchlist-записи. graduated=True ставит graduated_at,
        closed=True ставит closed_at."""
        now = now_iso or _now()
        updates = ["status = ?"]
        params: list = [status]
        if hit_reason is not None:
            updates.append("hit_reason = ?")
            params.append(hit_reason)
        if graduated:
            updates.append("graduated_at = ?")
            params.append(now)
        if closed:
            updates.append("closed_at = ?")
            params.append(now)
        params.append(watchlist_id)
        with self._conn() as c:
            c.execute(
                f"UPDATE watchlist SET {', '.join(updates)} WHERE id = ?",
                params,
            )

    def get_watchlist(self, watchlist_id: int) -> WatchlistRow | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM watchlist WHERE id = ?", (watchlist_id,)
            ).fetchone()
        return self._row_to_watchlist(row) if row else None

    def _row_to_watchlist(self, row: sqlite3.Row) -> WatchlistRow:
        return WatchlistRow(
            id=row["id"],
            video_id=row["video_id"],
            source_id=row["source_id"],
            added_at=row["added_at"],
            expires_at=row["expires_at"],
            initial_views=row["initial_views"],
            initial_velocity=row["initial_velocity"],
            reason=row["reason"],
            status=row["status"],
            graduated_at=row["graduated_at"],
            hit_reason=row["hit_reason"],
            closed_at=row["closed_at"],
        )
