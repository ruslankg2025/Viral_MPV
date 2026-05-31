# Cross-posting Phase 1 — VK (publisher + analytics + UI)

**Дата старта:** 2026-05-31
**Статус:** черновик
**Цель:** Запостить контент из VM в VK (Видео + Клипы) с планировщиком, собрать кросс-платформенную аналитику публикаций и дать UI (composer / queue / analytics). Всё — в **dry-run by default**; реальная публикация в @ruslan.roxber только под снятым флагом и под контролем Руслана.

## Контекст

Исходник — внешняя заметка `2026-05-31-VM-Spawn-Prompts-Phase1` (3 параллельных трека: publisher / analytics / shell-UI). При сверке с реальными конвенциями VM (эталон — `Modules/carousel/`, свежие коммиты `7103cdb`/`fbb1177` про shell-прокси и порядок миграций) найдено 6 фактических расхождений — они учтены ниже. Этот файл — источник правды для запуска агентов, заметка устарела.

## Зафиксированный контракт (Phase 0 — раздать всем агентам)

Форма publication:
```
publication = {
  id: str(uuid), platform: "vk_video"|"vk_clips",
  external_id: str|null, title, description, tags: [str],
  status: "dry_run"|"scheduled"|"publishing"|"published"|"failed",
  scheduled_at: iso|null, published_at: iso|null, error_message: str|null
}
```
Эндпоинты publisher (все с префиксом `/publisher`):
```
GET  /publisher/publications?limit=&platform=&status=  -> [publication]
POST /publisher/publish   {video_path,title,description,tags,platforms[],dry_run} -> {publication_ids[], status}
POST /publisher/schedule  {...+scheduled_at} -> то же
POST /publisher/retry/{id} -> {status}
GET  /publisher/healthz
```
Inter-service: внутренний порт всех контейнеров — **8000**; ходят напрямую (не через shell),
URL `http://publisher:8000/publisher/...`, заголовок `X-Worker-Token`.

## Конвенции VM (отличия от исходной заметки — ОБЯЗАТЕЛЬНО)

1. **Порт:** контейнер слушает `:8000`. 8800/8900 — только host-маппинг в dev-compose (`"8800:8000"`); на проде портов наружу нет.
2. **SQLite синхронный** (`sqlite3` + `PRAGMA journal_mode=WAL`, `isolation_level=None`, `busy_timeout=5000`). Async — только HTTP (httpx/aiohttp) и FastAPI-хендлеры. aiosqlite не использовать.
3. **Префикс роутов** именем сервиса (`/publisher/*`, `/analytics/*`). `healthz` объявлять **до** `include_router`.
4. **shell-прокси:** catch-all `/{path:path}` не матчит пустой путь (коммит `7103cdb`) — дёргать только именованные субпути, не корень `/api/<svc>`.
5. **Миграции** идемпотентные (`PRAGMA table_info` перед `ALTER`); индекс по новой колонке — только **после** её `ALTER` (коммит `fbb1177`).
6. **UI:** клиентского URL-роутера нет — `gotoScreen('id')` show/hide `<div id="...-screen">`. Экран `analytics-screen` уже существует — не дублировать.

Прочее: `X-Worker-Token` (как carousel/knowledge), `admin`-эндпоинты в `blocked_first_segments`; structlog + lifespan; `.env.<svc>.example` закоммитить, реальные — в `.gitignore`; проверить `deploy/sync-env.sh` на новые сервисы.

## Фазы

0. [ ] Зафиксировать контракт выше, раздать 3 агентам. Решить VK-таргет (личная страница vs группа) и получить `user_access_token` (см. «Что делает Руслан»).
1. [ ] **Track A — publisher** (`Modules/publisher/`): main/storage/vk_client/dry_run/Dockerfile/requirements/README + `.env.publisher.example`; schedule-worker; тесты; блоки в обоих compose + `depends_on` shell; `plan/PLAN_PUBLISHER.md`. Отдать `/openapi.json` в первый день.
2. [ ] **Track B — analytics** (`Modules/analytics/`): схема (platform_metrics, daily_snapshots), эндпоинты, hourly-fetcher (читает publisher по REST), адаптеры `platforms/{base,vk}.py`, тесты, compose-блоки + `depends_on` shell, `plan/PLAN_ANALYTICS_V2.md`.
3. [ ] **Track C — UI** (`Modules/shell/static/app/` + `shell/main.py`): экраны composer / queue / кросспостинг-аналитика (отдельный раздел nav, не ломая `analytics-screen`); прокси `/api/publisher/*` и `/api/analytics/*` + `PUBLISHER_URL`/`ANALYTICS_URL`; тесты.
4. [ ] Ручной smoke `docker compose up` всех трёх; верификация что dry-run не ходит в `api.vk.com`.
5. [ ] (Руслан) Апрув live-режима на одном тест-видео под снятым флагом.

## Координация (конфликтные файлы)

- `docker-compose.yml` / `deploy/docker-compose.prod.yml`: A добавляет блок `publisher`, B — `analytics`, оба трогают `depends_on` shell. Конфликт минимальный (разные строки); финальный merge `depends_on` — вручную.
- `shell/main.py` + `index.html` — только Track C, больше никто не трогает.

## Риски / открытые вопросы

- **VK Clips API**: официальный путь публикации в Клипы ограничен. Agent A обязан проверить, что реально доступно через `vk_api`; при отсутствии — деградировать на `video.save` + `wall.post` и явно задокументировать, не выдумывать метод.
- **VK-таргет**: личная @ruslan.roxber vs группа/сообщество — влияет на scopes и доступность wall/Клипов. Решить до выдачи токена.
- **DoD не включает реальный live-upload** — это ручной шаг Руслана под снятым флагом, не задача агента.
- Ротация Apify-токена (tech debt) — не блокирует, но рядом.

## Done criteria

- `docker compose up -d publisher analytics` собирается и стартует; `/publisher/healthz` и `/analytics/healthz` = 200.
- `POST /publisher/publish` c `dry_run=true` пишет в БД и НЕ делает HTTP к `api.vk.com` (проверено по логам/network).
- analytics hourly-fetcher тянет VK-метрики для test publications; эндпоинты дают верные агрегации.
- UI: composer создаёт publication через publisher API; queue показывает данные в реальном времени; кросспостинг-аналитика — из analytics API; существующие экраны не сломаны.
- Тесты зелёные; README + `plan/PLAN_*` на месте.
