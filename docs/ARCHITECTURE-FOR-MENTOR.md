# VIRAL_MPV — архитектура для агента Mentor

Документ для агента Mentor, который читает данные Viral Monitor (VM) на том же
сервере и помогает продвигать блог. Только факты из кода репозитория VIRAL_MPV.
Где функциональности нет — помечено «не реализовано».

Прод-сервер: DigitalOcean Droplet «Roxber-mentor», `/opt/viral_mpv/`,
домен vira.roxber.com.

---

## 1. Назначение

VM — платформа мониторинга вирусных видео с AI-пайплайном для контент-мейкеров.
Пользователь добавляет источники (каналы/блогеров в Instagram, TikTok, YouTube);
VM периодически обходит их через Apify и YouTube Data API, снимает метрики
(просмотры, лайки, комментарии) и вычисляет «вирусность» — z-score и скорость
набора просмотров. Самые быстрорастущие ролики попадают в watchlist («Мои
авторы»). По кнопке «Разобрать» ролик проходит pipeline: скачивание видеофайла →
транскрипция аудио → анализ кадров (vision-LLM) → стратегический разбор (хук,
нарратив, визуал, вовлечение, продакшн). На основе разбора и профиля аккаунта
(ниша, brand book, аудитория) генерируется сценарий нового ролика. Дополнительно
есть Knowledge Base (загрузка PDF/MD/TXT с эмбеддингами для RAG) и ручной ввод
метрик блога (insights). Внешние ИИ-сервисы (OpenAI/Anthropic/AssemblyAI и др.)
на момент написания ключами не подключены — pipeline-шаги, требующие LLM, без
ключей не отрабатывают.

---

## 2. Модули

Код всех сервисов — в `Modules/<service>/`. Каждый — отдельный FastAPI-процесс в
своём Docker-контейнере. Внутри контейнера слушает порт **8000**; в таблицах ниже
«порт» — это порт, опубликованный на хост (только в dev-compose; на проде наружу
опубликован лишь shell).

| Модуль | Назначение | Вход | Выход | Dev-порт |
|--------|-----------|------|-------|----------|
| **shell** | API gateway/BFF + оркестратор pipeline + статика SPA | HTTP-запросы браузера `/api/*`, `/` | проксирование в сервисы, runs, отдача фронта | 8000 |
| **processor** | AI-обработка видео: транскрипция, кадры, vision, стратег. разбор | `file_path` видеофайла | транскрипт, кадры, vision-анализ, стратегия | 8100 |
| **script** | Генерация сценариев из разбора + профиля | разбор видео + профиль аккаунта | JSON-сценарий (сцены, музыка, субтитры) | 8200 |
| **profile** | Профиль аккаунта: ниши, brand book, аудитория, промпты | CRUD от пользователя через UI | профиль для инъекции в генерацию | 8300 |
| **monitor** | Мониторинг вирусности: источники, обходы, метрики, trending, watchlist | конфиг источников, Apify/YouTube API | videos, метрики, trending, watchlist | 8400 |
| **downloader** | Скачивание видеофайлов по URL | `{url, platform, quality}` | видеофайл в `/media`, sha256, метаданные | 8500 |
| **knowledge** | Knowledge Base + RAG: загрузка документов, эмбеддинги, поиск | PDF/MD/TXT (≤20 МБ) | чанки с эмбеддингами, top-K поиск | 8600 |
| **shared** | Не сервис. Общий код, в т.ч. pip-пакет `viral_llm` (LLM-клиенты, хранилище API-ключей, шифрование, pricing) | — | используется processor и script | — |

Ключевые зависимости:
- **monitor** → Apify (Instagram/TikTok), YouTube Data API v3, сервис profile.
- **processor** → внешние ИИ-API: AssemblyAI, Deepgram, OpenAI Whisper, Groq
  (транскрипция); Anthropic Claude, OpenAI GPT-4o, Google Gemini (vision).
- **script** → Anthropic Claude / OpenAI GPT-4o (генерация текста).
- **knowledge** → OpenAI `text-embedding-3-small`.
- **shell** → внутренние сервисы processor, downloader, monitor, profile, script,
  knowledge по HTTP внутри Docker-сети.
- Внешние ИИ-ключи на момент написания не подключены (см. CLAUDE.md → таблица API).

---

## 3. Хранение данных

