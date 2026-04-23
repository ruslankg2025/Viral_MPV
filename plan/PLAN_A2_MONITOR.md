# PLAN — A2 Monitor (v2, после аудита)

## Контекст

Модуль A2 из [ПЛАН_ВИРАЛ-монитор.md](../ПЛАН_ВИРАЛ-монитор.md) — сервис мониторинга внешних источников (каналы/блогеры конкурентов).

**Роль в архитектуре:**
- Источники привязаны к `account_id` из profile (A7) — per-brand мониторинг.
- Результаты trending питают processor (A3) через ручной триггер и script (A5) как input patterns.
- Monitor НЕ вызывает processor — только отдаёт payload для внешнего оркестратора.

**Принципиальные решения:**
- **YouTube-only** на старте. Остальные платформы (IG/TikTok/VK) — pluggable через `MetricsSource` Protocol, подключаются когда появятся API-ключи/downloader.
- **APScheduler in-process**, без Celery/Redis. Миграция тривиальная позже.
- **SQLite** с миграциями через `PRAGMA user_version`. Postgres — когда появится multi-tenant.
- **Фаза 3 (Dedup) и Фаза 4 (Benchmarking) вырезаны** из текущего плана до появления downloader/A9.

## Фаза 1 — MVP (только этот документ описывает детально)

Покрывает A2.1–A2.4:

| # | Функция | Реализация |
|---|---|---|
| A2.1 | Source Registry | SQLite CRUD + HTTP |
| A2.2 | Scheduler | APScheduler `AsyncIOScheduler` с `max_instances=1`, `coalesce=True` |
| A2.3 | Metrics Collector | YouTube Data API v3 (playlistItems + videos.list) |
| A2.4 | Trending Detector | z-score 24h vs 7d baseline + growth_rate |

## Архитектура

FastAPI-сервис `monitor`, порт **8400**, по паттерну profile/script:

```
Modules/monitor/
  main.py          FastAPI + lifespan (scheduler bootstrap, stale crawl cleanup)
  config.py        Settings: MONITOR_TOKEN, MONITOR_ADMIN_TOKEN, YOUTUBE_API_KEY,
                   PROFILE_BASE_URL, PROFILE_TOKEN, MONITOR_FAKE_FETCH, DB_DIR,
                   CRAWL_MAX_CONCURRENT, TRENDING_ZSCORE_THRESHOLD, TRENDING_GROWTH_THRESHOLD
  auth.py          require_token + require_admin_token (X-Token / X-Admin-Token)
  state.py         global state (store, scheduler, platforms, profile_client)
  schemas.py       Pydantic: SourceCreate/Response, VideoResponse, MetricSnapshot,
                   TrendingItem, CrawlLogEntry, HealthResponse, QuotaResponse
  storage.py       SQLite store с миграциями через PRAGMA user_version
  router.py        /monitor/* (user) + /monitor/admin/* (admin)
  logging_setup.py structlog (копия processor)
  scheduler.py     APSchedulerWrapper: start/stop/reload/add/remove/list
  crawler.py       orchestrate_crawl(source) → platform.fetch → snapshots → trending
  profile_client.py httpx AsyncClient с timeout=3s для profile integration
  platforms/
    __init__.py
    base.py        MetricsSource Protocol: resolve_channel(url), fetch_new_videos(source),
                   fetch_metrics(video_ids)
    youtube.py     YouTubeSource + URL parser (все форматы @handle/channel/c/user)
  analytics/
    __init__.py
    trending.py    compute_trending(video_id, snapshots, channel_baseline)
  fixtures/
    youtube_channel_response.json
    youtube_playlist_response.json
    youtube_videos_response.json
  tests/
    __init__.py
    conftest.py       in-memory sqlite fixture + frozen clock + mocked platform
    test_storage.py   CRUD + migrations + cascades + quota tracking
    test_youtube.py   URL parser (5 форматов) + fake mode + response parsing
    test_crawler.py   orchestrate_crawl на мок-платформе
    test_scheduler.py add/remove/reload/max_instances
    test_trending.py  z-score/growth edge cases
    test_router.py    HTTP auth matrix + happy paths
  requirements.txt
  Dockerfile
```

## Схема БД (v1)

