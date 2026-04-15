# PLAN — A2 Monitor (Viral Monitor module)

## Контекст

Модуль A2 из [ПЛАН_ВИРАЛ-монитор.md](../ПЛАН_ВИРАЛ-монитор.md) — сервис мониторинга и сбора метрик по внешним источникам (каналы/блогеры конкурентов). Покрывает 7 под-модулей A2.1–A2.7:

| # | Название | Функция |
|---|---|---|
| A2.1 | Source Registry | CRUD каналов/блогеров, теги, группировка, расписание |
| A2.2 | Crawler Scheduler | Периодический обход с учётом приоритета |
| A2.3 | Metrics Collector | Инкрементальный сбор views/likes/comments |
| A2.4 | Trending Detector | Детектор вирусных роликов (z-score) |
| A2.5 | Historical Import | Первичный импорт постов канала |
| A2.6 | Deduplication Service | sha256 + pHash для перезаливов |
| A2.7 | Competitor Benchmarking | Сравнение своего аккаунта с конкурентами |

**Задача сервиса в общей архитектуре:**
- Источники конкурентов привязаны к `account_id` из `profile` (A7) — для одного бренда = свой список отслеживаемых.
- Результаты мониторинга (trending videos) питают:
  - `processor` (A3) — через ручной триггер «проанализируй этот ролик» → транскрипция + vision;
  - `script` (A5) — как input patterns при генерации сценариев;
  - Будущий Dashboard (B5) — лента трендов и бенчмарки.

**Ограничения MVP:**
- Сейчас нет A1 downloaders (кроме заготовки). YouTube Data API v3 отдаёт метаданные и статистику без скачивания самого видео — этого достаточно для A2.1–A2.4. Для A2.5/A2.6 с pHash потребуется реальная загрузка файлов, поэтому этот функционал отложен до появления A1.2 YouTube Downloader.
- APScheduler в том же FastAPI-процессе вместо Celery/Redis — меньше движущихся частей, для desktop-dev подходит. Миграция на Celery тривиальная, когда понадобится multi-worker.
- На старте поддерживается **только YouTube**. Архитектура источников — pluggable (через protocol/interface), IG/TikTok/VK добавляются отдельными адаптерами, когда появятся их API-ключи/разрешения.

## Архитектура

FastAPI-сервис `monitor`, порт **8400**, по образцу [Modules/profile](../Modules/profile/) и [Modules/script](../Modules/script/):

- SQLite (named docker volume `monitor_db`) для Source Registry, snapshots, trending.
- APScheduler (встроен в lifespan) гоняет периодические задачи.
- Слой `platforms/` — абстракция `MetricsSource` с реализациями `YouTubeSource`, заготовки `InstagramSource`, `TikTokSource`, `VKSource` на будущее.
- Слой `analytics/` — детекторы трендов и бенчмарки.
- Два router-а: `/monitor/*` (user-token) и `/monitor/admin/*` (admin-token).
- `X-Token` / `X-Admin-Token` как в `profile`.

### Интеграция с соседями

- **profile**: HTTP-запросами на `http://profile:8000/profile/accounts/{id}` резолвится информация об аккаунте для benchmarking (A2.7) и поиска niche-пира для trending (A2.4). Токен `PROFILE_TOKEN` в env.
- **processor**: monitor НЕ вызывает processor сам. Вместо этого эндпоинт `POST /monitor/videos/{id}/analyze` отдаёт `{file_path, source_url}` для ручного прокидывания в `processor` — либо будущий оркестратор (B1.3) подхватит это.
- **script**: опционально — эндпоинт `GET /monitor/trending?niche=…&limit=10` для A5, чтобы brief-ы генерировались на основе свежих трендов.

## Фазы разработки

Разбивка на 4 фазы, чтобы можно было демо-тестировать после каждой.

### Фаза 1 — MVP (1–2 недели)

Минимально жизнеспособный мониторинг с YouTube-only.

**Модули:**
- A2.1 Source Registry (SQLite CRUD)
- A2.2 Scheduler (APScheduler, in-process)
- A2.3 Metrics Collector (YouTube only, инкрементальный)
- A2.4 Trending Detector (базовый — z-score views за 24ч против 7-дневного среднего)

**Выход:**
- CRUD источников через UI.
- Автоматический обход раз в 30–60 мин для каждого источника.
- Список trending видео по подписанным источникам.
- Тесты: 30+ штук, покрывающие storage, router, YouTube-mock, scheduler, trending-алгоритм.

### Фаза 2 — Расширение охвата (2–3 недели, зависит от A1)

