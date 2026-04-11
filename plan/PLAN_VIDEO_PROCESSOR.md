# План: модуль обработки видео в Docker

> Дата: 2026-04-11
> Статус: черновик, требует решения по точкам ❓
> Связанный план: [PLAN_VIDEO_DOWNLOADER.md](PLAN_VIDEO_DOWNLOADER.md)

---

## 1. Цель

Отдельный сервис, который берёт **уже скачанный файл** (из shared volume, положенный туда [video-downloader](PLAN_VIDEO_DOWNLOADER.md)) и производит:

- **Транскрипцию** (аудио → текст)
- **Извлечение кадров** (равные интервалы или keyframes)
- **Vision-анализ** (кадры → структурированное описание через Claude Vision / GPT-4o)
- **Полный анализ** (всё перечисленное в одном job)

**Зачем выделять:**
- Тяжёлые зависимости (ffmpeg, faster-whisper, ~2GB образ) — изоляция от backend
- Долгие задачи (Whisper на 10 мин видео = минуты) — нельзя в request-handler FastAPI
- Можно вынести на машину с GPU без миграции backend
- Полная замена [transcriber.py](backend/transcriber.py) с расширенным функционалом

---

## ⚡ Тестовый сценарий MVP

На первом этапе [video-downloader](PLAN_VIDEO_DOWNLOADER.md) работает в **stub-режиме** и всегда возвращает один и тот же файл — [Залетевший роллик.mp4](../Залетевший%20роллик.mp4). Это значит processor можно разрабатывать и end-to-end тестировать против детерминированного входа:

1. Backend (или curl) → `POST processor/jobs/full-analysis {"url": "any"}`
2. Processor → `POST downloader/jobs/download` → получает путь `/media/downloads/test_fixture.mp4`
3. Processor выполняет: extract_audio → transcribe → extract_frames → vision_analyze
4. Возвращает результат

Для разработки processor НЕ нужен ни один реальный парсер. Реальные платформенные стратегии в downloader-е появятся позже, processor останется без изменений (контракт API тот же).

---

## 2. Точки решения (нужны ответы)

### ❓ 2.1 Что входит в первой итерации

- [ ] **A. Извлечение аудио** (ffmpeg, обязательно для транскрипции)
- [ ] **B. Транскрипция**
  - [ ] B1. YouTube subtitles (если файл — youtube; бесплатно)
  - [ ] B2. faster-whisper локально (CPU, бесплатно)
  - [ ] B3. AssemblyAI (как сейчас, платно)
- [ ] **C. Извлечение кадров** (ffmpeg)
- [ ] **D. Vision-анализ** (кадры → Claude Vision)
- [ ] **E. Объединённый full_analysis** (B + C + D в одном job)

**Рекомендация:** A + B (все три стратегии) + C + D + E. Это полная замена transcriber и сразу даёт vision-возможности.

### ❓ 2.2 Whisper локально

| Модель | Размер | CPU realtime | Качество |
|---|---|---|---|
| `tiny` | 75 MB | ~10× | низкое |
| `base` | 150 MB | ~5× | приемлемое |
| `small` | 500 MB | ~2× | хорошее |
| `medium` | 1.5 GB | ~1× | очень хорошее |

**Рекомендация:** `base` по умолчанию, параметризовать через env `WHISPER_MODEL`. faster-whisper (CTranslate2) — в 4× быстрее обычного OpenAI whisper.

### ❓ 2.3 Vision-провайдер

- [ ] **Claude Vision** (claude-sonnet-4) — лучшее качество анализа, ~$0.003/кадр
- [ ] **GPT-4o** — дешевле, ~$0.001/кадр, чуть хуже на детализации
- [ ] **Оба с роутингом** (как [ai/router.py](backend/ai/router.py) в основном backend)

**Рекомендация:** оба с fallback. Дефолт Claude, GPT-4o при недоступности.

### ❓ 2.4 Сколько кадров для vision

Trade-off: больше кадров → точнее анализ → дороже и медленнее.

