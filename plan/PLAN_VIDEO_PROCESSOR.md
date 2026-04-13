# План: модуль обработки видео в Docker

> Дата: 2026-04-11
> Статус: черновик, требует решения по точкам ❓

---

## 1. Цель

Отдельный сервис, который принимает **уже готовый видеофайл** (локальный путь в shared volume) и производит:

- **Извлечение аудио** (ffmpeg)
- **Транскрипцию** (аудио → текст, через внешние API)
- **Извлечение кадров** (ffmpeg, равные интервалы или keyframes)
- **Vision-анализ** (кадры → структурированное описание, через внешние LLM API)
- **Полный анализ** (всё перечисленное в одном job)

**Зачем выделять:**
- Изоляция ffmpeg и job-очереди от backend
- Долгие задачи (транскрипция + vision одного ролика = десятки секунд) — нельзя в request-handler FastAPI
- Централизованное управление AI-ключами и учёт их использования
- Полная замена [transcriber.py](backend/transcriber.py) с расширенным функционалом

**Два принципа изоляции:**

1. **Processor не знает, откуда файл.** Принимает только локальный путь к файлу в shared volume. Оркестрация (скачивание, парсинг URL, выбор источника) — ответственность вызывающей стороны.
2. **Processor не держит AI-моделей локально.** Ни Whisper, ни никакой другой. Вся транскрипция и vision идут через внешние API-сервисы поставщиков. Ключи хранятся в БД processor и управляются через admin API. Это оставляет образ маленьким (~300 MB вместо ~2 GB), убирает CPU/GPU нагрузку и даёт единую точку учёта стоимости.

---

## ⚡ Тестовый сценарий MVP

Для разработки и end-to-end тестирования processor-у достаточно любого локального файла, например [Залетевший роллик.mp4](../Залетевший%20роллик.mp4), положенного в `./data/media/downloads/`:

1. Перед первым запуском админ создаёт в processor хотя бы один ключ транскрипции и один ключ vision через `POST /admin/api-keys`
2. Backend (или curl) кладёт файл в shared volume (вручную или через downloader — processor-у всё равно)
3. `POST processor/jobs/full-analysis {"file_path": "/media/downloads/test_fixture.mp4"}`
4. Processor выполняет: extract_audio → transcribe (внешний API) → extract_frames → vision_analyze (внешний API)
5. Возвращает результат + `cost_usd` с разбивкой по провайдерам

Processor не зависит ни от downloader, ни от реальных парсеров платформ — контракт API тот же вне зависимости от источника файла.

---

## 2. Точки решения (нужны ответы)

### ❓ 2.1 Что входит в первой итерации

- [ ] **A. Извлечение аудио** (ffmpeg, обязательно для транскрипции)
- [ ] **B. Транскрипция** (через внешние API, см. §2.2)
- [ ] **C. Извлечение кадров** (ffmpeg + OpenCV dedup, см. §2.4)
- [ ] **D. Vision-анализ** (кадры → внешние LLM API, см. §2.3)
- [ ] **E. Объединённый full_analysis** (B + C + D в одном job)
- [ ] **F. Admin API управления AI-ключами + usage stats** (см. §2.7)
- [ ] **G. Тестовый веб-интерфейс** для ручной проверки всех фич (см. §2.8)

**Рекомендация:** A + B + C + D + E + F + G. F — обязательный: без активных ключей сервис не может выполнить ни одного job. G — обязательный для dev-цикла: без UI-а ручное тестирование всех провайдеров и параметров sampling через curl — медленно и неудобно.

> Стратегия «YouTube subtitles» сюда не входит — она требует URL и знания источника, а processor работает только с локальным файлом. Если нужно, такую оптимизацию делает вызывающая сторона до вызова processor.

### ✅ 2.2 Провайдеры транскрипции — все через внешние API

Локальных моделей нет. Вся транскрипция идёт через внешние сервисы по ключам из БД processor. Ни один ключ не хардкодится в env и не запекается в образ.

| Провайдер | Модель | Цена (≈) | Языки | Примечания |
|---|---|---|---|---|
| **AssemblyAI** | `best` / `nano` | $0.37/час (`best`), $0.12/час (`nano`) | 99+ | Хорошо с русским, уже используется в [backend/transcriber.py](backend/transcriber.py) |
| **Deepgram** | `nova-3` | ~$0.0043/мин (~$0.258/час) | 36+ | Быстрый (<1 s latency), дешевле AssemblyAI |
| **OpenAI Whisper API** | `whisper-1` | $0.006/мин (~$0.36/час) | 99+ | Можно переиспользовать ключ, который используется для GPT-4o vision |
| **Groq Whisper** | `whisper-large-v3` | ~$0.04/час | 99+ | Самый дешёвый, очень быстрый за счёт LPU |

**Рекомендация:** подключить все четыре как плагины-клиенты. Дефолтная цепочка `deepgram → assemblyai → openai_whisper → groq_whisper` (от самого удобного к fallback). Пользователь может переопределить через `provider` в payload или поменять порядок в админке. Если ни одного активного ключа с подходящим провайдером — job падает сразу с `no_transcription_provider_available`.

### ✅ 2.3 Провайдеры Vision — все через внешние API

| Провайдер | Модель | Цена (≈) | Примечания |
|---|---|---|---|
| **Anthropic** | `claude-sonnet-4-6` | $3 / 1M input tok, $15 / 1M output tok (≈ $0.003/кадр 1024×768) | Лучшее качество структурного анализа, дефолт |
| **OpenAI** | `gpt-4o` / `gpt-4o-mini` | $2.50 / 1M in (`4o`), $0.15 / 1M in (`4o-mini`) | `gpt-4o-mini` — самый дешёвый fallback |
| **Google Gemini** | `gemini-2.5-pro` / `gemini-2.5-flash` | $1.25 / 1M in (`pro`), $0.075 / 1M in (`flash`) | Альтернативный биллинг |

**Рекомендация:** три провайдера. Дефолтная цепочка `anthropic_claude → openai_gpt4o → google_gemini`. Выбор по приоритету активных ключей; при `429` / `5xx` — автоматический fallback на следующий. Не-дефолтная модель задаётся в payload (`provider: "openai_gpt4o_mini"` и т.п.).