**Тип:** только **SQLite** + файлы. PostgreSQL и иные СУБД в проекте **не
используются** (комментарий «read-only Postgres» в `Modules/shell/main.py` —
устаревший, фактически insights в SQLite).

Каждый сервис имеет свою БД (или несколько). Внутри контейнера БД лежат в каталоге
`/db` (env `DB_DIR=/db`). Каталог `/db` — это **именованный Docker-том** (в dev и
в prod одинаково — см. `docker-compose.yml` и `deploy/docker-compose.prod.yml`).
Медиафайлы — в `/media` (env `MEDIA_DIR=/media`), это **bind-mount** на каталог
репозитория `./data/media`.

### 3.1. Базы данных (файлы внутри `/db` контейнера)

| Сервис | Том (prod) | Файлы БД | Содержимое |
|--------|-----------|----------|------------|
| monitor | `deploy_monitor_db` | `monitor.db` | источники, видео, метрики, trending, watchlist, хэштеги, снимки профилей, лог обходов, квоты |
| processor | `deploy_processor_db` | `jobs.db`, `cache.db`, `keys.db`, `prompts.db` | очередь задач, кэш анализов, зашифрованные ИИ-ключи, версии промптов |
| script | `deploy_script_db` | `templates.db`, `scripts.db`, `keys.db` | шаблоны сценариев, версии сценариев + фидбек, ИИ-ключи |
| profile | `deploy_profile_db` | `profile.db` | аккаунты, таксономия ниш, brand book, аудитория, промпт-профили |
| downloader | `deploy_downloader_db` | `jobs.db` | очередь задач скачивания |
| knowledge | `deploy_knowledge_db` | `knowledge.db` | документы Knowledge Base + чанки с эмбеддингами |
| shell | `deploy_shell_db` | `runs.db`, `insights.db` | runs пайплайна, ручные метрики блога (insights) |

> Имя тома = `<project>_<volume>`. Project на проде = `deploy` (compose
> запускается из `cd /opt/viral_mpv/deploy`, см. `deploy/update.sh`). Точные имена
> проверяются командой `docker volume ls`. Физически тома лежат в
> `/var/lib/docker/volumes/<имя_тома>/_data/` — владелец `root`, чтение требует
> root или членства в группе `docker` (см. раздел 8).

Все БД работают в режиме `PRAGMA journal_mode=WAL`. Все временные метки — UTC
ISO8601.

### 3.2. Файловые хранилища

- **Видео, кадры, аудио:** bind-mount `./data/media` → внутри контейнера `/media`.
  На прод-сервере это **`/opt/viral_mpv/data/media/`** — обычный каталог,
  доступен по файловой системе. Скачанные видео, извлечённые кадры (`frames/`),
  аудиодорожки. `data/` в `.gitignore`.
- shell монтирует `/media` как **read-only** (`:ro`) — отдаёт миниатюры и аудио.

---

## 4. Схема данных

Ниже — основные таблицы. Помечено, **что создаёт/видит пользователь** (через UI).

### 4.1. `monitor.db` — мониторинг (главный источник аналитики)

**`sources`** — отслеживаемые блогеры/каналы. *Создаёт пользователь* («Добавить
автора»).
`id`, `account_id`, `platform` (youtube/instagram/tiktok), `channel_url`,
`external_id` (handle / channel_id), `channel_name`, `niche_slug`, `tags_json`,
`priority`, `interval_min`, `is_active`, `last_error`, `added_at`,
`last_crawled_at`, `max_results_limit`, `full_name`, `followers_count`,
`posts_count`, `avatar_url`, `is_verified`, `is_private`, `business_category`,
`profile_fetched_at`, `is_self` (флаг «мой аккаунт»).

**`videos`** — найденные ролики. *Создаёт система* при обходе.
`id`, `source_id`, `platform`, `external_id`, `url`, `title`, `description`,
`thumbnail_url`, `duration_sec`, `published_at`, `first_seen_at`, `is_short`,
`niche_slug`, `sha256`, `orchestrator_run_id`, `script_id`, `analysis_done_at`.

**`metric_snapshots`** — метрики ролика во времени. *Создаёт система*.
`id`, `video_id`, `captured_at`, `views`, `likes`, `comments`,
`engagement_rate`. Несколько снимков на ролик = динамика.