- 4 кадра — быстро, дёшево, для коротких роликов
- 8 кадров — баланс (рекомендация)
- 16 кадров — для длинных видео или подробного разбора
- Адаптивно по длительности (`min(8, max(4, duration_sec / 10))`)

**Рекомендация:** дефолт 8, параметризовать через payload, опционально адаптивный режим.

### ❓ 2.5 Принимает только локальные файлы или ещё URL?

- **Только `file_path`** (чисто, processor не зависит от downloader API)
- **`file_path` ИЛИ `url`** (если url — processor сам дёргает downloader)

**Рекомендация:** оба. Backend дёргает только processor с url — processor сам оркестрирует downloader. Проще для backend, downloader остаётся внутренней деталью.

### ❓ 2.6 Кеширование результатов

Vision-анализ дорогой. Если backend заново попросит анализ того же видео — пересчитывать или кешировать?

- **Кеш в БД processor** (по hash файла или external_id) — повторный запрос моментально
- **Без кеша** — backend сам решает, когда дёргать

**Рекомендация:** кешировать transcript и vision по `(platform, external_id)` в SQLite. TTL 30 дней.

---

## 3. Архитектура

```
┌─────────────────────┐    HTTP     ┌──────────────────────────┐
│  backend (FastAPI)  │ ──────────► │  video-processor         │
│                     │  POST /jobs │  (FastAPI + ffmpeg +     │
│                     │  GET  /jobs │   faster-whisper +       │
│                     │ ◄────────── │   anthropic + openai)    │
└──────────┬──────────┘             │                          │
           │                        │  jobs.db (SQLite)        │
           │                        │  cache.db (SQLite)       │
           │                        └────┬─────────────────────┘
           │                             │
           │                             │  если payload содержит url:
           │                             ▼
           │                   ┌──────────────────────┐
           │                   │  video-downloader    │
           │                   │  (POST /jobs/download)│
           │                   └──────────┬───────────┘
           │                              │
           │             shared volume    │
           └─────────────► ./data/media/ ◄┘
                             downloads/
                             audio/        ◄── processor пишет
                             frames/       ◄── processor пишет
                             transcripts/  ◄── processor пишет
```

**API:**

```
POST /jobs/transcribe
  body: {
    file_path?: "/media/downloads/youtube_abc.mp4",
    url?: "https://...",                  # альтернатива file_path
    platform?, external_id?,              # для кеша
    language?: "ru|en|auto",
    provider?: "auto|youtube_subs|whisper|assemblyai"
  }
  → 202 { job_id }

POST /jobs/extract-frames
  body: { file_path | url, count: 8, mode?: "uniform|keyframes" }
  → 202 { job_id }

POST /jobs/vision-analyze
  body: {
    file_path | url,
    frame_count: 8,
    prompt_template?: "default|detailed|hooks_focused",
    provider?: "claude|gpt4o|auto"
  }
  → 202 { job_id }

POST /jobs/full-analysis
  body: { file_path | url, frame_count: 8 }
  → 202 { job_id }
  # выполняет: download (если url) → transcribe → extract_frames → vision_analyze

GET /jobs/{job_id}
  → {
      status: queued|running|done|failed,
      result?: {
        transcript?: { text, language, provider, duration_sec },
        frames?: [{ index, timestamp_sec, file_path }],
        vision?: { hook, structure, scenes: [...], why_viral, emotion_trigger },
        cost_usd: { whisper: 0, vision: 0.024, total: 0.024 }
      },
      error?, started_at, finished_at
    }

DELETE /cache/{platform}/{external_id}
  → 204   # принудительная инвалидация кеша

GET /healthz
  → { status, ffmpeg_version, whisper_model, whisper_loaded,
      disk_free_gb, queue_depth, active_jobs, cache_size }
```

Авторизация: header `X-Worker-Token` (env `PROCESSOR_TOKEN`).

---

## 4. Структура