### ✅ 2.7 Хранение и управление AI-ключами

**Схема таблицы `api_keys` (SQLite, отдельная БД `keys.db` на persistent volume):**

```sql
CREATE TABLE api_keys (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  provider       TEXT NOT NULL,        -- 'assemblyai' | 'deepgram' | 'openai_whisper'
                                       -- | 'groq_whisper' | 'anthropic_claude'
                                       -- | 'openai_gpt4o' | 'openai_gpt4o_mini'
                                       -- | 'google_gemini_pro' | 'google_gemini_flash'
  kind           TEXT NOT NULL,        -- 'transcription' | 'vision'
  label          TEXT,                 -- человекочитаемая метка ("assemblyai-main")
  secret_enc     BLOB NOT NULL,        -- зашифрованный ключ (Fernet, master key в env)
  is_active      INTEGER NOT NULL DEFAULT 1,
  priority       INTEGER NOT NULL DEFAULT 100,  -- меньше = выше приоритет в цепочке fallback
  monthly_limit_usd  REAL,             -- опциональный лимит, сервис отключит ключ при превышении
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  last_used_at   TEXT
);

CREATE TABLE api_key_usage (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  key_id         INTEGER NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
  job_id         TEXT NOT NULL,
  ts             TEXT NOT NULL,        -- ISO timestamp начала вызова
  operation      TEXT NOT NULL,        -- 'transcribe' | 'vision_analyze'
  model          TEXT NOT NULL,        -- фактически вызванная модель
  input_tokens   INTEGER,              -- для LLM-vision
  output_tokens  INTEGER,
  audio_seconds  REAL,                 -- для транскрипции
  frames         INTEGER,              -- для vision
  latency_ms     INTEGER,
  status         TEXT NOT NULL,        -- 'ok' | 'error' | 'rate_limited'
  error          TEXT,
  cost_usd       REAL NOT NULL DEFAULT 0
);

CREATE INDEX idx_usage_key_ts ON api_key_usage(key_id, ts);
```

**Шифрование:** `secret_enc` — симметрично зашифрованный ключ через Fernet. Master-key берётся из env `PROCESSOR_KEY_ENCRYPTION_KEY`. Если env не задан — сервис не стартует. Ключ никогда не возвращается в open form через admin API (только маскированно: `sk-ant-***abcd`).

**Персистентность:** `keys.db` лежит в отдельном volume `./data/processor-db/` (bind mount), не в образе и не в `MEDIA_DIR`. Пересборка контейнера сохраняет ключи и статистику.

**Admin API** (авторизация — отдельный `X-Admin-Token` из env `PROCESSOR_ADMIN_TOKEN`, чтобы worker-токен не давал права управлять ключами):

```
POST   /admin/api-keys
  body: { provider, kind, label, secret, priority?, monthly_limit_usd? }
  → 201 { id, provider, kind, label, secret_masked, priority, is_active, ... }

GET    /admin/api-keys
  → [{ id, provider, kind, label, secret_masked, priority, is_active,
       last_used_at, usage_30d: { calls, cost_usd, errors } }, ...]

GET    /admin/api-keys/{id}
  → { ... + usage_30d_breakdown: [{ day, calls, cost_usd }, ...] }

PATCH  /admin/api-keys/{id}
  body: { label?, priority?, is_active?, monthly_limit_usd?, secret? }
  → 200

DELETE /admin/api-keys/{id}
  → 204

POST   /admin/api-keys/{id}/test
  → 200 { ok: true, latency_ms, model }   # пробный вызов провайдера

GET    /admin/usage?from=...&to=...&provider=...&kind=...
  → {
      total: { calls, cost_usd, errors },
      by_provider: [{ provider, calls, cost_usd, errors, avg_latency_ms }, ...],
      by_day: [{ day, calls, cost_usd }, ...],
      top_jobs: [{ job_id, cost_usd, duration_ms }, ...]
    }

GET    /admin/usage/export?format=csv&from=...&to=...
  → text/csv
```

**Логика выбора ключа для job-а:**

1. Job приходит с опциональным `provider` в payload. Если указан — ищется активный ключ именно этого провайдера.
2. Если `provider` не указан — берётся цепочка по `kind` (`transcription` / `vision`), отсортированная по `priority` ASC, только `is_active=1`.
3. При `429` / `5xx` / сетевой ошибке — автоматический fallback на следующий ключ той же цепочки; все попытки логируются в `api_key_usage`.
4. Перед вызовом проверяется `monthly_limit_usd`: суммарный `cost_usd` по ключу за текущий календарный месяц. При превышении — ключ временно пропускается и в ответе админки помечается как `limit_exceeded`.
5. После успешного вызова — апдейт `last_used_at` и запись в `api_key_usage` с реальным `cost_usd` (считается по прайсу модели, таблица-константа в коде).

**Где берём прайсы для `cost_usd`:** жёстко прописаны в `pricing.py` как dict `{provider → {model → {input_per_1m, output_per_1m, audio_per_hour}}}`. Таблица обновляется вручную в PR при изменении тарифов.

### ✅ 2.8 Тестовый веб-интерфейс

Встроенный в processor минимальный UI для ручного тестирования всех фич. Цель — dev/QA-цикл без curl-ов и Postman. В prod можно выключить через env `TEST_UI_ENABLED=false`.

**Путь:** `GET /ui/` (обслуживается самим FastAPI через `StaticFiles`). Авторизация — базовая HTTP (Basic Auth) с теми же токенами (`X-Admin-Token` как пароль, логин любой), чтобы не городить сессии.

**Что умеет UI:**

1. **Вкладка «Files»** — список содержимого `MEDIA_DIR/downloads/`. Можно:
   - Выбрать существующий файл для теста (клик → автоподстановка `file_path` во все формы)
   - Загрузить свой файл через `POST /admin/files/upload` (multipart, записывает в `downloads/`). Только для тестов.
   - Удалить файл (`DELETE /admin/files/{name}`)