**`trending_scores`** — оценка вирусности. *Создаёт система*.
`id`, `video_id`, `computed_at`, `zscore_24h`, `growth_rate_24h`, `is_trending`,
`velocity` (просмотров/час), `is_rising`.

**`watchlist`** — лента «Мои авторы», топ-ролики под наблюдением. *Создаёт
система* (ежедневный отбор top-N) и закрывает пользователь.
`id`, `video_id`, `source_id`, `added_at`, `expires_at`, `initial_views`,
`initial_velocity`, `reason`, `status` (active/hit/miss/stalled/closed),
`graduated_at`, `hit_reason`, `closed_at`.

**`profile_snapshots`** — рост подписчиков источника по дням. *Создаёт система*.
`id`, `source_id`, `captured_date`, `followers_count`, `posts_count`.

**`video_hashtags`** — хэштеги роликов. *Создаёт система*. `video_id`, `tag`.

**`crawl_log`** — журнал обходов: `source_id`, `started_at`, `finished_at`,
`status`, `videos_new`, `videos_updated`, `error`.
**`youtube_quota`** — расход квоты YouTube API по дням.
**`apify_usage`** — расход Apify по дням/платформам.
**`plan_limits`** — лимиты тарифа (singleton-строка).

### 4.2. `profile.db` — профиль аккаунта (*создаёт пользователь*)

**`accounts`** — `id`, `name`, `niche_slug`, `niche_slugs_json`, `language`,
таймстемпы.
**`niche_taxonomy`** — справочник ниш: `slug`, `label_ru`, `label_en`,
`parent_slug`, `type` (mass/expert/both). Заполняется из fixtures.
**`brand_books`** — тон бренда: `account_id`, `tone_preset`, `formality`,
`energy`, `humor`, `expertise`, `forbidden_words_json`, `cta_json`. Один на аккаунт.
**`audience_profiles`** — аудитория: `age_range`, `geography`, `gender`,
`expertise_level`, `pain_points_json`, `desires_json`. Один на аккаунт.
**`prompt_profiles`** — версии системных промптов: `account_id`, `version`,
`system_prompt`, `*_constraints_json`, `is_active`.

### 4.3. `script.db` (scripts.db / templates.db) — сценарии

**`script_versions`** — сгенерированные сценарии. *Создаёт система по запросу
пользователя*. `id`, `parent_id`, `root_id`, `template`, `status`, `body_json`
(сам сценарий: сцены, музыка, субтитры), `params_json`, `profile_json`,
`constraints_report_json`, `cost_usd`, `input_tokens`, `output_tokens`,
`provider`, `model`, `created_at`.
**`script_feedback`** — оценки сценариев. *Создаёт пользователь*. `script_id`,
`account_id`, `rating` (1–5), `vote` (fire/water), `comment`, `refine_request`.
**`templates`** — шаблоны генерации: `name`, `version`, `body`, `is_active`.

### 4.4. `processor` БД — анализ видео

**`cache.db` / `cache`** — кэш результатов анализа (транскрипт, vision,
стратегия): `cache_key`, `kind`, `result_json`, `expires_at`. AI-разборы (хуки,
структура, паттерны) лежат здесь в `result_json`, а также в `runs.db`
(см. ниже) — отдельной «таблицы разборов» нет.
**`prompts.db` / `prompts`** — версии промптов анализа.
**`keys.db`** — `api_keys` (зашифрованные ИИ-ключи, Fernet), `api_key_usage`
(лог расхода: токены, стоимость). `keys.db` есть и у script.
**`jobs.db`** — очередь задач processor.

### 4.5. `shell` БД

**`runs.db` / `runs`** — жизненный цикл pipeline одного ролика. *Создаёт система
по запросу пользователя* («Разобрать»).
`id`, `video_id` (→ monitor.videos), `url`, `platform`, `external_id`,
`account_id`, `script_template`, `status` (queued→downloading→transcribing→
analyzing→done/failed), `current_step`, `steps_json` (результаты всех шагов:
download, transcribe, vision, strategy), `video_meta_json`, `result_json`,
`scripts_json`, `error`, таймстемпы. **AI-разбор готового ролика** (хуки,
нарратив, визуал) хранится здесь в `steps_json.strategy` / `result_json`.

**`insights.db` / `manual_insights`** — ручные метрики блога. *Создаёт
пользователь* (ввод Алины). `id`, `template_code` (`blog_daily`), `respondent`,
`account_id`, `response_date` (YYYY-MM-DD), `responded_at`, `data_json` (метрики:
просмотры, охват, вовлечение и т.п.), `created_at`.