```
video_processor/
├── Dockerfile               # python:3.11-slim + ffmpeg + предзагрузка faster-whisper base
├── requirements.txt         # fastapi, faster-whisper, ffmpeg-python, anthropic, openai,
│                            # httpx, sqlalchemy, structlog, pillow
├── main.py                  # FastAPI app, lifespan (загрузка модели Whisper)
├── config.py                # env: PROCESSOR_TOKEN, MEDIA_DIR, WHISPER_MODEL,
│                            #      DOWNLOADER_URL, ANTHROPIC_KEY, OPENAI_KEY, ASSEMBLYAI_KEY
├── jobs/
│   ├── store.py             # SQLite jobs table
│   ├── queue.py             # asyncio.Queue + worker
│   └── router.py            # /jobs endpoints
├── cache/
│   ├── store.py             # SQLite cache (platform, external_id, type, result_json, expires_at)
│   └── router.py            # DELETE /cache endpoints
├── tasks/
│   ├── extract_audio.py     # ffmpeg → 16kHz mono mp3 в /media/audio/
│   ├── transcribe.py        # YT subs → Whisper local → AssemblyAI fallback chain
│   ├── extract_frames.py    # ffmpeg uniform/keyframes → /media/frames/{job_id}/
│   ├── vision_analyze.py    # кадры → Claude/GPT-4o Vision с structured output
│   └── full_analysis.py     # оркестратор: download → transcribe → frames → vision
├── clients/
│   ├── downloader.py        # httpx-обёртка над video-downloader API
│   ├── claude_vision.py
│   ├── openai_vision.py
│   └── assemblyai.py        # портирован из backend/transcriber.py
├── prompts/
│   ├── vision_default.py
│   ├── vision_detailed.py
│   └── vision_hooks.py
└── tests/
    ├── test_extract_audio.py
    ├── test_transcribe_whisper.py
    └── test_vision.py
```

---

## 5. План реализации

### Этап 1 — Скелет (0.5 дня)
- [ ] `Dockerfile`: python:3.11-slim + ffmpeg + `pip install faster-whisper` + предзагрузка модели base в build-stage
- [ ] FastAPI app, `/healthz`, заглушки `/jobs/*`
- [ ] Сервис в общем `docker-compose.yml` рядом с downloader, тот же volume
- [ ] Smoke: контейнер стартует, `/healthz` отвечает с `whisper_loaded=true`

### Этап 2 — Job store + queue + auth (0.5 дня)
- [ ] SQLite `jobs` (можно скопировать паттерн из downloader)
- [ ] `asyncio.Queue` + worker, лимит `MAX_CONCURRENT` (для Whisper — 1, для vision — 4)
- [ ] Middleware `X-Worker-Token`
- [ ] Cache store: `cache(platform, external_id, type, result_json, expires_at)`

### Этап 3 — Audio + Transcribe (1.5 дня)
- [ ] `tasks/extract_audio.py`: ffmpeg → 16kHz mono mp3 в `media/audio/{external_id}.mp3`
- [ ] `clients/whisper.py`: faster-whisper, lazy-load в lifespan
- [ ] `clients/assemblyai.py`: портировать из [backend/transcriber.py](backend/transcriber.py)
- [ ] `tasks/transcribe.py`: chain `youtube_subs → whisper → assemblyai`
- [ ] Запись результата в `media/transcripts/{external_id}.json`
- [ ] Кеширование по `(platform, external_id)`
- [ ] Логирование cost_usd
- [ ] Тесты на 10-секундном тестовом файле

### Этап 4 — Extract frames (0.5 дня)
- [ ] `tasks/extract_frames.py`: ffmpeg `-vf fps=N/duration` для uniform, `-vf select='eq(pict_type,I)'` для keyframes
- [ ] Запись в `media/frames/{job_id}/frame_001.jpg`
- [ ] JPEG quality 85, max-width 1280