2. **Вкладка «Transcribe»** — форма:
   - выбор `file_path` (из списка)
   - dropdown `provider` (`auto` + список активных ключей)
   - `language` (auto/ru/en/…)
   - кнопка «Run» → `POST /jobs/transcribe` → поллинг `GET /jobs/{id}` каждые 1 с
   - результат: текст транскрипции, длительность, cost_usd, latency, использованный ключ

3. **Вкладка «Frames»** — форма:
   - `file_path`
   - sliders для `fps`, `diff_threshold`, `min_frames`, `max_frames`
   - кнопка «Extract» → `POST /jobs/extract-frames` → после готовности показывает:
     - Галерея извлечённых кадров с подписью `timestamp_sec / diff_ratio`
     - Гистограмма diff_ratio по всем сырым кадрам (видно пороговую линию)
     - Stats: `raw_count → kept_count (dropped X)`
   - Помогает подбирать `diff_threshold` под конкретный тип контента

4. **Вкладка «Vision»** — форма:
   - `file_path`
   - sampling-параметры (как в Frames)
   - dropdown `provider` (все 6 моделей vision)
   - dropdown `prompt_template`
   - кнопка «Analyze» → `POST /jobs/vision-analyze`
   - результат: JSON-viewer структурированного vision-ответа, стоимость, использованный ключ, галерея кадров, которые пошли в vision

5. **Вкладка «Full analysis»** — объединённая форма (file + sampling + transcribe provider + vision provider), запускает `POST /jobs/full-analysis`, показывает оба блока результата и общий `cost_usd`.

6. **Вкладка «Keys»** — табличный CRUD над `/admin/api-keys`:
   - список всех ключей с маскированием, провайдером, `is_active`, `priority`, `monthly_limit_usd`, `last_used_at`, `usage_30d.cost_usd`
   - форма добавления нового ключа (provider dropdown + secret input + label + priority + лимит)
   - кнопка «Test» на каждом ключе → `POST /admin/api-keys/{id}/test`, показывает latency и любой error
   - toggle `is_active`, edit `priority`/`monthly_limit_usd`, delete

7. **Вкладка «Usage»** — дашборд `/admin/usage`:
   - фильтры: дата-диапазон, provider, kind
   - таблица «Total / By provider / By day»
   - кнопка «Export CSV» → `GET /admin/usage/export`

8. **Вкладка «Jobs»** — история последних 50 job-ов из `jobs.db` с фильтром по статусу; клик на job → модалка с полным payload и result.

**Технически:**
- Single-page приложение на ванильном JS/HTML + один CSS-файл или Alpine.js — никакого React/Vue, чтобы не тянуть билд-пайплайн в processor
- Статика в `video_processor/ui/static/` (index.html, app.js, styles.css), монтируется `app.mount("/ui", StaticFiles(directory="ui/static", html=True))`
- Все вызовы — к тем же REST endpoints, что уже описаны (`/jobs/*`, `/admin/*`), никаких отдельных API для UI. UI = тонкий клиент.
- Один dependency из новых: ничего. Всё в браузере.

**Что UI НЕ делает:**
- Не заменяет prod админку (она будет отдельно на стороне основного backend)
- Не имеет ролей / мульти-пользователей — один токен на всех, защита только от случайного доступа по сети
- Не хранит состояние между перезагрузками (никакого localStorage кроме токена для Basic Auth)

### ✅ 2.4 Извлечение кадров — per-second + OpenCV dedup

Фиксированное число кадров убрано. Вместо этого — **адаптивный алгоритм на основе изменений сцены**:

1. **Sampling:** ffmpeg извлекает по одному кадру в секунду (`-vf fps=1`). Для 10-секундного ролика — 10 сырых кадров, для 60-секундного — 60.
2. **Dedup через OpenCV:** каждый следующий кадр сравнивается с **последним сохранённым** (не с предыдущим сырым, иначе плавные переходы накапливаются). Если разница **< 10%** — кадр считается повторным и удаляется.
3. **Метрика сравнения:** `cv2.absdiff` между кадрами в grayscale, нормализованный по суммарной яркости: `diff_ratio = sum(absdiff) / (255 * width * height)`. Порог по умолчанию `0.10`.
4. **На выходе:** список «опорных» кадров, где каждый достаточно отличается от предыдущего. Для статичного ролика (talking head) останется 2–3 кадра; для динамичного монтажа — 20–40.
5. **Верхний лимит:** `max_frames` (дефолт 40) — защита от очень длинных или очень динамичных видео; если лимит достигнут, остаток просто пропускается.
6. **Нижний лимит:** `min_frames` (дефолт 3) — если после дедупа осталось меньше, добирается равномерно из отброшенных.

**Почему так, а не fps=N/duration:**
- Талкинг-хэд ролик на 60 с при `fps=8/60` даст 8 почти одинаковых кадров — дорого и бесполезно для vision. Per-second + dedup даст 2–3 уникальных.
- Быстрый монтаж (TikTok 15 с с 10 сменами сцен) при `fps=8/15` = 8 кадров — может пропустить половину сцен. Per-second даст 15 кадров, dedup оставит ~10 уникальных.
- Алгоритм самоадаптируется под контент, не нужно гадать про «адаптивный режим по длительности».

**Почему OpenCV, а не ffmpeg `-vf select='gt(scene,0.1)'`:**
- Явный и тестируемый threshold
- Возможность сохранять вспомогательные структуры (diff_ratio в метаданных кадра — передаётся в vision-промпт: «кадр 5 — сильная смена сцены, diff 0.42»)
- OpenCV уже нужен для будущих фич (детекция лиц, blur check) — одна зависимость

**Параметризация через payload:**
```json
{
  "file_path": "...",
  "sampling": {
    "fps": 1,              // кадров в секунду для сырого sampling
    "diff_threshold": 0.10, // порог dedup
    "min_frames": 3,
    "max_frames": 40
  }
}
```
Все поля опциональные, дефолты выше.

### ✅ 2.5 Источник файла — решено

Processor принимает **только `file_path`** (локальный путь в shared volume). URL, скачивание, выбор стратегии — вне зоны ответственности. Backend сам оркеструет: сначала downloader, потом processor.

### ❓ 2.6 Кеширование результатов