```sql
CREATE TABLE sources (
    id              TEXT PRIMARY KEY,          -- uuid4
    account_id      TEXT NOT NULL,             -- FK на profile.accounts.id (lazy validate)
    platform        TEXT NOT NULL,             -- 'youtube'
    channel_url     TEXT NOT NULL,             -- как ввёл пользователь
    external_id     TEXT NOT NULL,             -- UC... для YouTube (resolved)
    channel_name    TEXT,
    niche_slug      TEXT,
    tags_json       TEXT NOT NULL DEFAULT '[]',
    priority        INTEGER NOT NULL DEFAULT 100,
    interval_min    INTEGER NOT NULL DEFAULT 60,
    is_active       INTEGER NOT NULL DEFAULT 1,
    profile_validated INTEGER NOT NULL DEFAULT 0,  -- 0 если profile был down при создании
    last_error      TEXT,                      -- последняя ошибка crawl
    added_at        TEXT NOT NULL,
    last_crawled_at TEXT,
    UNIQUE(account_id, platform, external_id)
);

CREATE TABLE videos (
    id              TEXT PRIMARY KEY,          -- uuid4
    source_id       TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    platform        TEXT NOT NULL,
    external_id     TEXT NOT NULL,             -- YouTube video_id
    url             TEXT NOT NULL,
    title           TEXT,
    description     TEXT,
    thumbnail_url   TEXT,
    duration_sec    INTEGER,
    published_at    TEXT,
    first_seen_at   TEXT NOT NULL,
    UNIQUE(platform, external_id)
);
CREATE INDEX idx_videos_source_published ON videos(source_id, published_at DESC);

CREATE TABLE metric_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id         TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    captured_at      TEXT NOT NULL,
    views            INTEGER NOT NULL DEFAULT 0,
    likes            INTEGER NOT NULL DEFAULT 0,
    comments         INTEGER NOT NULL DEFAULT 0,
    engagement_rate  REAL,
    UNIQUE(video_id, captured_at)
);
CREATE INDEX idx_snapshots_video_time ON metric_snapshots(video_id, captured_at DESC);

CREATE TABLE trending_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    computed_at     TEXT NOT NULL,
    zscore_24h      REAL,
    growth_rate_24h REAL,
    is_trending     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(video_id, computed_at)
);
CREATE INDEX idx_trending_flag ON trending_scores(is_trending, computed_at DESC);

CREATE TABLE crawl_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,             -- 'running' | 'ok' | 'failed'
    videos_new      INTEGER NOT NULL DEFAULT 0,
    videos_updated  INTEGER NOT NULL DEFAULT 0,
    error           TEXT
);
CREATE INDEX idx_crawl_log_source_time ON crawl_log(source_id, started_at DESC);

CREATE TABLE youtube_quota (
    date            TEXT PRIMARY KEY,          -- YYYY-MM-DD (Pacific Time)
    units_used      INTEGER NOT NULL DEFAULT 0
);

PRAGMA user_version = 1;
```

**Миграции.** `_migrate(conn)` вызывается при старте: читает `PRAGMA user_version`, применяет все `MIGRATIONS[v]` в словаре для `v > current`, атомарно обновляет `user_version`. Первая версия — initial schema выше.

## HTTP API

### User token (`/monitor/*`, заголовок `X-Token`)

```
GET    /monitor/healthz                             public, возвращает HealthResponse
GET    /monitor/sources?account_id=…                список sources аккаунта
POST   /monitor/sources                             создать (может поставить profile_validated=0)
GET    /monitor/sources/{id}                        детали + последний crawl_log
PATCH  /monitor/sources/{id}                        обновить priority/interval/tags/is_active
DELETE /monitor/sources/{id}                        каскад удаление
POST   /monitor/sources/{id}/crawl                  принудительный обход сейчас

GET    /monitor/videos?source_id=…&limit=50         список видео источника
GET    /monitor/videos/{id}                         детали + последние N snapshots
GET    /monitor/videos/{id}/metrics                 хронология snapshots для графика
POST   /monitor/videos/{id}/analyze                 stub: возвращает payload для processor
                                                    (file_path=null, source_url, hints)

GET    /monitor/trending?account_id=…&window=24h&limit=20
GET    /monitor/trending/{video_id}                 детализация score

GET    /monitor/crawl-log?source_id=…&limit=50
```

### Admin token (`/monitor/admin/*`, заголовок `X-Admin-Token`)

```
GET    /monitor/admin/platforms                     список платформ + статус ключей
POST   /monitor/admin/platforms/youtube/test        проверить YOUTUBE_API_KEY
GET    /monitor/admin/platforms/youtube/quota       {date, used, limit, percent}
GET    /monitor/admin/scheduler                     {running, jobs: [...]}
POST   /monitor/admin/scheduler/reload              перезагрузить jobs из БД
POST   /monitor/admin/trending/recompute            пересчёт trending всех свежих видео
```