### 4.6. `knowledge.db`

**`knowledge_documents`** — загруженные файлы. *Создаёт пользователь*.
`id`, `account_id`, `filename`, `content_type`, `size_bytes`, `chunks_count`,
`created_at`.
**`knowledge_chunks`** — чанки текста с эмбеддингами. *Создаёт система*.
`id`, `doc_id`, `account_id`, `chunk_index`, `text`, `token_count`,
`embedding` (BLOB float32, 1536-dim), `embedding_dim`, `created_at`.

### Где что лежит — сводка

| Что | Таблица / хранилище |
|-----|---------------------|
| Отслеживаемые блогеры/каналы | `monitor.db` → `sources` |
| Найденные вирусные ролики | `monitor.db` → `videos` + `trending_scores` + `watchlist` |
| Метрики роликов (динамика) | `monitor.db` → `metric_snapshots` |
| Рост подписчиков источников | `monitor.db` → `profile_snapshots` |
| AI-разборы (хуки, структура, паттерны) | `shell` → `runs.db.runs.steps_json/result_json`; кэш в `processor` → `cache.db` |
| Сгенерированные сценарии | `script.db` → `script_versions` |
| Профиль аккаунта пользователя | `profile.db` → `accounts` + `brand_books` + `audience_profiles` + `prompt_profiles` |
| Метрики блога (ручной ввод) | `shell` → `insights.db.manual_insights` |
| Knowledge Base | `knowledge.db` → `knowledge_documents` + `knowledge_chunks` |

---

## 5. API (gateway)

Браузер ходит только в shell. На проде shell слушает **`127.0.0.1:8080`** (порт
8000 контейнера); внешний nginx (vira.roxber.com, TLS) проксирует `/api/*` и `/`
на `127.0.0.1:8080`. Авторизации со стороны клиента нет — gateway сам
подставляет сервисные токены.

### 5.1. Прокси-маршруты

Gateway транслирует `/api/<module>/*` в соответствующий сервис, прозрачно прокидывая
методы GET/POST/PUT/PATCH/DELETE. Конкретные эндпоинты определены внутри
сервисов.

| Внешний путь | Сервис | Заголовок-токен | Блокируется |
|--------------|--------|-----------------|-------------|
| `/api/profile/*` | profile `/profile/*` | `X-Token` (PROFILE_TOKEN) | `/seed` |
| `/api/monitor/*` | monitor `/monitor/*` | `X-Token` (MONITOR_TOKEN) | `/admin/*` |
| `/api/script/*` | script `/script/*` | `X-Worker-Token` (SCRIPT_TOKEN) | `/admin/*` |
| `/api/knowledge/*` | knowledge `/knowledge/*` | `X-Worker-Token` (KNOWLEDGE_TOKEN) | — |

Заблокированные первые сегменты (`seed`, `admin`) возвращают 403
`admin_endpoint_not_proxied` — admin-эндпоинты наружу не проксируются.

Ключевые GET-эндпоинты monitor (полезны для аналитики, read-only):
`/api/monitor/sources?account_id=…` — список источников;
`/api/monitor/trending?account_id=…` — trending-лента;
`/api/monitor/watchlist?account_id=…&status=all` — лента «Мои авторы»;
`/api/monitor/videos/recent?account_id=…` — свежие видео;
`/api/monitor/thumb/{video_id}` — обложка (бинарь).

### 5.2. Эндпоинты, встроенные в shell (не прокси)

**Orchestrator** (без авторизации клиента):
- `POST /api/orchestrator/runs` — запустить pipeline (вход: `video_id` или
  `url`+`platform`); ответ `{run_id, status}`.
- `GET /api/orchestrator/runs/{id}` — статус/результат run.
- `GET /api/orchestrator/runs` — список runs.
- `POST /api/orchestrator/runs/manual` — run из текста (manual-сценарий).
- `POST/GET /api/orchestrator/published`, `POST .../published/{id}/refresh`,
  `DELETE .../published/{id}` — свои опубликованные рилсы.
- `POST/GET /api/orchestrator/runs/{id}/scripts`,
  `GET .../scripts/{script_id}` — генерация и чтение сценариев run.

