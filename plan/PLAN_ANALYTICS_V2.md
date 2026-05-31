# PLAN_ANALYTICS_V2 — Cross-platform analytics (микросервис)

## Контекст

`PLAN_ANALYTICS.md` (Этап A) — аналитика **наблюдаемых авторов**, встроенная в
`Modules/monitor/` (данные Apify, snapshot профилей конкурентов).

`PLAN_ANALYTICS_V2` — другой контур: сквозная аналитика **собственных
публикаций**, которые VIRA уже разместила через будущий `publisher`-сервис.
Здесь источник истины — не Apify, а сами площадки (VK API и т.д.) по
`external_id` публикации.

Поэтому это **отдельный микросервис** `Modules/analytics/`:
- своя БД (`/db/analytics.db`, volume `analytics_db`)
- свой контейнер (`viral-mpv-analytics` / `vira-analytics`)
- читает publisher по REST, агрегирует на pandas, отдаёт сводки фронту через shell

Сервис **не зависит от готовности publisher**: при недоступности publisher-а
или флаге `ANALYTICS_USE_MOCK=1` использует mock-фикстуры, поэтому может
разрабатываться и деплоиться раньше publisher-а.

## Конвенции (как у остальных сервисов VM)

- Контейнер слушает **8000** внутри (dev-host-маппинг `8900:8000`; прод — без ports).
- Все роуты с префиксом `/analytics/*`. `GET /analytics/healthz` — на уровне app
  **до** `include_router`.
- SQLite **синхронный** (`sqlite3` + WAL, `isolation_level=None`,
  `busy_timeout=5000`, `row_factory=Row`). Async — только httpx и FastAPI-хендлеры.
- Миграции идемпотентные (`PRAGMA table_info` перед `ALTER`; индекс после `ALTER`).
- structlog + lifespan. HTTP к сервисам — httpx.AsyncClient + `X-Worker-Token`.

## Схема БД

```sql
CREATE TABLE platform_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT, publication_id TEXT NOT NULL,
  platform TEXT NOT NULL, fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  views INTEGER DEFAULT 0, reach INTEGER DEFAULT 0, likes INTEGER DEFAULT 0,
  comments INTEGER DEFAULT 0, shares INTEGER DEFAULT 0, saves INTEGER DEFAULT 0,
  click_through_to_external INTEGER DEFAULT 0);
CREATE INDEX idx_pm_pub ON platform_metrics(publication_id, fetched_at);
CREATE INDEX idx_pm_platform ON platform_metrics(platform, fetched_at);

CREATE TABLE daily_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT, date DATE, platform TEXT,
  total_views INTEGER, total_reach INTEGER, new_followers INTEGER,
  click_through INTEGER, publications_count INTEGER,
  fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX idx_ds_date ON daily_snapshots(date, platform);
```

- `platform_metrics` — по строке на каждый fetch. Агрегации «текущего состояния»
  используют **последний** срез на публикацию (`latest_per_publication`), чтобы
  повторные fetch-и не суммировались многократно.
- `daily_snapshots` — дневные агрегаты по платформе.

## Платформы (адаптеры)

`platforms/base.py` — абстрактный `PlatformAdapter` с
`async fetch_metrics(external_id) -> PlatformMetrics`. `PlatformMetrics` —
нормализованный dataclass (views/reach/likes/comments/shares/saves/CTR).

- **Phase 1:** `platforms/vk.py` (`VKAdapter`, метод `video.get`). `external_id`
  публикации в форме `"{owner_id}_{item_id}"` (общий для `vk_video`/`vk_clips`).
- **Phase 2+:** Дзен / YouTube / Telegram / Pinterest — заглушки-наследники
  `_StubAdapter` (поднимают `NotImplementedError`).

publisher-платформы `vk_video` / `vk_clips` маппятся на один analytics-ключ `vk`.

## Fetcher worker (asyncio task в lifespan)

