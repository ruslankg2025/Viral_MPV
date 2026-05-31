# PLAN_PUBLISHER — автопубликация видео в соцсети

Архитектурное ТЗ микросервиса `publisher`. Phase 1 — VK (Видео + Клипы).

## Цель

Дать платформе единый сервис автопубликации коротких видео в соцсети с
очередью отложенных постов, статусной моделью и безопасным dry-run по
умолчанию. Phase 1 ограничена VK; контракт спроектирован расширяемым на
другие платформы (Instagram Reels, YouTube Shorts, TikTok) без изменения формы
`publication`.

## Место в системе

- 9-й микросервис. Порт **8000** внутри (host dev — `8800:8000`, прод — без портов).
- За shell-прокси: `/api/publisher/*` → `<PUBLISHER_URL>/publisher/*`,
  инъекция `X-Worker-Token`, admin-сегмент заблокирован.
- Своя SQLite-БД (`publisher_db` volume), своё `.env.publisher`.

## Модель данных

Таблица `publications` (см. `storage.py`). Контракт `publication`:

```
id, platform(vk_video|vk_clips), external_id, title, description, tags[],
status(dry_run|scheduled|publishing|published|failed),
scheduled_at, published_at, error_message
```

Статусная машина:

```
publish (dry)      → dry_run
publish (live)     → publishing → published | failed
schedule           → scheduled → (scheduler) → publishing → published | failed
retry              → повтор execute с текущим режимом dry-run
```

Миграции идемпотентные: `PRAGMA table_info` перед `ALTER`, индекс по новой
колонке — только после её ALTER (конвенция VM).

## Компоненты

- **router.py** — HTTP-контракт `/publisher/*`.
- **service.py** — `resolve_dry_run()` + `execute_publication()`: единая точка,
  где решается dry-run vs live и пишутся статусы. Используется и router-ом, и
  scheduler-ом.
- **dry_run.py** — симуляция без сети (лог payload, `dry-run-<uuid>`). НЕ
  импортирует vk_client → физически не может сходить в VK.
- **vk_client.py** — единственный модуль с реальными HTTP к `api.vk.com`
  (httpx.AsyncClient). Методы: `video.save` → upload → `wall.post` →
  `stats.getPostReach`.
- **scheduler.py** — asyncio-task в lifespan, раз в минуту публикует «дозревшие»
  scheduled-записи.

## Безопасность публикации (dry-run)

`DEFAULT_DRY_RUN=true` по умолчанию. Реальная публикация требует явного
`DEFAULT_DRY_RUN=false` + валидного `VK_ACCESS_TOKEN`. Это защищает staging и
CI от случайных постов в боевые аккаунты. Тесты дополнительно банят любой
исходящий HTTP в dry-run.

## VK Клипы — ограничение API

Официального документированного метода загрузки в **Клипы** через VK Public API
нет. Недокументированный `shortVideo.create` существует, но в `vk.com/dev`
отсутствует и ненадёжен. Решение: `vk_clips` деградирует на официальный
`video.save + wall.post`; несуществующий метод не вызывается. Подробности —
`Modules/publisher/README.md` → «Ограничение VK Клипов».

## Будущие фазы

- Phase 2: Instagram Reels (Graph API), YouTube Shorts (Data API), TikTok.
- Привязка публикации к аккаунту (`account_id`) и к исходной карусели/сценарию.
- Вебхуки/поллинг метрик (reach, views) после публикации (`stats.getPostReach`
  уже реализован в клиенте).
- UI в shell SPA: очередь, календарь отложенных, статусы.

## DoD Phase 1

- Сервис собирается; `/publisher/healthz` = 200.
- `POST /publisher/publish` с `dry_run=true` пишет в БД и НЕ ходит в api.vk.com.
- Live-путь покрыт моками (httpx.MockTransport).
- Тесты зелёные. Доки на месте (README + этот PLAN).
- Реальный live-upload — вне DoD Phase 1.
