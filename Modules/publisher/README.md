# publisher — публикация видео в соцсети (Phase 1: VK)

Микросервис автопубликации коротких видео. Phase 1 — только **VK** (Видео и
Клипы). FastAPI + синхронный SQLite + httpx. Слушает порт **8000** внутри
контейнера (host-маппинг dev — `8800:8000`).

> ⚠️ По умолчанию работает в **dry-run**: реальных HTTP-вызовов к `api.vk.com`
> НЕ происходит. Live-публикация включается только `DEFAULT_DRY_RUN=false`
> при наличии валидного `VK_ACCESS_TOKEN`.

## Архитектура

```
shell  ──/api/publisher/*──►  publisher  ──┬─ dry_run.py     (dry-run: лог payload, без сети)
        (X-Worker-Token)                   └─ vk_client.py   (live: httpx → api.vk.com)
                                            │
                                   storage.py (SQLite WAL, синхронный)
                                   scheduler.py (asyncio task, раз в минуту)
```

| Файл | Назначение |
|------|------------|
| `main.py` | FastAPI app, lifespan (Store + scheduler), `/publisher/healthz` (объявлен ДО include_router) |
| `router.py` | роуты `/publisher/*` (publish / schedule / retry / publications) |
| `admin.py` | `/publisher/admin/*` (config, stats) — под `X-Admin-Token`, shell их не проксирует |
| `service.py` | бизнес-логика: dry-run vs live, обновление статусов в БД |
| `storage.py` | `PublicationStore` — синхронный sqlite3 (WAL, busy_timeout, row_factory) |
| `vk_client.py` | live VK API клиент (httpx): `video.save`, upload, `wall.post`, `stats.getPostReach` |
| `dry_run.py` | симуляция публикации без сети |
| `scheduler.py` | фоновая очередь отложенных публикаций |
| `schemas.py` | pydantic-контракты |
| `config.py` | настройки (pydantic-settings, `.env.publisher`) |

## Контракт publication

```jsonc
{
  "id": "uuid",
  "platform": "vk_video" | "vk_clips",
  "external_id": "string | null",   // в VK "{owner_id}_{video_id}" или "dry-run-<uuid>"
  "title": "string",
  "description": "string",
  "tags": ["string"],
  "status": "dry_run" | "scheduled" | "publishing" | "published" | "failed",
  "scheduled_at": "iso | null",
  "published_at": "iso | null",
  "error_message": "string | null"
}
```

## Эндпоинты

Все требуют `X-Worker-Token` (admin — `X-Admin-Token`).

| Метод | Путь | Описание |
|-------|------|----------|
| GET  | `/publisher/healthz` | health (без auth) |
| GET  | `/publisher/publications?limit=&platform=&status=` | список публикаций |
| GET  | `/publisher/publications/{id}` | одна публикация |
| POST | `/publisher/publish` | немедленная публикация (dry-run или live) |
| POST | `/publisher/schedule` | отложенная (в очередь scheduler-а) |
| POST | `/publisher/retry/{id}` | повтор |
| DELETE | `/publisher/publications/{id}` | удалить |
| GET  | `/publisher/admin/config` | конфиг (без секретов) |
| GET  | `/publisher/admin/stats` | счётчики по статусам |

### POST /publisher/publish

Запрос:
```json
{
  "video_path": "/media/uploads/clip.mp4",
  "title": "Топ-1 вирусный приём",
  "description": "Разбор хука",
  "tags": ["viral", "shorts"],
  "platforms": ["vk_video", "vk_clips"],
  "dry_run": true
}
```
Ответ:
```json
{ "publication_ids": ["uuid1", "uuid2"], "status": "dry_run" }
```

`dry_run` опускается → берётся `DEFAULT_DRY_RUN` из env. Если `dry_run=true`
ИЛИ `DEFAULT_DRY_RUN=true` — публикация создаётся со `status='dry_run'`,
`external_id='dry-run-<uuid>'`, **без обращения к api.vk.com**.

### POST /publisher/schedule

То же + обязательное `scheduled_at` (iso). Создаёт `status='scheduled'`.
Фоновый scheduler раз в минуту берёт записи `scheduled_at <= NOW` и публикует.

## Env

| Переменная | Default | Описание |
|------------|---------|----------|
| `PUBLISHER_TOKEN` | `dev-worker-token-change-me` | worker-токен |
| `PUBLISHER_ADMIN_TOKEN` | `dev-admin-token-change-me` | admin-токен |
| `DB_DIR` | `/db` | каталог SQLite |
| `MEDIA_DIR` | `/media` | каталог видео |
| `DEFAULT_DRY_RUN` | `true` | **true → без HTTP к VK** |
| `VK_ACCESS_TOKEN` | — | токен VK (нужен только для live) |
| `VK_API_VERSION` | `5.199` | версия VK API |
| `VK_GROUP_ID` | — | id группы для wall.post (пусто → стена юзера) |

Образец — `.env.publisher.example` в корне репо.

## Ограничение VK Клипов (vk_clips)

**Официального документированного метода загрузки в Клипы через VK Public API
нет.** В неофициальных источниках упоминается скрытый метод `shortVideo.create`
(POST `https://api.vk.com/method/shortVideo.create` → `upload_url` → multipart
загрузка), но он **не входит** в официальную документацию `vk.com/dev`, может
требовать спец-прав/официального приложения и отвалиться без предупреждения.

Чтобы не зависеть от недокументированного API, `vk_clips` **деградирует** на
официальный путь `video.save` + `wall.post` (см. `VKClient.publish_clip`):
видео грузится как обычное VK-видео и публикуется на стену; VK при подходящем
соотношении сторон (вертикаль 9:16) может показать его в Клипах, но это не
гарантируется API. Несуществующий метод сервис **не вызывает**.

Источник: поиск по VK API на май 2026 — `shortVideo.create` фигурирует только
в неофициальных скриптах, в `vk.com/dev/` отсутствует.

## Тесты

```
cd Modules/publisher && pytest -q
```

- `tests/test_vk_client.py` — live-путь VK через `httpx.MockTransport` (без сети);
  проверка, что Клипы НЕ дёргают `shortVideo`/clips-методы.
- `tests/test_dry_run.py` — гарантия **отсутствия** HTTP к api.vk.com в dry-run
  (любой исходящий запрос роняет тест).
- `tests/test_integration.py` — publish → запись в БД → статус через TestClient.

## Локальный запуск

```
docker compose up -d --build publisher
curl -s localhost:8800/publisher/healthz
```