Vision-анализ дорогой. Если backend заново попросит анализ того же видео — пересчитывать или кешировать?

- **Кеш в БД processor** (по sha256 файла или по внешнему ключу из payload) — повторный запрос моментально
- **Без кеша** — backend сам решает, когда дёргать

**Рекомендация:** кешировать transcript и vision по `cache_key` (опциональное поле в payload, например `"{platform}:{external_id}"`). Если не передан — fallback на sha256 файла. Processor не парсит URL и не знает про платформы, ключ формирует вызывающая сторона. TTL 30 дней.

---

## 3. Архитектура

```
┌─────────────────────┐    HTTP     ┌──────────────────────────┐
│  backend / caller   │ ──────────► │  video-processor         │
│  (оркеструет        │  POST /jobs │  (FastAPI + ffmpeg)      │
│   скачивание сам)   │  GET  /jobs │                          │
│                     │ ◄────────── │  jobs.db   (SQLite)      │
└──────────┬──────────┘             │  cache.db  (SQLite)      │
           │                        │  keys.db   (SQLite)      │◄── persistent
           │                        └────┬───────────┬─────────┘    volume
           │                             │           │
┌──────────┴──────────┐                  │           │  HTTPS + ключ из keys.db
│  admin UI / curl    │                  │           ▼
│  X-Admin-Token      │                  │   ┌────────────────────────────┐
└──────────┬──────────┘                  │   │  Внешние AI провайдеры     │
           │                             │   │  ─ AssemblyAI              │
           │   HTTP /admin/api-keys      │   │  ─ Deepgram                │
           └────────────────────────────►┤   │  ─ OpenAI (Whisper+GPT-4o) │
                                         │   │  ─ Groq Whisper            │
           shared volume                 │   │  ─ Anthropic Claude        │
           ./data/media/   ◄─────────────┘   │  ─ Google Gemini           │
             downloads/    ◄── кладёт caller └────────────────────────────┘
             audio/        ◄── processor пишет
             frames/       ◄── processor пишет
             transcripts/  ◄── processor пишет
```

Processor не делает исходящих HTTP-вызовов для получения файла. Файл должен уже лежать в `MEDIA_DIR` на момент вызова job. Все исходящие HTTP — только к AI-провайдерам, с ключами, зачитанными из `keys.db` на лету (без кеша в памяти дольше чем на длительность одного job).

**Volumes:**
- `./data/media/` — shared с другими сервисами, для видео/аудио/кадров/транскриптов
- `./data/processor-db/` — только processor, содержит `jobs.db`, `cache.db`, `keys.db`. Пересборка образа эти БД не трогает.

**API:**

Все job-endpoints принимают **только `file_path`** — локальный путь в shared volume. Оркестрация скачивания — вне контракта processor.

```
POST /jobs/transcribe
  body: {
    file_path: "/media/downloads/test_fixture.mp4",   # обязательное
    cache_key?: "youtube:abc123",                      # опционально, для кеша
    language?: "ru|en|auto",
    provider?: "auto|assemblyai|deepgram|openai_whisper|groq_whisper"
  }
  → 202 { job_id }

POST /jobs/extract-frames
  body: {
    file_path,
    sampling?: {
      fps: 1,                 # сырых кадров в секунду (default 1)
      diff_threshold: 0.10,   # порог dedup в долях (default 0.10)
      min_frames: 3,
      max_frames: 40
    }
  }
  → 202 { job_id }

POST /jobs/vision-analyze
  body: {
    file_path,
    cache_key?,
    sampling?,                # те же поля, что в /jobs/extract-frames
    prompt_template?: "default|detailed|hooks_focused",
    provider?: "auto|anthropic_claude|openai_gpt4o|openai_gpt4o_mini
                |google_gemini_pro|google_gemini_flash"
  }
  → 202 { job_id }

POST /jobs/full-analysis
  body: {
    file_path,
    cache_key?,
    sampling?,
    source_ref?: { platform, external_id },             # v2
    prompt_version?: "v1|v2|...",                       # v2
    analysis_profile?: "quick|standard|deep",            # v2
    providers?: { transcription?, vision? }              # v2
  }
  → 202 { job_id }
  # выполняет: transcribe (parallel) + extract_frames → vision_analyze

GET /jobs/{job_id}
  → {
      status: queued|running|done|failed,
      parent_job_id?: "...",                             # v2, для reanalyze
      reanalysis_of?: "...",                             # v2, для reanalyze
      result?: {
        analysis_version: "2.0",                          # v2
        prompt_version: "vision_default_v1",             # v2
        source_ref?: { platform, external_id },          # v2 echo
        artifacts: {                                      # v2
          audio_path?: "/media/audio/{job_id}.mp3",
          frames_dir?: "/media/frames/{job_id}/",
          transcript_path?: "/media/transcripts/{job_id}.json",
          vision_result_path?: "/media/vision/{job_id}.json"
        },
        transcript?: { text, language, provider, model, duration_sec },
        frames?: {
          extracted: [{ index, timestamp_sec, file_path, diff_ratio }],
          stats: { raw_count, kept_count, dropped_count, duration_sec }
        },
        vision?: { provider, model, hook, structure, scenes: [...],
                   why_viral, emotion_trigger },
        cost_usd: { transcription: 0.012, vision: 0.024, total: 0.036 }
      },
      error?, started_at, finished_at
    }

POST /jobs/reanalyze                                      # v2
  body: {
    base_job_id: "...",
    override?: {
      vision_model?, transcription_model?,
      prompt_version?, analysis_profile?, sampling?
    }
  }
  → 202 { job_id }   # новый job с reanalysis_of = base_job_id

DELETE /cache/{cache_key}
  → 204   # принудительная инвалидация кеша

# Prompts Registry (v2) — A3.10
GET    /admin/prompts
  → [{ name, version, is_active, created_at }, ...]

GET    /admin/prompts/{name}
  → [{ version, body, is_active, metadata }, ...]

GET    /admin/prompts/{name}/{version}
  → { name, version, body, is_active, metadata, created_at }

POST   /admin/prompts
  body: { name, version, body, metadata? }
  → 201 { ... }

PATCH  /admin/prompts/{name}/activate/{version}
  → 200 { name, version, is_active: true }

DELETE /admin/prompts/{name}/{version}
  → 204   # нельзя удалить активную версию

GET /healthz
  → { status, ffmpeg_version, disk_free_gb,
      queue_depth, active_jobs, cache_size,
      active_keys: { transcription: 2, vision: 2 } }
```