**Media** — `GET /api/media/frames/{job_id}/{filename}`,
`GET /api/media/audio/{job_id}.mp3` (отдаёт из `/media`, read-only).

**Insights:**
- `POST /api/insights/blog-daily` — запись метрик блога. **Требует**
  `X-Worker-Token` = `INSIGHTS_WRITE_TOKEN`; без переменной — 503, неверный
  токен — 401.
- `GET /api/insights/blog-daily?days=…&respondent=…&account_id=…` — чтение
  метрик. **Публично, без токена.**
- `GET /api/insights/health` — статистика БД insights.

**Services** — `GET /api/services/status` — статус/расход внешних API
(Apify/OpenAI/…), кэш 5 мин, `?force=true` сбрасывает.

**Static** — `/` (на проде nginx переписывает в `/app/`) отдаёт consumer-SPA
`Modules/shell/static/app/index.html`; `/` внутри shell — admin-UI.

---

## 6. Поток данных — жизненный цикл ролика

1. **Обнаружение — monitor.** Пользователь добавляет источник →
   `monitor.db.sources`. Планировщик (APScheduler) или ручная кнопка «Обновить»
   запускает `orchestrate_crawl` (`Modules/monitor/crawler.py`): `fetch_new_videos`
   через Apify/YouTube → `videos`; `fetch_metrics` → `metric_snapshots`;
   `compute_trending` → `trending_scores`. Ежедневный отбор кладёт топ-ролики в
   `watchlist`. Лог обхода → `crawl_log`.

2. **Запуск разбора — shell/orchestrator.** Пользователь жмёт «Разобрать» →
   `POST /api/orchestrator/runs` с `video_id`. Создаётся строка в
   `shell/runs.db.runs` (`status=queued`), запускается фоновый pipeline.

3. **Загрузка — downloader.** `_step_download`: shell вызывает downloader
   (`POST /jobs/download`), тот скачивает файл (Apify / yt-dlp в зависимости от
   платформы) в `/media` (на проде `/opt/viral_mpv/data/media/`), возвращает
   `file_path`, `sha256`. Результат → `runs.steps_json.download`,
   очередь → `downloader/jobs.db`.

4. **Транскрипт и vision — processor.** `_step_transcribe` и `_step_vision`
   идут параллельно: транскрипция аудио (AssemblyAI/Deepgram/Whisper/Groq) и
   извлечение кадров + vision-LLM (Claude/GPT-4o/Gemini). Результаты кэшируются в
   `processor/cache.db` и пишутся в `runs.steps_json.transcribe` / `.vision`.

5. **Стратегический разбор — processor.** `_step_analysis`: на вход транскрипт +
   vision-анализ, на выход 5-секционный разбор (хук, нарратив, визуал,
   вовлечение, продакшн) → `runs.steps_json.strategy`. По завершении shell
   патчит `monitor.videos` (`orchestrator_run_id`, `sha256`, `analysis_done_at`).

6. **Генерация сценария — script.** Отдельная операция по запросу пользователя
   («Создать аналог»): на вход разбор + профиль аккаунта (`profile.db`), на выход
   JSON-сценарий → `script.db.script_versions`. В коде это не часть авто-pipeline
   шага 2–5, а отдельный вызов `/api/orchestrator/runs/{id}/scripts`.

> Шаги 4–6 требуют подключённых ИИ-ключей. Без ключей соответствующие шаги
> завершаются ошибкой.

Knowledge Base (модуль knowledge) — самостоятельный: загрузка документов,
чанкинг, эмбеддинги, поиск по cosine similarity. Автоматическое подмешивание
knowledge-чанков в генерацию сценария в коде **не подтверждено** — Mentor'у
полагаться на это не следует.

---

## 7. Деплой

- **Dev (Windows + Docker Desktop):** `docker-compose.yml` в корне репозитория.
  7 сервисов, порты на хост 8000–8600. БД — именованные тома, media —
  bind-mount `./data/media`.
- **Prod (DigitalOcean):** `deploy/docker-compose.prod.yml`. Контейнеры с
  префиксом `vira-` (`vira-shell`, `vira-monitor`, `vira-processor`,
  `vira-script`, `vira-profile`, `vira-downloader`, `vira-knowledge`). Наружу
  опубликован **только shell** на `127.0.0.1:8080`; остальные сервисы доступны
  лишь внутри Docker-сети. БД — именованные тома (раздел 3), media —
  bind-mount `/opt/viral_mpv/data/media`.