### Health response

```json
{
  "status": "ok",
  "fake_mode": false,
  "active_sources": 5,
  "scheduler_running": true,
  "youtube_quota_used_percent": 7,
  "pending_crawls": 0,
  "last_crawl_at": "2026-04-15T10:30:00Z"
}
```

## YouTube Data API v3

### Расходы quota

| Endpoint | units | Назначение |
|---|---|---|
| `channels.list` | 1 | Resolve channel_id из URL, получить uploads playlist |
| `playlistItems.list` | 1 | Инкремент новых видео (batch 50) |
| `videos.list?id=…` | 1 | Статистика (views/likes/comments), batch 50 |
| `search.list` | 100 | ИЗБЕГАЕМ (только при emergency fallback для @handle) |

**Оценка:** один обход = ~3 units. 10 sources × 24 обхода/день × 3 = 720 units/day (< 10% от 10k free).

### URL Parser (критично)

`youtube.py::resolve_channel_id(url)` поддерживает:
- `youtube.com/channel/UC...` → извлечение напрямую, **0 units**
- `youtube.com/@handle` → `channels.list?forHandle=…`, 1 unit
- `youtube.com/c/customname` → `channels.list?forUsername=…` (legacy), 1 unit
- `youtube.com/user/legacy` → `channels.list?forUsername=…`, 1 unit
- `youtu.be/videoID` → ошибка (это видео, не канал)

### Retry + Quota Exhaustion

- **5xx / ConnectionError:** 3 попытки, exponential backoff (1s, 4s, 16s).
- **403 quotaExceeded:** `crawl_log.error = "quota_exhausted"`, scheduler пауза до 00:00 PT (next midnight Pacific Time). Health-status → warning.
- **404 channel not found:** `is_active = 0`, `last_error`, не ретраить.

### Инкрементальный обход

1. При `POST /sources` — resolve_channel_id → сохранить `external_id` (UC...).
2. Каждый обход:
   - `playlistItems.list(uploads_playlist_id, maxResults=50)` — новые видео.
   - diff с БД → INSERT новых в `videos`.
   - `videos.list(id=[...], part='statistics,contentDetails')` батчами по 50 для всех видео источника моложе 30 дней → INSERT `metric_snapshots`.
   - `trending.compute_batch()` для видео моложе 7 дней → UPSERT `trending_scores`.
   - `crawl_log.status='ok'`, `last_crawled_at`.

## Scheduler (APScheduler)

```python
scheduler = AsyncIOScheduler(
    job_defaults={
        "max_instances": 1,      # не параллелить job одного source
        "coalesce": True,        # пропустить пропущенные тики
        "misfire_grace_time": 300,
    }
)
```

Поведение:
- При **lifespan startup**: `storage.mark_stale_crawls_as_failed()` (все `running > 10 min` → `failed`), `reload_jobs_from_db()`.
- При `POST /sources` → `scheduler.add_job(source_id, interval_min)`.
- При `PATCH /sources/{id}` с изменением `interval_min` или `is_active` → `remove_job` + `add_job`.
- При `DELETE /sources/{id}` → `remove_job`.
- `CRAWL_MAX_CONCURRENT` через `asyncio.Semaphore` в `crawler.orchestrate_crawl`.
- Housekeeping отложен до MVP+3 месяца (SQLite держит миллионы строк без оптимизации).

## Trending Algorithm

Для каждого видео с `published_at >= now - 7 days`:

1. Последние 2 снапшота за окно 24h → `views_now`, `views_24h_ago`.
2. `growth_rate_24h = (views_now - views_24h_ago) / max(views_24h_ago, 1)`.
3. Baseline канала: среднее `views` всех видео этого source за 30 дней, кроме текущего.
4. `zscore_24h = (views_now - mean) / max(stdev, 1)`.
5. `is_trending = (zscore_24h >= THRESHOLD_Z) and (growth_rate_24h >= THRESHOLD_GROWTH) and (views_now >= 100)`.

**Edge cases:**
- < 2 снапшотов → `zscore=NULL, growth=NULL, is_trending=0`
- Канал с < 3 видео в истории → `zscore=NULL`, только growth_rate
- `views_now < 100` → `is_trending=0` (шум первого часа)
- `stdev == 0` → `stdev := 1` (защита от zero division)

Пороги в env: `TRENDING_ZSCORE_THRESHOLD=2.0`, `TRENDING_GROWTH_THRESHOLD=0.5`.