- A2.5 Historical Import (первичная загрузка N последних видео при добавлении источника).
- Подключение Instagram Downloader (требует A1.3) и TikTok (A1.4) как дополнительные платформы.
- Расширение schema под мультиплатформенность.

### Фаза 3 — Качество данных (1–2 недели)

- A2.6 Deduplication Service: sha256 по video_id + pHash по превью (требует скачивания thumbnails — бесплатно по YouTube API).
- Полноценный pHash по видео-контенту — отложено до появления downloader-а.

### Фаза 4 — Бенчмарк и аналитика (1 неделя)

- A2.7 Competitor Benchmarking: эндпоинт, который берёт метрики своего аккаунта (из A9, когда появится, пока — из ручного seed) и сравнивает со средним по группе конкурентов.
- Интеграция trending → script генерация.

---

## Фаза 1 — детальный план (ближайшая реализация)

### Схема БД

```sql
-- A2.1 Source Registry
CREATE TABLE sources (
    id             TEXT PRIMARY KEY,     -- uuid
    account_id     TEXT NOT NULL,         -- FK на profile.accounts.id (строковое)
    platform       TEXT NOT NULL,         -- 'youtube' | 'instagram' | 'tiktok' | 'vk'
    channel_url    TEXT NOT NULL,         -- полный URL
    external_id    TEXT NOT NULL,         -- e.g. youtube channel_id (UC...)
    channel_name   TEXT,
    niche_slug     TEXT,                  -- опциональный override (иначе от profile)
    tags_json      TEXT NOT NULL DEFAULT '[]',
    priority       INTEGER NOT NULL DEFAULT 100,  -- чем выше, тем чаще обход
    interval_min   INTEGER NOT NULL DEFAULT 60,
    is_active      INTEGER NOT NULL DEFAULT 1,
    added_at       TEXT NOT NULL,
    last_crawled_at TEXT,
    UNIQUE(account_id, platform, external_id)
);

-- Видео (обобщённая сущность любого поста/ролика)
CREATE TABLE videos (
    id             TEXT PRIMARY KEY,     -- uuid
    source_id      TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    platform       TEXT NOT NULL,
    external_id    TEXT NOT NULL,         -- YouTube video_id
    url            TEXT NOT NULL,
    title          TEXT,
    description    TEXT,
    thumbnail_url  TEXT,
    duration_sec   INTEGER,
    published_at   TEXT,                  -- ISO, из платформы
    first_seen_at  TEXT NOT NULL,         -- когда monitor его впервые увидел
    UNIQUE(platform, external_id)
);

-- A2.3 Metrics Collector — временные snapshot-ы
CREATE TABLE metric_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id       TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    captured_at    TEXT NOT NULL,
    views          INTEGER NOT NULL DEFAULT 0,
    likes          INTEGER NOT NULL DEFAULT 0,
    comments       INTEGER NOT NULL DEFAULT 0,
    engagement_rate REAL,                  -- (likes+comments)/views
    UNIQUE(video_id, captured_at)
);

CREATE INDEX idx_metric_snapshots_video_time ON metric_snapshots(video_id, captured_at);

-- A2.4 Trending Detector — материализованные рассчитанные оценки
CREATE TABLE trending_scores (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id       TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    computed_at    TEXT NOT NULL,
    zscore_24h     REAL,                   -- z-score по views за 24ч vs 7д baseline канала
    growth_rate_24h REAL,                  -- (views_now - views_24h_ago) / views_24h_ago
    is_trending    INTEGER NOT NULL DEFAULT 0,  -- boolean
    UNIQUE(video_id, computed_at)
);
CREATE INDEX idx_trending_flag ON trending_scores(is_trending, computed_at);

-- Лог обходов (для отладки и видимости из UI)
CREATE TABLE crawl_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id      TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,         -- 'running' | 'ok' | 'failed'
    videos_new     INTEGER NOT NULL DEFAULT 0,
    videos_updated INTEGER NOT NULL DEFAULT 0,
    error          TEXT
);
```

### HTTP API

**User-token (`X-Token`, `/monitor/*`):**

