# analytics — cross-platform analytics

Микросервис сквозной аналитики VIRA. Собирает метрики опубликованных видео
с платформ (Phase 1 — VK), агрегирует на pandas и отдаёт сводки по периодам,
платформам, отдельным публикациям, A/B-парам и топам.

Не зависит от готовности **publisher**: читает его по REST, при недоступности
переходит на mock-фикстуры.

## Конвенции

- Контейнер слушает **8000** внутри (в dev-compose маппится на host `8900`).
- Все роуты с префиксом `/analytics/*`. `GET /analytics/healthz` объявлен на
  уровне app **до** `include_router` (чтобы параметрический роут не перехватил).
- SQLite **синхронный**: `sqlite3` + `PRAGMA journal_mode=WAL`,
  `isolation_level=None`, `busy_timeout=5000`, `row_factory=Row`. Async — только
  httpx-клиенты и FastAPI-хендлеры.
- Миграции идемпотентные (`PRAGMA table_info` перед `ALTER`; индекс по колонке —
  после её `ALTER`).
- structlog + lifespan для инициализации Store и фонового fetcher-а.
- HTTP к другим сервисам — httpx.AsyncClient + заголовок `X-Worker-Token`.

## Файлы

| Файл | Назначение |
|------|-----------|
| `main.py` | FastAPI app, lifespan (Store + fetcher task), healthz |
| `router.py` | эндпоинты `/analytics/*` |
| `storage.py` | `AnalyticsStore` (sync sqlite3, WAL, миграции) |
| `aggregations.py` | pandas-агрегации (cross-platform / platform / top / a-b / timeseries) |
| `config.py` | `Settings` (pydantic-settings, `.env.analytics`) |
| `state.py` | singleton-стейт |
| `auth.py` | `require_worker_token` (X-Worker-Token == ANALYTICS_TOKEN) |
| `publisher_client.py` | REST-клиент publisher + mock-фикстуры |
| `fetcher.py` | фоновый воркер (hourly cycle + daily snapshot) |
| `platforms/base.py` | абстрактный `PlatformAdapter` + `PlatformMetrics` + заглушки платформ |
| `platforms/vk.py` | VK-адаптер (video.get) |

## Схема БД

- `platform_metrics` — по строке на каждый fetch публикации
  (views/reach/likes/comments/shares/saves/click_through_to_external).
- `daily_snapshots` — дневные агрегаты по платформе.

## Эндпоинты

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/analytics/healthz` | статус + счётчики |
| GET | `/analytics/cross-platform?period=30d` | сводка по всем платформам + тоталы + ER |
| GET | `/analytics/platform/{platform}?period=30d` | сводка одной платформы + её публикации |
| GET | `/analytics/publication/{publication_id}` | история метрик публикации (timeseries) |
| GET | `/analytics/ab?ids=a,b,c` | A/B-сравнение, победитель по engagement_rate |
| GET | `/analytics/top?metric=views&period=30d&limit=10` | топ публикаций по метрике |
| POST | `/analytics/refresh-now` | ручной hourly-проход (нужен `X-Worker-Token`) |

`period`: `1d` / `7d` / `30d` / `Nd` / `Nh` / `all`. `metric` для `/top`:
любая числовая колонка, либо `engagement` / `engagement_rate`.

## Fetcher worker

Запускается как asyncio-task в lifespan (`ENABLE_FETCHER=1`):

- **раз в час** — берёт активные публикации за 30д из publisher напрямую:
  `GET http://publisher:8000/publisher/publications?limit=1000` с
  `X-Worker-Token`. Если publisher недоступен — лог + mock-фикстуры. Для каждой
  опубликованной публикации с `external_id` тянет VK-метрики (или mock-метрики,
  если `VK_TOKEN` не задан) и пишет строку в `platform_metrics`.
- **раз в сутки в 00:00 МСК** — daily snapshot: агрегирует последние метрики по
  платформам в `daily_snapshots`.

## Конфиг (`.env.analytics`)

См. `.env.analytics.example`. Ключевое: `ANALYTICS_TOKEN`, `PUBLISHER_URL`,
`PUBLISHER_TOKEN`, `VK_TOKEN`, `ANALYTICS_USE_MOCK`, `ENABLE_FETCHER`,
`FETCH_INTERVAL_SECONDS`, `SNAPSHOT_CHECK_SECONDS`.

## Тесты

```
cd Modules/analytics && pytest
```

`tests/`: storage (sqlite/WAL/миграции), VK-адаптер (mock HTTP),
pandas-агрегации, интеграция hourly-cycle (publisher API → fetch → DB на
фикстурах) и smoke эндпоинтов через TestClient. 31 тест, все зелёные.

## Mock publisher

publisher-сервис ещё может отсутствовать. Замокан на двух уровнях:
1. **REST-клиент** (`PublisherClient.list_publications`) — в тестах подменяется
   `httpx.MockTransport`, отвечающим на `GET /publisher/publications`.
2. **Fallback-фикстуры** (`publisher_client.mock_publications`) — используются
   при сетевой ошибке publisher-а или при `ANALYTICS_USE_MOCK=1`. VK-метрики
   при отсутствии `VK_TOKEN` берутся из детерминированного
   `mock_vk_metrics(external_id)`.