**Ошибки:**
- `file_path` не существует или находится вне `MEDIA_DIR` → `400 file_not_found` сразу, без постановки job в очередь
- Нет ни одного активного ключа нужного `kind` → `503 no_provider_available` при постановке job

Авторизация: header `X-Worker-Token` (env `PROCESSOR_TOKEN`).

---

## 4. Структура

```
video_processor/
├── Dockerfile               # python:3.11-slim + ffmpeg. НЕТ faster-whisper, НЕТ моделей.
│                            # Образ ~300 MB.
├── requirements.txt         # fastapi, ffmpeg-python, opencv-python-headless, numpy,
│                            # httpx, sqlalchemy, structlog, pillow,
│                            # cryptography (Fernet для шифрования ключей),
│                            # anthropic, openai, google-genai, assemblyai, deepgram-sdk, groq
├── main.py                  # FastAPI app, lifespan (init БД, bootstrap ключей из env)
├── config.py                # env: PROCESSOR_TOKEN, PROCESSOR_ADMIN_TOKEN,
│                            #      PROCESSOR_KEY_ENCRYPTION_KEY, MEDIA_DIR, DB_DIR,
│                            #      BOOTSTRAP_* (см. §10)
├── jobs/
│   ├── store.py             # SQLite jobs table (в DB_DIR/jobs.db)
│   ├── queue.py             # asyncio.Queue + worker
│   └── router.py            # /jobs endpoints
├── cache/
│   ├── store.py             # SQLite cache (DB_DIR/cache.db): cache_key, type,
│   │                        # result_json, expires_at
│   └── router.py            # DELETE /cache endpoints
├── keys/
│   ├── store.py             # SQLite api_keys + api_key_usage (DB_DIR/keys.db)
│   ├── crypto.py            # Fernet wrap/unwrap с master key из env
│   ├── resolver.py          # выбор ключа для job (цепочка fallback, лимиты)
│   ├── pricing.py           # константы цен по моделям
│   ├── bootstrap.py         # первичное наполнение keys.db из env BOOTSTRAP_*
│   └── router.py            # /admin/api-keys, /admin/usage endpoints
├── tasks/
│   ├── extract_audio.py     # ffmpeg → 16kHz mono mp3 в /media/audio/
│   ├── transcribe.py        # через keys.resolver → внешний провайдер
│   ├── extract_frames.py    # ffmpeg fps=1 → OpenCV dedup (§2.4) → /media/frames/{job_id}/
│   ├── vision_analyze.py    # кадры → через keys.resolver → внешний провайдер
│   └── full_analysis.py     # оркестратор: transcribe ∥ (frames → vision)
├── clients/
│   ├── assemblyai.py
│   ├── deepgram.py
│   ├── openai_whisper.py
│   ├── groq_whisper.py
│   ├── anthropic_claude.py
│   ├── openai_gpt4o.py
│   └── google_gemini.py
├── prompts/
│   ├── vision_default.py
│   ├── vision_detailed.py
│   └── vision_hooks.py
├── ui/                       # тестовый веб-интерфейс (§2.8)
│   ├── static/
│   │   ├── index.html        # SPA: tabs Files/Transcribe/Frames/Vision/Full/Keys/Usage/Jobs
│   │   ├── app.js            # ванильный JS, fetch к тем же REST endpoints
│   │   └── styles.css
│   └── router.py             # POST /admin/files/upload, DELETE /admin/files/{name},
│                             # GET /admin/files (список)
└── tests/
    ├── test_extract_audio.py
    ├── test_extract_frames.py
    ├── test_keys_crypto.py
    ├── test_keys_resolver.py
    ├── test_admin_api.py
    ├── test_bootstrap.py
    ├── test_transcribe.py
    ├── test_vision.py
    └── test_ui_routes.py     # smoke: index.html отдаётся, upload/delete работают
```

---

## 5. План реализации

### Этап 1 — Скелет (0.5 дня)
- [ ] `Dockerfile`: python:3.11-slim + ffmpeg. Без ML-моделей. Multi-stage, финальный ~300 MB
- [ ] FastAPI app, `/healthz`, заглушки `/jobs/*` и `/admin/*`
- [ ] Сервис в общем `docker-compose.yml` c двумя volumes: `./data/media/` и `./data/processor-db/`
- [ ] Smoke: контейнер стартует, `/healthz` отвечает

### Этап 2 — Job store + queue + auth (0.5 дня)
- [ ] SQLite `jobs` в `DB_DIR/jobs.db`
- [ ] `asyncio.Queue` + worker, отдельные лимиты: `MAX_CONCURRENT_TRANSCRIBE`, `MAX_CONCURRENT_VISION`
- [ ] Middleware `X-Worker-Token` для `/jobs/*`, `X-Admin-Token` для `/admin/*`
- [ ] Cache store: `DB_DIR/cache.db` с `cache(cache_key, type, result_json, expires_at)`

### Этап 3 — Keys store + admin API + bootstrap (1 день)
- [ ] `keys/crypto.py`: Fernet wrap/unwrap, master-key из env (fail-fast при отсутствии)
- [ ] `keys/store.py`: `api_keys` + `api_key_usage` в `DB_DIR/keys.db`
- [ ] `keys/pricing.py`: таблица цен по всем моделям (9 штук из §2.2 и §2.3)
- [ ] `keys/resolver.py`: выбор ключа для `(kind, provider?)`, учёт `priority`, `is_active`, `monthly_limit_usd`
- [ ] `keys/bootstrap.py`: при старте — если `keys.db` пустая и есть `BOOTSTRAP_*` env → создать ключи (см. §10)
- [ ] `keys/router.py`: CRUD `/admin/api-keys`, `POST /admin/api-keys/{id}/test`, `GET /admin/usage`, `GET /admin/usage/export`
- [ ] Тесты: шифрование, резолвер (fallback chain, лимиты), bootstrap (идемпотентность)