- **Внешний слой:** nginx (`deploy/nginx-vira.conf`) терминирует TLS на
  vira.roxber.com, проксирует `/api/*` и `/` на `127.0.0.1:8080`.
- **Авто-деплой:** cron каждые ~2 минуты запускает `deploy/update.sh`:
  `git fetch` + `git reset --hard origin/main` + `sync-env.sh` +
  `docker compose -f docker-compose.prod.yml up -d --build --remove-orphans`
  (из каталога `/opt/viral_mpv/deploy`).
- **Секреты:** 7 файлов `.env.<service>` в корне репозитория, не в git
  (образцы — `.env.<service>.example`).

---

## 8. Read-only доступ для Mentor

Mentor работает на том же сервере под пользователем `claw`. Цель — **только
чтение** данных VM, без вмешательства в работу сервисов.

### 8.1. Где лежат данные

- **БД (SQLite)** — в именованных Docker-томах
  (`/var/lib/docker/volumes/deploy_<service>_db/_data/*.db`). Владелец каталога
  тома — `root`. Прямое чтение файла требует root или членства `claw` в группе
  `docker`. Точные имена томов — `docker volume ls`.
- **Медиа** — каталог `/opt/viral_mpv/data/media/` на файловой системе (видео,
  кадры, аудио).

### 8.2. Рекомендуемый способ — снимок БД read-only

SQLite-файл живой БД нельзя открывать на запись и нельзя копировать «на горячую»
без учёта WAL. Безопасный путь — сделать консистентную копию через сам SQLite
и читать копию:

```
# скопировать БД monitor в каталог Mentor (нужен доступ к тому — docker или root)
docker run --rm -v deploy_monitor_db:/src:ro -v /home/claw/vm-snapshot:/out \
  alpine sh -c "apk add -q sqlite && sqlite3 /src/monitor.db \".backup /out/monitor.db\""
```

Затем Mentor читает копию в режиме только-чтение:
`sqlite3 'file:/home/claw/vm-snapshot/monitor.db?mode=ro' …`
Снимок периодически обновлять. Так живые БД сервисов не затрагиваются.

Если у `claw` нет доступа к Docker-томам — копию БД готовит владелец VM
(пользователь с правами) и складывает в каталог, читаемый `claw`.

### 8.3. Альтернатива — HTTP через gateway

Gateway на `127.0.0.1:8080` отдаёт данные по HTTP. Для read-only Mentor должен
**ограничиться методом GET** (gateway технически проксирует и POST/PATCH/DELETE —
самоограничение обязательно). Полезно:
`GET http://127.0.0.1:8080/api/monitor/trending?account_id=…`,
`/api/monitor/watchlist?...`, `/api/monitor/sources?...`,
`GET /api/insights/blog-daily?days=30` (метрики блога, без токена).

### 8.4. Что полезно для аналитики продвижения

- **`monitor.db`** — `sources` (кого отслеживаем), `videos` + `trending_scores`
  (что вирусится в нише, какие темы/хэштеги растут), `metric_snapshots`
  (динамика просмотров), `video_hashtags`, `profile_snapshots` (рост подписчиков
  конкурентов).
- **`shell/insights.db` → `manual_insights`** — метрики блога ROXBER
  (`template_code='blog_daily'`, `data_json`). Прямой сигнал по продвижению
  блога.
- **`script.db`** — `script_versions` (что генерировалось), `script_feedback`
  (как пользователь оценивал).
- **`profile.db`** — `accounts`, `brand_books`, `audience_profiles` (ниша, тон,
  целевая аудитория — контекст для рекомендаций).

### 8.5. Чего трогать нельзя

- **Не открывать живые `*.db` сервисов на запись** и не писать в них — повредит
  WAL и состояние работающего сервиса. Только снимок/копия, только `mode=ro`.
- **Не вызывать POST/PUT/PATCH/DELETE** через gateway — это изменяет данные VM.
- **Не писать** в `/opt/viral_mpv/data/media/` и не удалять файлы оттуда.
- **Не трогать** `.env.*`, не перезапускать контейнеры, не вмешиваться в
  `deploy/update.sh` / cron.
- **Не редактировать** код репозитория `/opt/viral_mpv/` — cron каждые 2 минуты
  делает `git reset --hard`, любые правки будут затёрты.
