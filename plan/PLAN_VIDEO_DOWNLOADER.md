# План: модуль скачивания видео в Docker

> Дата: 2026-04-11
> Статус: черновик, требует решения по точкам ❓
> Связанный план: [PLAN_VIDEO_PROCESSOR.md](PLAN_VIDEO_PROCESSOR.md)

---

## 1. Цель

Отдельный сервис, который умеет одно: **получить URL видео → положить файл в shared volume → вернуть путь и метаданные**. Никакой обработки (ffmpeg, транскрипция, AI) — это задача [video_processor](PLAN_VIDEO_PROCESSOR.md).

**Зачем выделять:**
- yt-dlp / instagrapi / Apify-клиенты — отдельный набор зависимостей и точек отказа
- Скачивание ломается чаще всего (платформы меняют API) — изоляция упрощает обновления
- Можно масштабировать независимо: один downloader на N processor-ов
- IP-блокировки касаются только этого контейнера; processor работает с уже скачанными файлами

---

## ⚡ Режим MVP / тестирования

**Для первой итерации** downloader работает в **stub-режиме**: игнорирует URL платформы и всегда возвращает один тестовый файл [Залетевший роллик.mp4](../Залетевший%20роллик.mp4) (лежит в корне проекта). Это нужно, чтобы:

- Сразу разрабатывать [video-processor](PLAN_VIDEO_PROCESSOR.md), не дожидаясь готовности парсеров
- Изолированно отлаживать контракт API между сервисами
- Прогонять end-to-end pipeline на детерминированном входе

**Поведение stub-режима:**
- `POST /jobs/download` принимает любой `url`, но возвращает фиксированный результат
- Файл копируется (или симлинкается) в `./data/media/downloads/test_fixture.mp4` при старте контейнера
- Метаданные (duration, width, height, size) извлекаются один раз через `ffprobe`
- Все остальные эндпоинты (`GET /jobs/{id}`, `DELETE /files/{id}`, `/healthz`) работают штатно

Stub-режим включается env-переменной `STUB_MODE=true` (дефолт для dev). Реальные стратегии (yt-dlp, Apify, instagrapi) реализуются после того, как processor pipeline доказал свою работоспособность на тестовом файле.

---

## 2. Точки решения (нужны ответы)

### ❓ 2.1 Платформы в первой итерации

- [ ] YouTube
- [ ] Instagram (Reels)
- [ ] TikTok
- [ ] VK
- [ ] Все четыре (как в основном backend)

**Рекомендация:** все четыре — иначе processor останется без половины источников.

### ❓ 2.2 Стратегии скачивания для Instagram/TikTok

Instagram особенно проблемный (блокировки, rate limits). Варианты:

| Стратегия | Плюсы | Минусы |
|---|---|---|
| **yt-dlp** | Бесплатно, единый API | Часто ломается, требует cookies |
| **Apify actor** | Стабильно, residential proxy | Платно (~$0.30 за 1000 видео) |
| **instagrapi** | Mobile API, надёжно | Нужен реальный аккаунт-парсер |

**Рекомендация:** chain с fallback — `yt-dlp → Apify → instagrapi`. Та же логика, что в основном backend ([instagram_apify.py](backend/parsers/instagram_apify.py), [instagram_instagrapi.py](backend/parsers/instagram_instagrapi.py), [instagram_legacy.py](backend/parsers/instagram_legacy.py)). Можно переиспользовать код парсеров.

### ❓ 2.3 Качество и формат

- [ ] **Лучшее доступное** (`bestvideo+bestaudio`) — больше места, лучше для vision
- [ ] **Среднее** (`best[height<=720]`) — компромисс
- [ ] **Только аудио** (`bestaudio`) — если нужна только транскрипция, экономия 90% места
- [ ] **Параметризовать** через payload запроса

**Рекомендация:** параметризовать. Дефолт `best[height<=720]` для full-analysis, `bestaudio` для transcribe-only.

### ❓ 2.4 Авторизация платформ (cookies/sessions)

Нужно для Instagram (private content), иногда YouTube (age-restricted). Куда складывать:

- **В env-переменной** worker'а (cookies = строка)
- **В файле в volume** (`./data/secrets/instagram.cookies`)
- **В payload каждого запроса** (backend хранит, передаёт)