### Этап 4 — Audio + Transcribe (1 день)
- [ ] `tasks/extract_audio.py`: ffmpeg → 16kHz mono mp3 в `media/audio/{job_id}.mp3`
- [ ] Клиенты: `assemblyai.py`, `deepgram.py`, `openai_whisper.py`, `groq_whisper.py`. Каждый — функция `transcribe(audio_path, api_key, language) → TranscriptResult`
- [ ] `tasks/transcribe.py`: резолвер → клиент → запись usage в `api_key_usage` → при `429/5xx` fallback на следующий ключ
- [ ] Запись результата в `media/transcripts/{job_id}.json`
- [ ] Кеширование по `cache_key` (если передан в payload)
- [ ] Тесты на 10-секундном тестовом файле (моки для провайдеров)

### Этап 5 — Extract frames (1 день)
- [ ] `tasks/extract_frames.py`:
  - Шаг 1: ffmpeg `-vf fps={sampling.fps}` (дефолт 1) → временная папка с сырыми кадрами
  - Шаг 2: OpenCV проход по кадрам в порядке возрастания timestamp:
    - grayscale + `cv2.absdiff` с последним сохранённым кадром
    - `diff_ratio = absdiff.sum() / (255 * w * h)`
    - если `diff_ratio >= diff_threshold` → сохраняем, обновляем «последний сохранённый»
    - если `<` → удаляем сырой файл
  - Шаг 3: пост-обработка min/max лимитов
    - если `kept < min_frames` → добить из отброшенных равномерно по timestamp
    - если `kept > max_frames` → прорядить равномерно
- [ ] Запись в `media/frames/{job_id}/frame_001.jpg` с суффиксом `_sceneXX` для читаемости
- [ ] JPEG quality 85, max-width 1280 (ресайз делается в OpenCV до сравнения — ускоряет diff)
- [ ] Возвращать `FramesResult { extracted: [{index, timestamp_sec, file_path, diff_ratio}], stats: {raw_count, kept_count, dropped_count, duration_sec} }`
- [ ] Тесты:
  - статичное видео (talking head) → ≤ 5 кадров
  - быстрый монтаж → 10–20 кадров
  - чёрный экран целиком → `min_frames` кадров (все diff_ratio ≈ 0)
  - проверка `max_frames` ограничения

### Этап 6 — Vision analyze (1 день)
- [ ] Клиенты: `anthropic_claude.py`, `openai_gpt4o.py`, `google_gemini.py`. Каждый — `analyze(frames, api_key, prompt, model) → VisionResult + UsageStats`
- [ ] `prompts/vision_default.py`: промпт «найди hook, structure, scenes, why_viral, emotion_trigger»
- [ ] `tasks/vision_analyze.py`: резолвер → клиент → парсинг structured output → запись usage
- [ ] Fallback chain при ошибках провайдера
- [ ] Кеширование по `cache_key`

### Этап 7 — Full analysis оркестратор (0.5 дня)
- [ ] `tasks/full_analysis.py`:
  1. Валидация `file_path` (существует, внутри `MEDIA_DIR`)
  2. extract_audio + transcribe — параллельно с (3)
  3. extract_frames → vision_analyze
  4. Объединить результат, посчитать общий cost
- [ ] Прогнать end-to-end на тестовом локальном файле

### Этап 8 — Тестовый веб-интерфейс (1 день)
- [ ] `ui/router.py`: `GET /admin/files`, `POST /admin/files/upload` (multipart → `MEDIA_DIR/downloads/`), `DELETE /admin/files/{name}`
- [ ] `ui/static/index.html`: SPA на ванильном JS с табами Files / Transcribe / Frames / Vision / Full / Keys / Usage / Jobs
- [ ] `ui/static/app.js`:
  - fetch-обёртка с автоматической подстановкой `X-Admin-Token` (Basic Auth или header из localStorage)
  - поллинг job-ов каждые 1 с до `status in (done, failed)`
  - json-viewer для результатов, галерея кадров, гистограмма `diff_ratio` (canvas)
- [ ] Монтирование: `app.mount("/ui", StaticFiles(directory="ui/static", html=True))`
- [ ] Env `TEST_UI_ENABLED` (default `true` в dev, `false` в prod-конфиге)
- [ ] Smoke-тесты: `GET /ui/` → 200, `GET /admin/files` → список, upload/delete реального файла
- [ ] Прогнать вручную полный цикл: upload → extract_frames с разными `diff_threshold` → vision_analyze → full_analysis. Итог: все провайдеры из bootstrap работают end-to-end через UI.

### Этап 9 — Contract v2 (0.5 дня) ✅ добавлено для A3
> Расширение API без ломающих изменений. Подготовка к стыковке с отдельным сервисом `analyzer` (A3.6/A3.7/A3.8).
- [ ] `schemas/result_v2.py`: pydantic-модели `AnalysisResultV2`, `Artifacts`, `SourceRef`
- [ ] [tasks/full_analysis.py](../Modules/processor/tasks/full_analysis.py): сериализация `vision`-блока в `/media/vision/{job_id}.json` + возврат пути в `result.artifacts.vision_result_path`
- [ ] [tasks/transcribe.py](../Modules/processor/tasks/transcribe.py): добавить `transcript_path` в результат
- [ ] [tasks/vision_analyze.py](../Modules/processor/tasks/vision_analyze.py): добавить `frames_dir`, `vision_result_path` в результат
- [ ] [jobs/router.py](../Modules/processor/jobs/router.py): новые опциональные поля во всех `*Req`:
  - `source_ref: {platform, external_id}` — opaque, идёт в cache_key
  - `prompt_version: str` — явная версия промпта, идёт в cache_key
  - `analysis_profile: "quick"|"standard"|"deep"` — пресет sampling+prompt (опционально)
  - `providers: {transcription?, vision?}` — раздельный выбор (обратная совместимость с плоским `provider` сохраняется)