- **раз в час** — `GET http://publisher:8000/publisher/publications?limit=1000`
  (порт 8000, путь с префиксом, заголовок `X-Worker-Token`; **НЕ** через shell,
  **НЕ** порт 8800). Фильтр: статус `published`/`publishing`, есть `external_id`,
  `published_at` за последние 30д. Для каждой — fetch VK-метрик (или mock-метрик
  без `VK_TOKEN`) → строка в `platform_metrics`. Publisher недоступен → лог +
  mock-фикстуры.
- **раз в сутки 00:00 МСК** — daily snapshot: агрегирует последние метрики по
  платформам в `daily_snapshots`.

Ядро воркера (`run_hourly_cycle`, `run_daily_snapshot`, `select_active_publications`)
— чистые async-функции, тестируемые на фикстурах без FastAPI.

## Контракт publisher

```
publication = {id:str, platform:"vk_video"|"vk_clips", external_id:str|null,
  title, description, tags:[str],
  status:"dry_run"|"scheduled"|"publishing"|"published"|"failed",
  scheduled_at:iso|null, published_at:iso|null, error_message:str|null}
```

Ответ — список или `{"items": [...]}`/`{"publications": [...]}`.

## Эндпоинты (pandas-агрегации)

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/analytics/cross-platform?period=` | тоталы + ER + разбивка по платформам |
| GET | `/analytics/platform/{platform}?period=` | сводка платформы + её публикации |
| GET | `/analytics/publication/{publication_id}` | timeseries метрик публикации |
| GET | `/analytics/ab?ids=a,b,c` | A/B-сравнение, winner по engagement_rate |
| GET | `/analytics/top?metric=&period=&limit=` | топ публикаций по метрике |
| POST | `/analytics/refresh-now` | ручной hourly-проход (нужен `X-Worker-Token`) |
| GET | `/analytics/healthz` | статус + счётчики |

`period`: `1d`/`7d`/`30d`/`Nd`/`Nh`/`all`.

## Docker

- `docker-compose.yml`: сервис `analytics` (`viral-mpv/analytics:dev`,
  `viral-mpv-analytics`, `8900:8000`, env_file `.env.analytics`, volume
  `analytics_db:/db`, healthcheck `/analytics/healthz`). Добавлен в `depends_on`
  shell. Named volume `analytics_db`.
- `deploy/docker-compose.prod.yml`: `vira-analytics` (`:prod`, context `..`, без
  ports), volume `analytics_db`, в `depends_on` shell. (Обязательно — иначе
  `update.sh --remove-orphans` снесёт контейнер.)

nginx не трогаем (shell проксирует по имени сервиса).

## Тесты

`Modules/analytics/tests/` (pytest, asyncio_mode=auto):
- `test_storage.py` — sqlite/WAL/миграции/latest_per_publication.
- `test_vk_adapter.py` — VK-адаптер на `httpx.MockTransport`.
- `test_aggregations.py` — pandas-агрегации (cross/platform/top/ab/timeseries).
- `test_fetcher_integration.py` — hourly-cycle: publisher API → fetch → DB на
  фикстурах (real path + publisher-down fallback + use_mock + daily snapshot).
- `test_endpoints.py` — smoke эндпоинтов через TestClient.

Статус: **31 тест зелёный**.

## Статус / DoD

- [x] Сервис стартует; `/analytics/healthz` = 200.
- [x] Fetcher тянет VK-метрики для test-публикаций (mock, если publisher нет).
- [x] Эндпоинты дают верные агрегации.
- [x] Тесты зелёные; README + этот план на месте.
- [x] Блоки в обоих compose; `analytics` в `depends_on` shell; volume объявлен.

## Дальше (Phase 2+)

- Реальные адаптеры Дзен/YouTube/Telegram/Pinterest (заглушки уже есть в `base.py`).
- `new_followers` в daily snapshot (когда publisher/площадки начнут отдавать прирост).
- Проксирование `/api/analytics/*` в shell + UI-вкладка «Аналитика публикаций».
- BYOK-токены площадок вместо общего `VK_TOKEN` (multi-tenant, фаза 2 SaaS).
```