**Рекомендация:** payload запроса. Backend уже хранит ScraperProfile.session_json — пусть передаёт worker'у в нужный момент. Это убирает дублирование секретов.

### ❓ 2.5 TTL и очистка

Файлы могут весить десятки МБ. Когда удалять?

- **По таймауту** (старше N дней)
- **По сигналу от backend** (после успешного processor-job — DELETE /files/{path})
- **При нехватке места** (LRU)

**Рекомендация:** комбинация — TTL 7 дней + endpoint `DELETE /files/{job_id}` для явного освобождения после обработки.

---

## 3. Архитектура

```
┌─────────────────────┐    HTTP     ┌──────────────────────────┐
│  backend (FastAPI)  │ ──────────► │  video-downloader        │
│                     │  POST /jobs │  (FastAPI + yt-dlp +     │
│                     │  GET  /jobs │   Apify + instagrapi)    │
│                     │ ◄────────── │                          │
└──────────┬──────────┘             │  jobs.db (SQLite)        │
           │                        └────────────┬─────────────┘
           │                                     │
           │            shared volume            ▼
           └─────────► ./data/media/downloads/ ◄─┘
                         youtube_<id>.mp4
                         instagram_<id>.mp4
                         tiktok_<id>.mp4
                         vk_<id>.mp4
```

**API:**

```
POST /jobs/download
  body: {
    url: "https://...",
    platform: "youtube|instagram|tiktok|vk",
    quality: "best|720p|audio_only",       # default 720p
    auth?: { cookies?, session_json?, ... }
  }
  → 202 { job_id }

GET /jobs/{job_id}
  → {
      status: queued|running|done|failed,
      result?: {
        file_path: "/media/downloads/youtube_abc123.mp4",
        external_id: "abc123",
        platform: "youtube",
        duration_sec: 185,
        width: 1280, height: 720,
        size_bytes: 18452310,
        format: "mp4"
      },
      error?, started_at, finished_at
    }

DELETE /files/{job_id}
  → 204   # удаляет файл из volume

GET /healthz
  → { status, ytdlp_version, disk_free_gb, queue_depth, active_jobs }
```

Авторизация: header `X-Worker-Token` (env `DOWNLOADER_TOKEN`).

---

## 4. Структура

```
video_downloader/
├── Dockerfile               # python:3.11-slim + ffmpeg (+ yt-dlp на этапе 3)
├── requirements.txt         # MVP: fastapi, httpx, sqlalchemy, structlog
│                            # later: + yt-dlp, instagrapi, apify-client
├── main.py                  # FastAPI app, lifespan (копирование фикстуры в stub-режиме)
├── config.py                # env: DOWNLOADER_TOKEN, MEDIA_DIR, STUB_MODE, TTL_DAYS, MAX_CONCURRENT
├── fixture.py               # MVP: загрузка test_fixture.mp4 + ffprobe метаданные
├── jobs/
│   ├── store.py             # SQLite jobs table
│   ├── queue.py             # asyncio.Queue + worker
│   └── router.py            # /jobs endpoints
├── strategies/              # этап 3+
│   ├── base.py              # BaseDownloader ABC
│   ├── youtube.py           # yt-dlp
│   ├── instagram.py         # yt-dlp → Apify → instagrapi (fallback chain)
│   ├── tiktok.py            # yt-dlp → Apify
│   └── vk.py                # yt-dlp
├── cleanup.py               # TTL cleanup loop (этап 5)
├── files_router.py          # DELETE /files/{job_id}
└── tests/
    ├── test_stub.py
    ├── test_youtube.py
    └── test_instagram.py
```

---

## 5. План реализации

### 🟢 MVP (stub-режим, ~1 день)

#### Этап 1 — Скелет + stub (0.5 дня)
- [ ] `Dockerfile` (python:3.11-slim + ffmpeg)
- [ ] FastAPI app, `/healthz`, `/jobs/download` со stub-логикой
- [ ] Lifespan: при старте копировать `Залетевший роллик.mp4` → `/media/downloads/test_fixture.mp4`, прогнать `ffprobe`, закешировать метаданные
- [ ] Сервис в `docker-compose.yml`, volume `./data/media`, env `STUB_MODE=true`
- [ ] Smoke: `curl -X POST .../jobs/download -d '{"url":"any"}'` возвращает путь к фикстуре