- [ ] `analysis_version: "2.0"` проставляется в result всеми tasks-оркестраторами
- [ ] [cache/store.py](../Modules/processor/cache/store.py): новый хелпер `build_cache_key(base, prompt_version, model)` — учитывает все версии
- [ ] Обратная совместимость: запросы без новых полей работают без изменений
- [ ] Smoke-тест v2: все существующие тесты зелёные + новый `test_v2_contract.py` (artifacts в ответе, vision_result_path на диске)

### Этап 10 — Prompts Registry v2 (1 день)
> Полноценный A3.10: версионирование, A/B, CRUD, миграция хардкодов.
- [ ] `prompts/store.py`: SQLite-хранилище в `DB_DIR/prompts.db`
  ```sql
  CREATE TABLE prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,              -- 'vision_default' | 'vision_detailed' | 'vision_hooks'
    version TEXT NOT NULL,           -- 'v1' | 'v2' | ...
    body TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0,  -- 1 = дефолтный для имени
    metadata_json TEXT,              -- свободный dict: author, ab_group, notes
    created_at TEXT NOT NULL,
    UNIQUE(name, version)
  );
  ```
- [ ] Миграция: при первом старте, если таблица пуста, заливает три текущих файла [prompts/vision_default.py](../Modules/processor/prompts/vision_default.py), [vision_detailed.py](../Modules/processor/prompts/vision_detailed.py), [vision_hooks.py](../Modules/processor/prompts/vision_hooks.py) как `v1` с `is_active=1`.
- [ ] Обновить `prompts/__init__.py: get_prompt(template, version=None) → PromptRecord` — чтение из БД с fallback на встроенные константы (если БД недоступна)
- [ ] `prompts/router.py`: admin CRUD
  ```
  GET    /admin/prompts                         → список всех (name, version, is_active)
  GET    /admin/prompts/{name}                  → все версии одного имени
  GET    /admin/prompts/{name}/{version}        → тело промпта
  POST   /admin/prompts                         → создать новую версию {name, version, body, metadata}
  PATCH  /admin/prompts/{name}/activate/{version} → переключить is_active
  DELETE /admin/prompts/{name}/{version}        → удалить версию (кроме активной)
  ```
- [ ] [tasks/vision_analyze.py](../Modules/processor/tasks/vision_analyze.py): использует новый `get_prompt(template, version)` и прописывает фактическую `prompt_version` в результат
- [ ] Включение `prompt_version` в `cache_key` (через build_cache_key из этапа 9)
- [ ] UI: новая вкладка «Prompts» в [ui/static/index.html](../Modules/processor/ui/static/index.html) — CRUD над промптами, preview body, toggle active
- [ ] Тесты: `test_prompts_store.py` (миграция, CRUD, активация), `test_prompts_cache_invalidation.py` (смена prompt_version → новый cache_key)

### Этап 11 — Re-analyze Service v2 (0.5 дня)
> Полноценный A3.11: новый job на другой модели/промпте с сохранением истории.
- [ ] Миграция [jobs/store.py](../Modules/processor/jobs/store.py): `ALTER TABLE jobs ADD COLUMN parent_job_id TEXT`, `ADD COLUMN reanalysis_of TEXT` (оба nullable, индекс по `reanalysis_of`)
- [ ] `tasks/reanalyze.py`: новый handler, который:
  1. Читает исходный job по `base_job_id`
  2. Берёт его `payload` + `file_path` + `source_ref`
  3. Применяет `override: {vision_model?, transcription_model?, prompt_version?, analysis_profile?}`
  4. Запускает полный цикл как обычный `full_analysis` (но с `parent_job_id` = base_job_id, `reanalysis_of` = base_job_id)
  5. Новый job сохраняется как отдельная запись, не трогая исходный
- [ ] Эндпоинт `POST /jobs/reanalyze` в [jobs/router.py](../Modules/processor/jobs/router.py): `{base_job_id, override}`, возвращает `{job_id}` нового job-а
- [ ] `GET /jobs/{id}` возвращает `reanalysis_of` и `parent_job_id` в ответе если они заполнены
- [ ] UI: в табе «Jobs» — кнопка «Re-analyze» рядом с каждым завершённым vision/full job-ом, модалка с выбором override
- [ ] Тесты: `test_reanalyze.py` — два прогона с разными `prompt_version`, оба job-а сохранены, связь через `reanalysis_of` установлена, новый job не ломает кеш исходного

### Этап 12 — Backend integration (0.5 дня)
- [ ] `backend/clients/processor_client.py`: httpx-обёртка с поллингом
- [ ] Оркестрация на стороне backend: сначала вызов downloader, затем processor с полученным `file_path`
- [ ] Заменить [backend/transcriber.py](backend/transcriber.py) на тонкую обёртку, либо удалить
- [ ] Конфиг: `PROCESSOR_URL`, `PROCESSOR_TOKEN` в `.env`
- [ ] [routers/videos.py](backend/routers/videos.py) `POST /api/videos/{id}/analyze` — дёргает processor full-analysis с локальным путём
- [ ] [routers/analyze.py](backend/routers/analyze.py) `POST /api/analyze-url` — сначала downloader, потом processor

**Итого: ~8.5 дней** (этапы 1–8 готовы, 9–11 — контракт v2 / Prompts / Re-analyze ≈ 2 дня, этап 12 — backend-интеграция)

---

## 6. Что НЕ делаем

- ❌ Скачивание, парсинг URL, работа с источниками — processor не знает про них вообще
- ❌ Знание о платформах (YouTube/TikTok/Instagram) — только опциональный непрозрачный `cache_key`
- ❌ HTTP-клиенты к другим сервисам (downloader и т.п.)
- ❌ **Локальные AI-модели любого рода** (faster-whisper, llama.cpp, локальный CLIP и т.д.) — только внешние API
- ❌ GPU-инференс — образ полностью CPU-only, тяжёлая работа — на стороне провайдера
- ❌ Diarization (кто говорит) — не нужно для коротких роликов
- ❌ Перекодирование / нарезка клипов
- ❌ Полноценный prod-админ UI с ролями/сессиями/i18n — только минимальный тестовый UI (§2.8) для dev/QA, который можно выключить в prod через `TEST_UI_ENABLED=false`. Настоящая админка делается на стороне основного backend

---

## 7. Риски