```
GET    /monitor/healthz                      — public
GET    /monitor/sources?account_id=…         — список источников аккаунта
POST   /monitor/sources                      — добавить источник
GET    /monitor/sources/{id}                 — детали источника + последний crawl_log
PATCH  /monitor/sources/{id}                 — обновить (priority, interval, tags, is_active)
DELETE /monitor/sources/{id}                 — удалить (+ каскад videos/snapshots)
POST   /monitor/sources/{id}/crawl           — принудительно запустить обход вне расписания

GET    /monitor/videos?source_id=…&limit=50  — список видео источника, новые сверху
GET    /monitor/videos/{id}                  — детали видео + последние N snapshots
GET    /monitor/videos/{id}/metrics          — хронология snapshot-ов (для графика)

GET    /monitor/trending?account_id=…&window=24h&limit=20
                                              — топ trending видео по подписанным источникам
GET    /monitor/trending/{video_id}          — детализация score-ов

GET    /monitor/crawl-log?source_id=…&limit=50
```

**Admin-token (`X-Admin-Token`, `/monitor/admin/*`):**

```
GET    /monitor/admin/platforms              — какие платформы доступны, есть ли ключи
POST   /monitor/admin/platforms/youtube/test — тест YouTube API key
POST   /monitor/admin/scheduler/start        — идемпотентный ре-старт планировщика
POST   /monitor/admin/scheduler/pause        — пауза (без остановки процесса)
GET    /monitor/admin/scheduler/state        — текущее состояние + активные jobs
POST   /monitor/admin/trending/recompute     — принудительный пересчёт trending-скоров
```

### File layout

```
Modules/monitor/
  __init__.py
  main.py                   # FastAPI + lifespan (bootstrap scheduler)
  config.py                 # Settings: MONITOR_TOKEN, MONITOR_ADMIN_TOKEN,
                            #           YOUTUBE_API_KEY, PROFILE_BASE_URL, PROFILE_TOKEN,
                            #           DB_DIR
  auth.py                   # require_token / require_admin_token (копия profile pattern)
  state.py                  # глобальный state: store, scheduler, platform_registry
  schemas.py                # Pydantic: SourceCreate/Response, VideoResponse,
                            #           MetricSnapshot, TrendingItem
  storage.py                # SQLite ProfileStore-style: sources/videos/snapshots/trending/crawl_log
  router.py                 # /monitor/* + /monitor/admin/*
  logging_setup.py          # structlog, как в profile
  scheduler.py              # APScheduler wrapper + job factory per source
  crawler.py                # orchestrate_crawl(source) — вызывает platform.fetch_new_videos(),
                            #                             обновляет metric_snapshots,
                            #                             пишет crawl_log
  platforms/
    __init__.py
    base.py                 # MetricsSource Protocol: .fetch_new_videos(source),
                            #                          .fetch_metrics(video_ids)
    youtube.py              # YouTubeSource — клиент Data API v3 (requests + API key)
    _stubs.py               # InstagramSource / TikTokSource / VKSource — NotImplementedError
  analytics/
    __init__.py
    trending.py             # compute_trending(video_id, snapshots) → zscore, growth, is_trending
    benchmarks.py           # Phase 4: account_vs_peers_benchmark(...)
  tests/
    __init__.py
    conftest.py             # in-memory sqlite fixture, mock_youtube fixture,
                            # frozen clock
    test_storage.py         # sources/videos/snapshots CRUD + каскады
    test_router.py          # HTTP layer с TestClient + токены
    test_youtube.py         # YouTubeSource с мокнутым requests
    test_crawler.py         # orchestrate_crawl на фейковом источнике
    test_trending.py        # z-score/rate, edge cases (мало данных, деление на ноль)
    test_scheduler.py       # ре-загрузка jobs при старте, приоритизация
  requirements.txt          # fastapi, pydantic, httpx или requests, apscheduler, structlog
  Dockerfile
```

### Конфигурация

`.env.monitor`:

```
MONITOR_TOKEN=dev-token-change-me
MONITOR_ADMIN_TOKEN=dev-admin-token-change-me
DB_DIR=/db
YOUTUBE_API_KEY=             # обязателен для реальных обходов (10k quota/day по умолчанию)
PROFILE_BASE_URL=http://profile:8000
PROFILE_TOKEN=dev-token-change-me
MONITOR_FAKE_FETCH=false     # если true — берём из fixtures/sample_youtube_response.json
CRAWL_DEFAULT_INTERVAL_MIN=60
CRAWL_MAX_CONCURRENT=3
```

Docker service в `docker-compose.yml`:

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
    - monitor_db:/db
    - ./data/monitor/cache:/cache
  ports:
    - "8400:8000"
  depends_on: [profile]
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/monitor/healthz').status == 200 else 1)"]
    interval: 30s
    timeout: 5s
    retries: 3
    start_period: 10s

volumes:
  monitor_db:
```

CORS middleware (как в других сервисах) — `allow_origins=["http://localhost:8000", ...]`.

### YouTube Data API v3 — что дёргаем

Бесплатный quota: 10 000 units/day. Основные расходы:
- `channels.list` — 1 unit.
- `search.list` (поиск видео канала за период) — **100 units**. Дорого, использовать только при первичном импорте.
- `playlistItems.list` (uploads playlist канала) — 1 unit. **Основной способ инкремента.**
- `videos.list?id=…` (batch до 50 видео) — 1 unit. Статистика (views/likes/comments).

Эвристика инкрементального обхода:
1. При добавлении источника — достать `uploads` playlist ID через `channels.list`.
2. Каждый обход — `playlistItems.list` (берём первые 50), сравниваем `video_id` с базой. Новые → добавляем в `videos`.
3. Затем `videos.list?id=<все активные за последние 30 дней, батчами по 50>` → snapshot всех метрик.
4. После snapshot — пересчитываем trending для свежих видео.

Стоимость одного обхода канала с 30-дневным окном: ~2 + ceil(30/50) ≈ **3 units**. При 10 источниках и интервале 60 мин = 10 * 24 * 3 = **720 units/day** — выжимаем меньше 10%, хороший запас.

### Trending — алгоритм MVP

Для каждого свежего видео (published_at за последние 7 дней):

1. Собрать все snapshot-ы этого видео за последние 24ч → `views_now`, `views_24h_ago`.
2. `growth_rate_24h = (views_now - views_24h_ago) / max(views_24h_ago, 1)`.
3. Для канала источника собрать среднее `views` всех видео канала за последние 30 дней (исключая текущее).
4. `zscore_24h = (views_now - mean) / max(stdev, 1)`.
5. `is_trending = (zscore_24h > 2.0 AND growth_rate_24h > 0.5)` (пороги в конфиге).

Edge cases:
- < 3 snapshot-ов → не считаем (записываем zscore=NULL, is_trending=0).
- Новый канал с < 10 видео в истории → не считаем baseline, только growth_rate.
- Видео в первый час после публикации — growth_rate часто > 10 из-за шума; фильтруем `views_now > 100`.

### Scheduler — APScheduler, паттерн

- При старте (`lifespan`) создаём `AsyncIOScheduler`, из БД подгружаем все активные sources и регистрируем `interval_min`-jobs (deduplicated по source_id).
- При PATCH/POST/DELETE source — обновляем соответствующий job (через `state.scheduler`).
- Каждый job вызывает `crawler.orchestrate_crawl(source)` и пишет `crawl_log`.
- `CRAWL_MAX_CONCURRENT` лимит через `asyncio.Semaphore`.
- Отдельный housekeeping-job раз в час: пересчёт trending_scores всех свежих видео, очистка старых snapshot-ов (> 30 дней → держим 1 в день).

### Тесты (цель Фазы 1: ~35 тестов)

- **storage.py** (10): CRUD sources/videos/snapshots + каскады, уникальные констрейнты.
- **router.py** (10): auth matrix (no-token / wrong-token / admin vs user), happy path по каждому endpoint-у, 404/400.
- **youtube.py** (5): парсинг реального JSON (фикстура), обработка quota exceeded, network error, пустой канал.
- **crawler.py** (4): мок-платформа, добавление новых + обновление существующих, обработка ошибок, крах посреди обхода.
- **trending.py** (4): edge cases (мало данных, 0 views, нулевая дисперсия, корректный порог).
- **scheduler.py** (2): загрузка при старте, динамический add/remove job.

Pattern fixtures и conftest — копируем подход из [Modules/profile/tests/conftest.py](../Modules/profile/tests/conftest.py).

---

## Фаза 2 — Расширение охвата

Предусловия: готов A1.1 Downloader-core + хотя бы один из A1.3/A1.4/A1.5. Добавляется:

- **A2.5 Historical Import** — при создании source параметр `import_last_n` (например 100). Запускается как one-off job в Scheduler-е, прогресс виден через `crawl_log`.
- **Расширение platforms/** — `InstagramSource`, `TikTokSource`, `VKSource` с общим Protocol. Абстракция уже лежит в `platforms/base.py` от Фазы 1.
- **Multi-platform schema** — `videos.platform` уже в таблице, фактически ничего не меняется кроме реализаций.

## Фаза 3 — Качество данных

- **A2.6 Deduplication** — sha256 по `(platform, external_id)` уже есть через UNIQUE. Добавляется pHash по скачанному thumbnail-у (8×8 DCT hash), хранится в `videos.phash`. Cross-platform матчинг: нормальзуем название + ищем по pHash.
- Таблица `dedup_clusters(cluster_id, video_ids_json)`.
- Эндпоинт `GET /monitor/dedup/clusters?min_size=2`.

## Фаза 4 — Benchmarking

- **A2.7 Competitor Benchmarking** — endpoint `GET /monitor/benchmarks?account_id=…&window=30d`. Возвращает:
  - Собственные метрики аккаунта (из A9, пока — ручной seed).
  - Средние по всем активным `sources` этого `account_id`.
  - Процентиль в группе.
- Доступ из shell UI как отдельная вкладка «Benchmark».

---

## UI — интеграция в shell

В [Modules/shell/static/index.html](../Modules/shell/static/index.html) заменить текущий monitor placeholder на полный набор вкладок:

- **Sources** — таблица источников активного аккаунта + форма «Add source». Поля: platform, channel_url, priority, interval_min, tags. Кнопка «Crawl now».
- **Videos** — фильтр по источнику, таблица видео, сортировка по first_seen / published / current_views.
- **Trending** — топ trending по подписанным источникам, фильтр `window`. Клик → детализация со snapshot-ами и графиком views.
- **Crawl log** — таблица обходов, цветовая индикация status.
- **Settings** — YouTube API key status, тест-кнопка, состояние scheduler-а.

Файл [Modules/shell/static/monitor.js](../Modules/shell/static/monitor.js) переписывается с заглушки на полную реализацию по образцу [profile.js](../Modules/shell/static/profile.js).

В [shell.js](../Modules/shell/static/shell.js):
- `MODULES.monitor.tokenKinds = ["user", "admin"]`
- `MODULES.monitor.auth = [["admin", ["/monitor/admin"]], ["user", ["/monitor"]]]`

---

## Открытые вопросы и развилки (уточнить перед стартом)

1. **YouTube API ключ** — у пользователя есть доступ к Google Cloud Console? Создание проекта + включение Data API v3 + key = 5 минут, ключ кладём в `.env.monitor` как `YOUTUBE_API_KEY`.
2. **Хранилище**: оставляем SQLite или сразу Postgres? Для desktop-dev и одного пользователя SQLite норма; Postgres стоит, если будет multi-tenant в будущем. Рекомендация: **SQLite**, миграция нетривиальная но отложимая.
3. **Интервалы обхода** — 60 мин по умолчанию комфортно по quota, но для trending real-time полезнее 15 мин. Предлагаю `interval_min` per-source, с дефолтом 60.
4. **Trending thresholds** — пороги zscore > 2 и growth_rate > 0.5 прикинуты «от стола». Вынесены в `.env`, подкрутим после первых обходов на реальных данных.
5. **Историческая глубина** — на сколько дней назад хранить снапшоты? Рекомендация: 30 дней полных + агрегаты (1/день) на 1 год. Реализуется housekeeping-job-ом.
6. **Зависимость от profile** — при создании source проверять, что `account_id` существует в profile? Это даёт чистые данные, но связывает жёстко. Альтернатива — хранить `account_id` как свободную строку и проверять лениво. Рекомендация: **валидировать через HTTP-запрос к profile при POST**, с fallback на предупреждение (не 400).

## Sequencing — ближайшие шаги

1. Утвердить план (после заполнения демо-профиля).
2. Создать `Modules/monitor/` skeleton (config, state, auth, storage, schemas, main.py, пустой router, Dockerfile, requirements) + тесты на storage. Без YouTube пока.
3. Подключить в docker-compose, проверить healthz из shell.
4. Реализовать YouTubeSource + фикстуры + тесты.
5. crawler.orchestrate_crawl + тест на моке.
6. Scheduler + lifespan интеграция + тест.
7. Trending detector + тест.
8. HTTP роуты + e2e тесты через TestClient.
9. Переписать `monitor.js` в shell — вкладки Sources / Videos / Trending / Crawl log / Settings.
10. Боевой тест: добавить 3–5 реальных YouTube каналов конкурентов, подождать обхода, посмотреть trending.

## Файлы, которые будут созданы

- `Modules/monitor/*` — структура выше
- `data/monitor/cache/` — для будущего bind-mount кеша thumbnails
- `.env.monitor`, `.env.monitor.example`
- Правки:
  - `docker-compose.yml` — сервис `monitor` + volume `monitor_db`
  - `Modules/shell/static/index.html` — секция monitor с реальными вкладками
  - `Modules/shell/static/shell.js` — `MODULES.monitor.tokenKinds`/`auth`
  - `Modules/shell/static/monitor.js` — переписать полностью