#### Этап 2 — Job store + queue + auth (0.5 дня)
- [ ] SQLite-таблица `jobs(id, type, status, payload_json, result_json, error, created_at, started_at, finished_at)`
- [ ] `asyncio.Queue` + фоновый worker (для stub — мгновенное завершение)
- [ ] Middleware `X-Worker-Token`
- [ ] `GET /jobs/{id}`, `DELETE /files/{id}`
- [ ] Тесты статус-машины

**После этих двух этапов** processor можно начинать разрабатывать против рабочего downloader.

---

### 🟡 Реальные стратегии (после того, как processor работает на stub)

#### Этап 3 — YouTube downloader (0.5 дня)
- [ ] Убрать `STUB_MODE` или сделать его опциональным per-request
- [ ] `strategies/youtube.py`: yt-dlp programmatic API
- [ ] Извлечение метаданных (duration, width/height, size)
- [ ] Запись в `media/downloads/youtube_<external_id>.<ext>`
- [ ] Тест на коротком публичном видео

#### Этап 4 — Instagram/TikTok/VK (1 день)
- [ ] `strategies/instagram.py`: yt-dlp → Apify → instagrapi с fallback
- [ ] `strategies/tiktok.py`: yt-dlp → Apify
- [ ] `strategies/vk.py`: yt-dlp
- [ ] Передача `auth` payload (cookies, session_json) в нужную стратегию
- [ ] Тесты со статичными URL

#### Этап 5 — Очистка и метрики (0.5 дня)
- [ ] TTL-cleanup loop (по умолчанию 7 дней)
- [ ] Расширенный `/healthz`: queue depth, active, disk free
- [ ] Алерт в логах при <2GB free

#### Этап 6 — Интеграция с backend (0.5 дня)
- [ ] `backend/clients/downloader_client.py`: httpx-обёртка с поллингом
- [ ] Конфиг: `DOWNLOADER_URL`, `DOWNLOADER_TOKEN` в `.env`
- [ ] Background task в [routers/videos.py](backend/routers/videos.py): при запросе анализа → скачать → передать processor

**Итого: ~1 день MVP + ~3 дня реальные стратегии**

---

## 6. Что НЕ делаем

- ❌ Транскрипция, ffmpeg-обработка, vision — это [video_processor](PLAN_VIDEO_PROCESSOR.md)
- ❌ S3 / MinIO (только локальный volume)
- ❌ Прямая стриминг-передача файлов (только volume)
- ❌ GUI / web UI

---

## 7. Риски

| Риск | Митигация |
|---|---|
| **yt-dlp ломается** | Pin версию + еженедельный авто-апдейт через cron / `pip install -U yt-dlp` в healthcheck |
| **IP-блокировка Instagram/TikTok** | Fallback на Apify (residential proxy); поддержка cookies в payload |
| **Диск переполняется** | TTL 7 дней + DELETE endpoint + алерт <2GB |
| **Долгие скачивания блокируют queue** | Лимит `MAX_CONCURRENT` + per-job timeout 5 мин |
| **Размер файлов** | Дефолт `720p`, не `best`; для transcribe-only — `audio_only` |

---

## 8. Открытые вопросы

1. **Платформы** (см. 2.1) — все четыре или подмножество?
2. **Apify ключ** — переиспользуем тот же, что в основном backend, или отдельный для downloader?
3. **Cookies/sessions** (см. 2.4) — устраивает payload-подход, или хранить в volume?
4. **TTL по умолчанию** — 7 дней ок, или другое значение?
5. **Дефолтное качество** — 720p mp4, или другое?

---

## 9. Общая инфраструктура с processor

`docker-compose.yml` будет содержать **оба сервиса** + shared volume:

```yaml
services:
  video-downloader:
    build: ./video_downloader
    volumes: [./data/media:/media]
    environment:
      DOWNLOADER_TOKEN: ${DOWNLOADER_TOKEN}
      MEDIA_DIR: /media

  video-processor:
    build: ./video_processor
    volumes: [./data/media:/media]
    environment:
      PROCESSOR_TOKEN: ${PROCESSOR_TOKEN}
      MEDIA_DIR: /media
```

Паттерны (jobs store, auth middleware, structlog setup, healthz) — одинаковые в обоих сервисах. После реализации первого можно скопировать общий код во второй или вынести в `shared/` пакет.