| Риск | Митигация |
|---|---|
| **Провайдер недоступен (429/5xx)** | Fallback chain по `priority`; все попытки логируются в `api_key_usage` с `status='rate_limited'` или `'error'` |
| **Стоимость транскрипции + vision растёт незаметно** | `cost_usd` в каждом job; `monthly_limit_usd` на ключ (ключ авто-деактивируется); дашборд `/admin/usage` |
| **Утечка ключей** | Ключи шифруются Fernet в БД; master key только в env; через API отдаются маскированными (`sk-ant-***abcd`); HTTPS обязателен в prod |
| **Потеря БД при пересборке контейнера** | `DB_DIR` = bind mount на хост-директорию `./data/processor-db/`, никогда не в образе |
| **Файл не появился в volume к моменту вызова** | Валидация `file_path` в начале job → `400 file_not_found` до постановки в очередь |
| **Нет активных ключей** | `/jobs/*` возвращает `503 no_provider_available` без постановки в очередь; `/healthz` показывает `active_keys` |
| **Кеш разрастается** | TTL 30 дней + ежедневная очистка; лимит 10000 записей |
| **Vision на почти одинаковых кадрах (talking head)** | OpenCV dedup (§2.4) выкидывает повторы; порог `diff_threshold` настраивается через payload; тестовый UI (§2.8) даёт визуализацию для подбора порога |
| **Тестовый UI открыт наружу в prod** | Env `TEST_UI_ENABLED=false` полностью отключает монтирование `/ui` и `/admin/files/*`; Basic Auth как минимальная защита даже в dev |
| **Bootstrap не идемпотентен — дубли ключей при рестарте** | `keys/bootstrap.py` запускается только если `keys.db` не содержит ни одного ключа с `label` из ожидаемого набора |

---

## 8. Открытые вопросы

1. **Скоуп** (см. 2.1) — все из A–G или подмножество?
2. **Кеш TTL 30 дней** — ок или иначе?
3. **Что делать с [backend/transcriber.py](backend/transcriber.py)** — удалить полностью после миграции, или оставить как fallback?
4. **Нужны ли ещё провайдеры** кроме перечисленных 9 (Rev.ai? Azure Speech? Cohere Vision?) — можно добавить позже без изменения схемы.
5. **Per-user квоты** — сейчас лимит только `monthly_limit_usd` на ключ. Нужны ли лимиты на уровне вызывающего (`X-Worker-Token` → user_id → лимит)?
6. **Default `diff_threshold` для OpenCV dedup** — 0.10 оптимальный? Возможно подобрать по данным: на батче из разных роликов замерить, сколько кадров остаётся при 0.05 / 0.10 / 0.15.

---

## 9. Инфраструктура

Processor — самодостаточный контейнер. Зависимости:
- `./data/media/` — shared volume (read для `downloads/`, write для `audio/`, `frames/`, `transcripts/`)
- `./data/processor-db/` — persistent volume только для processor (`jobs.db`, `cache.db`, `keys.db`)
- Внешние HTTPS к AI-провайдерам

Паттерны (jobs store, auth middleware, structlog, healthz) — те же, что в любом подобном сервисе. Если в проекте есть другие похожие контейнеры (например, video-downloader), общий код можно вынести в `shared/` Python-пакет через bind mount.

---

## 10. Bootstrap ключей из конфига развёртывания

Чтобы не вставлять ключи руками при каждом локальном тесте / пересборке dev-окружения, processor поддерживает **первичный посев ключей из env** при первом старте.

**Механика:**

1. При старте сервис читает `keys.db` (создаёт если нет).
2. Если таблица `api_keys` пуста **ИЛИ** в ней нет ключа с `label = "bootstrap:<provider>"` — processor берёт соответствующий `BOOTSTRAP_*_API_KEY` из env и вставляет запись с таким label.
3. Идемпотентность: при рестарте без пустой БД ничего не делается. Админ может удалить bootstrap-ключ, и он не восстановится (запись в `keys.db` помечается `bootstrap_consumed=1` в отдельной мета-таблице, чтобы не перевставлять после ручного удаления).
4. Bootstrap создаёт ключи с `priority=100` и `is_active=1`. Админ дальше может правами admin API менять приоритеты и лимиты.
5. В логах при старте — строка `bootstrapped N api keys from env` или `no bootstrap keys found, db has M existing keys`.

**Env vars для bootstrap** (все опциональные, processor стартует и без них, но первые job-ы упадут с `503 no_provider_available`):

```
# transcription
BOOTSTRAP_ASSEMBLYAI_API_KEY=
BOOTSTRAP_DEEPGRAM_API_KEY=
BOOTSTRAP_OPENAI_WHISPER_API_KEY=       # если тот же, что openai_gpt4o — укажите оба
BOOTSTRAP_GROQ_API_KEY=

# vision / LLM
BOOTSTRAP_ANTHROPIC_API_KEY=
BOOTSTRAP_OPENAI_API_KEY=               # используется и для gpt4o, и для gpt4o_mini
BOOTSTRAP_GOOGLE_GEMINI_API_KEY=        # один ключ на pro и flash

# обязательное, без него сервис не стартует
PROCESSOR_KEY_ENCRYPTION_KEY=           # 32-байтный URL-safe base64 (Fernet)
```

**Файл `.env.processor`** в корне проекта (gitignored) — именно он подставляется через `env_file` в `docker-compose.yml` для processor-сервиса. Переменные с префиксом `BOOTSTRAP_` processor читает только при старте; в runtime они не используются — источник истины становится `keys.db`.

**Dev-значения для текущего проекта:** реальные ключи уже созданы в `.env.processor` в корне репозитория (`D:\PROGRAMS\VIRAL_MPV\.env.processor`), скопированы из [VIRAL MONITOR/.env](../../VIRAL%20MONITOR/.env). Там есть:
- Anthropic (vision, Claude)
- OpenAI (vision GPT-4o + транскрипция Whisper API)
- Groq (транскрипция Whisper)
- AssemblyAI (транскрипция)

На старте processor автоматически создаст 4 записи в `keys.db`. Deepgram и Google Gemini — не заполнены, добавляются позже через `POST /admin/api-keys`.