### Этап 5 — Vision analyze (1 день)
- [ ] `clients/claude_vision.py`: загрузка кадров в Claude, structured output (Pydantic schema)
- [ ] `clients/openai_vision.py`: то же для GPT-4o
- [ ] `prompts/vision_default.py`: промпт «найди hook, structure, scenes, why_viral, emotion_trigger»
- [ ] `tasks/vision_analyze.py`: загрузка кадров → провайдер → парсинг → cost tracking
- [ ] Кеширование по `(platform, external_id)`

### Этап 6 — Full analysis оркестратор (0.5 дня)
- [ ] `tasks/full_analysis.py`:
  1. Если payload содержит `url` → дёрнуть downloader, дождаться `file_path`
  2. extract_audio + transcribe (parallel со step 3)
  3. extract_frames + vision_analyze
  4. Объединить результат
- [ ] Прогнать на реальном youtube-видео end-to-end

### Этап 7 — Backend integration (0.5 дня)
- [ ] `backend/clients/processor_client.py`: httpx-обёртка с поллингом
- [ ] Заменить [backend/transcriber.py](backend/transcriber.py) на тонкую обёртку, либо удалить
- [ ] Конфиг: `PROCESSOR_URL`, `PROCESSOR_TOKEN` в `.env`
- [ ] [routers/videos.py](backend/routers/videos.py) `POST /api/videos/{id}/analyze` — дёргает processor full-analysis вместо локальной обработки
- [ ] [routers/analyze.py](backend/routers/analyze.py) `POST /api/analyze-url` — то же

**Итого: ~4.5 дня**

---

## 6. Что НЕ делаем

- ❌ Скачивание (это [video-downloader](PLAN_VIDEO_DOWNLOADER.md))
- ❌ GPU-инференс Whisper (только CPU, добавится позже одной env-переменной)
- ❌ Diarization (кто говорит) — не нужно для коротких роликов
- ❌ Перекодирование / нарезка клипов
- ❌ Web UI

---

## 7. Риски

| Риск | Митигация |
|---|---|
| **Whisper медленный на CPU** | Дефолт `base` модель, лимит длительности 10 мин (как в текущем transcriber); AssemblyAI как fallback при превышении |
| **Vision API дорогой** | Дефолт 8 кадров; кеширование на 30 дней; cost tracking в каждом job |
| **OOM при загрузке Whisper** | `MAX_CONCURRENT_TRANSCRIBE=1`, отдельная очередь от vision |
| **Запросы к downloader зависают** | Per-job timeout 5 мин; статус `failed` с понятной ошибкой |
| **Размер образа ~2GB** | Multi-stage build; предзагрузка модели в отдельный layer для кеша |
| **Кеш разрастается** | TTL 30 дней + ежедневная очистка; лимит 10000 записей |
| **Vision на размытых кадрах** | Mode `keyframes` вместо `uniform` для роликов с быстрыми переходами |

---

## 8. Открытые вопросы

1. **Скоуп** (см. 2.1) — все из A–E или подмножество?
2. **Whisper модель по умолчанию** — `base` или `small`?
3. **Vision-провайдер** (см. 2.3) — Claude, GPT-4o или оба с роутингом?
4. **Кадры по умолчанию** — фикс 8 или адаптивно по длительности?
5. **Принимает ли processor `url`** (см. 2.5), или только локальные `file_path`?
6. **Кеш TTL 30 дней** — ок или иначе?
7. **Что делать с [backend/transcriber.py](backend/transcriber.py)** — удалить полностью после миграции, или оставить как fallback?

---

## 9. Общая инфраструктура с downloader

`docker-compose.yml` содержит **оба сервиса** + shared volume — см. раздел 9 в [PLAN_VIDEO_DOWNLOADER.md](PLAN_VIDEO_DOWNLOADER.md).

Паттерны (jobs store, auth middleware, structlog, healthz) — одинаковые в обоих сервисах. Если делать downloader первым, второй сервис скопирует общий код. Если оба одновременно — есть смысл вынести в `shared/` Python-пакет, который оба контейнера импортируют через bind mount.