## Profile integration

`profile_client.py`:
```python
async def validate_account(account_id: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"{PROFILE_BASE_URL}/profile/accounts/{account_id}",
                           headers={"X-Token": PROFILE_TOKEN})
            return r.status_code == 200
    except (httpx.TimeoutException, httpx.ConnectError):
        return False  # fallback: создать с profile_validated=0, лог warning
```

При `POST /sources` — валидация вызывается, но **не блокирует создание**. Если `False`, `profile_validated=0` в БД + warning в логе. Можно потом перевалидировать через `POST /sources/{id}/revalidate`.

## Конфигурация

`.env.monitor.example`:
```
MONITOR_TOKEN=dev-token-change-me
MONITOR_ADMIN_TOKEN=dev-admin-token-change-me
DB_DIR=/db

# YouTube Data API v3. Оставить пустым → MONITOR_FAKE_FETCH=true включается автоматически.
YOUTUBE_API_KEY=

# Profile service для валидации account_id
PROFILE_BASE_URL=http://profile:8000
PROFILE_TOKEN=dev-token-change-me

# Fake mode: берёт данные из fixtures/, не ходит в сеть. Для тестов/первого запуска.
MONITOR_FAKE_FETCH=false

# Scheduler
CRAWL_DEFAULT_INTERVAL_MIN=60
CRAWL_MAX_CONCURRENT=3

# Trending
TRENDING_ZSCORE_THRESHOLD=2.0
TRENDING_GROWTH_THRESHOLD=0.5
TRENDING_MIN_VIEWS=100
```

## Docker

```yaml
monitor:
  build:
    context: .
    dockerfile: Modules/monitor/Dockerfile
  image: viral-mpv/monitor:dev
  container_name: viral-mpv-monitor
  restart: unless-stopped
  env_file: [.env.monitor]
  environment:
    DB_DIR: /db
  volumes:
    - monitor_db:/db     # named volume (SQLite WAL friendly на Windows)
  ports:
    - "8400:8000"
  depends_on:
    profile:
      condition: service_healthy   # ждать profile готовности
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/monitor/healthz').status == 200 else 1)"]
    interval: 30s
    timeout: 5s
    retries: 3
    start_period: 10s
```

**CORS middleware** — копия из profile:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)
```

**Shell integration** — обновить `MODULES.monitor` в `shell.js`:
```js
MODULES.monitor = {
  base: "http://localhost:8400",
  health: "/monitor/healthz",
  tokenKinds: ["user", "admin"],
  headers: { user: "X-Token", admin: "X-Admin-Token" },
  auth: [["admin", ["/monitor/admin"]], ["user", ["/monitor"]]],
  parseHealth: (h) => `ok · sources=${h.active_sources} · quota=${h.youtube_quota_used_percent}%`,
};
```

`shell.depends_on` в compose нужно дополнить `monitor`.

## Тесты (цель: 35+ зелёных)

- **test_storage.py** (~10): migrations, sources CRUD, videos CRUD, snapshots batch insert, trending upsert, crawl_log, quota counter, cascade delete, stale crawl marking, `PRAGMA user_version`.
- **test_youtube.py** (~6): URL parser (5 форматов + 1 невалидный), fake mode возвращает fixture, response parsing, retry on 503.
- **test_crawler.py** (~4): orchestrate_crawl на мок-платформе (happy), новые видео + обновление, error propagation в crawl_log, quota_exhausted обработка.
- **test_scheduler.py** (~3): reload_jobs_from_db, add/remove, `max_instances` не допускает дублей.
- **test_trending.py** (~5): < 2 snapshots → NULL, корректный z-score, нулевая дисперсия, `views < 100` фильтруется, is_trending threshold.
- **test_router.py** (~10): auth matrix для user/admin/public, CRUD sources через HTTP, POST crawl trigger, GET trending, GET videos, health response shape, 404 для unknown.

## Sequencing

1. ✅ План переписан.
2. Skeleton: config/state/auth/schemas/logging/requirements/Dockerfile.
3. storage.py + test_storage.py (зелёный).
4. platforms/youtube.py + fixtures + test_youtube.py (зелёный).
5. crawler.py + test_crawler.py (зелёный).
6. scheduler.py + test_scheduler.py (зелёный).
7. analytics/trending.py + test_trending.py (зелёный).
8. router.py + main.py + test_router.py (зелёный).
9. Запуск всего suite → ≥35 passed.
10. docker-compose.yml + shell.js update.
11. Отчёт.
