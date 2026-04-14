# План: модуль профиля аккаунта (A7)

> Дата: 2026-04-14
> Статус: 🟡 scaffold создан (папки + файлы), требует ревью и утверждения.
> Предусловие — `Modules/script/` (A5) ✅ реализован, `viral_llm` ✅ shared-пакет.

---

## 1. Цель

Пакет `Modules/profile/` — централизованное хранилище профиля аккаунта пользователя: ниша, brand voice, портрет ЦА, системные промпты. Покрывает **A7.1 – A7.5 + A7.8 (частично)** из [ПЛАН_ВИРАЛ-монитор.md](../ПЛАН_ВИРАЛ-монитор.md).

**Что делает модуль:**
- Хранит в SQLite блоки профиля: brand book (tone-of-voice по 4 осям, запрещённые слова, CTA), аудиторию (демография, боли, желания), системные промпты с версионированием.
- Ведёт справочник ниш (~60 записей, seed из `fixtures/taxonomy.json`).
- Отдаёт `GET /profile/accounts/{id}` — **merged dict**, который клиент передаёт как `profile: {...}` в `POST /script/generate` без каких-либо изменений в script-сервисе.
- Backend-only: UI нет, тестирование через JSON-фикстуры и seed-скрипт.

**Зачем выделять в отдельный модуль:**
- Профиль будет использоваться **всеми** модулями: script, crawler (A2), publisher (A8), analytics (A9). Единый источник правды — отдельный сервис, а не таблицы в script.db или processor.db.
- Легко встраивается в `api`-монолит позже — router просто include'ится без изменений кода.

**Принципы:**
1. **Profile-сервис ничего не знает о LLM.** Он только хранит и отдаёт данные.
2. **Клиент сам передаёт profile в генератор.** Profile-сервис не вызывает script-сервис — нет зависимости.
3. **Нет PostgreSQL на этом этапе.** SQLite, WAL, именованный docker-volume — как в processor и script.

---

## ⚡ Тестовый сценарий MVP

1. Seed: `POST /profile/seed` (admin token) → загружает `example_account.json` в БД.
2. Получить профиль: `GET /profile/accounts/example` → merged dict.
3. Передать в script: `POST /script/generate` с полученным dict в поле `profile`.
4. Убедиться, что сценарий содержит элементы brand book (tone, CTA-фразы из профиля).

---

## 2. Структура модуля

```
Modules/profile/
├── __init__.py
├── auth.py               # require_token / require_admin_token
├── config.py             # Settings (DB_DIR, PROFILE_TOKEN, PROFILE_ADMIN_TOKEN)
├── logging_setup.py      # structlog (аналог script/logging_setup.py)
├── main.py               # FastAPI lifespan: init ProfileStore + seed taxonomy
├── router.py             # REST API /profile/*
├── schemas.py            # Pydantic models (request / response)
├── state.py              # singleton AppState { settings, profile_store }
├── storage.py            # ProfileStore: SQLite + все CRUD операции
├── seed.py               # load_taxonomy() + load_example_account() из fixtures/
├── Dockerfile
├── requirements.txt
├── fixtures/
│   ├── taxonomy.json          # ~60 ниш: slug, label_ru, label_en, parent_slug
│   └── example_account.json   # полный профиль для dev/test
└── tests/
    ├── __init__.py
    ├── conftest.py            # tmp-path ProfileStore fixture
    ├── test_storage.py        # unit-тесты CRUD
    └── test_router.py         # integration через TestClient
```

---

## 3. SQLite схема (`storage.py`)

```sql
-- Аккаунты
CREATE TABLE accounts (
    id          TEXT PRIMARY KEY,        -- uuid4 или строка "example"
    name        TEXT NOT NULL,
    niche_slug  TEXT,                    -- FK → niche_taxonomy.slug
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Справочник ниш (seed из taxonomy.json)
CREATE TABLE niche_taxonomy (
    slug        TEXT PRIMARY KEY,        -- "tech/ai"
    label_ru    TEXT NOT NULL,
    label_en    TEXT,
    parent_slug TEXT REFERENCES niche_taxonomy(slug)
);

-- Brand Book (один на аккаунт, upsert)
CREATE TABLE brand_books (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id           TEXT NOT NULL UNIQUE REFERENCES accounts(id),  -- UNIQUE: одна запись на аккаунт
    formality            INTEGER,         -- 1=формально, 10=неформально
    energy               INTEGER,         -- 1=спокойно, 10=энергично
    humor                INTEGER,         -- 1=серьёзно, 10=юморно
    expertise            INTEGER,         -- 1=просто, 10=экспертно
    forbidden_words_json TEXT NOT NULL DEFAULT '[]',
    cta_json             TEXT NOT NULL DEFAULT '[]',
    extra_json           TEXT NOT NULL DEFAULT '{}',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

-- Аудитория (один на аккаунт, upsert)
CREATE TABLE audience_profiles (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id        TEXT NOT NULL UNIQUE REFERENCES accounts(id),     -- UNIQUE: одна запись на аккаунт
    age_range         TEXT,              -- "25-35"
    geography         TEXT,
    gender            TEXT,
    expertise_level   TEXT,             -- beginner | intermediate | expert
    pain_points_json  TEXT NOT NULL DEFAULT '[]',
    desires_json      TEXT NOT NULL DEFAULT '[]',
    extra_json        TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

-- Промпт-профиль (версионированный; is_active=1 только у одной версии)
CREATE TABLE prompt_profiles (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id            TEXT NOT NULL REFERENCES accounts(id),
    version               TEXT NOT NULL,   -- "1.0", "1.1", ...
    system_prompt         TEXT NOT NULL,
    modifiers_json        TEXT NOT NULL DEFAULT '{}',
    hard_constraints_json TEXT NOT NULL DEFAULT '{}',
    soft_constraints_json TEXT NOT NULL DEFAULT '{}',
    is_active             INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL,
    UNIQUE(account_id, version)   -- нельзя создать дубликат версии
);
```

**Паттерн `ProfileStore`** — аналог `VersionStore` / `TemplateStore` из script:
- `__init__` создаёт директорию + выполняет SCHEMA-скрипт.
- `_conn()` → sqlite3 + WAL + foreign_keys + busy_timeout.
- upsert для brand_book и audience: merge-стратегия (None → сохранить старое значение).
- prompt_profile: создать новую версию = деактивировать все старые → INSERT с `is_active=1`.

---

## 4. REST API (`router.py`, prefix `/profile`)

Аутентификация: `X-Token` (worker) и `X-Admin-Token` (admin).

| Method | Path | Auth | Описание |
|---|---|---|---|
| GET | `/profile/healthz` | — | health check (публичный) |
| GET | `/profile/taxonomy` | X-Token | дерево ниш (опц. ?parent_slug=) |
| GET | `/profile/accounts` | X-Token | список аккаунтов |
| POST | `/profile/accounts` | X-Token | создать аккаунт |
| **GET** | **`/profile/accounts/{id}`** | X-Token | **полный merged профиль → в A5 как `profile: {}`** |
| PATCH | `/profile/accounts/{id}` | X-Token | обновить name / niche_slug |
| GET | `/profile/accounts/{id}/brand-book` | X-Token | |
| PUT | `/profile/accounts/{id}/brand-book` | X-Token | upsert (merge: null → сохранить старое) |
| GET | `/profile/accounts/{id}/audience` | X-Token | |
| PUT | `/profile/accounts/{id}/audience` | X-Token | upsert (merge: null → сохранить старое) |
| GET | `/profile/accounts/{id}/prompt-profile` | X-Token | активная версия |
| GET | `/profile/accounts/{id}/prompt-profile/versions` | X-Token | история версий |
| POST | `/profile/accounts/{id}/prompt-profile` | X-Token | создать новую версию (деактивирует старые) |
| POST | `/profile/accounts/{id}/prompt-profile/rollback/{version}` | X-Token | откат: деактивировать текущую → активировать указанную |
| POST | `/profile/seed` | X-Admin-Token | загрузить example_account.json (идемпотентно) |

**Ключевой ответ `GET /profile/accounts/{id}`** (FullProfileResponse) — merged dict для A5:
```json
{
  "account_id": "example",
  "name": "...",
  "niche": "tech/ai",
  "brand_book": {
    "tone_of_voice": {"formality": 4, "energy": 7, "humor": 3, "expertise": 8},
    "forbidden_words": ["дёшево", ...],
    "cta": ["Напишите в Telegram", ...]
  },
  "audience": {
    "age_range": "28-45",
    "pain_points": ["Теряют время на ручные операции", ...],
    "desires": ["Автоматизировать рутину", ...]
  },
  "system_prompt": "Ты опытный B2B-контент-стратег...",
  "hard_constraints": {"max_hashtags": 5, "require_cta": true},
  "soft_constraints": {"prefer_numbers": true},
  "prompt_version": "1.0"
}
```

---

## 5. Интеграция с A5 Script Generator

**Никаких изменений в `Modules/script/` не требуется.** Поле `profile: dict[str, Any]` в `GenerateReq` уже существует ([schemas.py:25](../Modules/script/schemas.py#L25)).

Workflow:
```
клиент → GET /profile/accounts/{id}     → получает FullProfileResponse
клиент → POST /script/generate          → передаёт FullProfileResponse.model_dump() в profile
script → _build_user_prompt()           → инжектирует profile как JSON в user_prompt (generator.py:86)
```

---

## 6. Docker и env

**Добавить в `docker-compose.yml`:**
```yaml
profile:
  build:
    context: .
    dockerfile: Modules/profile/Dockerfile
  image: viral-mpv/profile:dev
  container_name: viral-mpv-profile
  restart: unless-stopped
  env_file:
    - .env.profile
  environment:
    DB_DIR: /db
  volumes:
    - profile_db:/db
  ports:
    - "8300:8000"
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/profile/healthz').status == 200 else 1)"]
    interval: 30s
    timeout: 5s
    retries: 3
    start_period: 10s

volumes:
  profile_db:   # добавить к существующим processor_db / script_db
```

**`.env.profile`:**
```env
PROFILE_TOKEN=change-me
PROFILE_ADMIN_TOKEN=change-me-admin
DB_DIR=/db
BOOTSTRAP_EXAMPLE=false
```

---

## 7. Что включаем / откладываем

| Модуль | В этом этапе | Примечание |
|---|---|---|
| A7.1 Profile Store | ✅ | accounts + CRUD + merged endpoint |
| A7.2 Niche Taxonomy | ✅ | fixtures/taxonomy.json, ~60 ниш, seed при старте |
| A7.3 Brand Book | ✅ | tone-of-voice, forbidden_words, CTA |
| A7.4 Audience Profile | ✅ | age_range, pain_points, desires |
| A7.5 Account Prompt Profile | ✅ | система промптов + версионирование |
| A7.8 Versioning / Rollback | 🟡 | только prompt_profile; brand_book/audience — overwrite |
| A7.6 ToV Extractor (LLM) | ⬜ defer | LLM-анализ постов → Этап 11 Polish |
| A7.7 Profile Preview | ⬜ defer | требует BFF + вызов script-сервиса |

---

## 8. Тест-кейсы

### `test_storage.py` (unit, in-memory SQLite)
- `test_create_account` — создаёт аккаунт, проверяет поля
- `test_get_account_none` — несуществующий id → None
- `test_upsert_brand_book_insert` — первый upsert создаёт запись
- `test_upsert_brand_book_merge` — второй upsert с None-полями сохраняет старые значения
- `test_upsert_audience_insert_and_update` — аналог для audience
- `test_prompt_profile_versioning` — create v1.0 → create v1.1 → v1.0 is_active=False, v1.1 is_active=True
- `test_prompt_profile_unique_version` — создать v1.0 дважды → IntegrityError
- `test_rollback_prompt_profile` — v1.0 → v1.1 → rollback v1.0 → active=v1.0
- `test_rollback_unknown_version` — rollback несуществующей версии → None
- `test_get_full_profile_complete` — аккаунт + brand_book + audience + prompt → merged dict корректен
- `test_get_full_profile_partial` — аккаунт без brand_book → brand_book=None в результате
- `test_seed_taxonomy_idempotent` — seed дважды → одно и то же количество записей

### `test_router.py` (integration, TestClient)
- `test_healthz_no_auth` — GET /profile/healthz → 200 без токена
- `test_taxonomy_requires_token` — GET /profile/taxonomy без токена → 401
- `test_create_and_get_account` — POST accounts → GET accounts/{id} → 200 FullProfileResponse
- `test_get_unknown_account` — GET /accounts/nonexistent → 404
- `test_brand_book_validation` — PUT brand-book с formality=11 → 422
- `test_brand_book_upsert_and_merge` — два PUT: второй с null-полями не затирает первые
- `test_prompt_profile_versioning_via_api` — создать v1.0 → v1.1 → GET active → version=1.1
- `test_rollback_via_api` — rollback к v1.0 → GET active → version=1.0
- `test_rollback_unknown_version` — rollback к "99.0" → 404
- `test_seed_idempotent` — POST /seed дважды → не падает, возвращает `seeded: true/false`
- `test_full_profile_response_shape` — после seed → GET /accounts/example → все ключи present

## 9. Порядок реализации

- [x] **A. Scaffold** — папки, `__init__.py`, `config.py`, `logging_setup.py`, `auth.py`, `state.py`
- [x] **B. `storage.py`** — `ProfileStore`: init_db, CRUD для всех 5 таблиц + `get_full_profile()`; UNIQUE constraints
- [x] **C. `fixtures/`** — `taxonomy.json` (65 ниш) + `example_account.json`
- [x] **E. `schemas.py`** + **F. `router.py`** — Pydantic models + все endpoints
- [ ] **D. `seed.py`** — `load_taxonomy()`, `load_example_account()`
- [ ] **G. `main.py`** — lifespan: init ProfileStore + seed taxonomy + опц. example fixture
- [ ] **H. `Dockerfile` + `requirements.txt`** + **`.env.profile`** + **`.env.profile.example`**
- [ ] **I. `docker-compose.yml`** — добавить сервис profile + volume
- [ ] **K. Тесты** — `conftest.py`, `test_storage.py`, `test_router.py`
- [ ] **L. Проверка** — pytest tests/ + docker compose up + smoke curl

---

## 9. Проверка

```bash
# Поднять сервис
docker compose up profile

# Seed данными
curl -X POST http://localhost:8300/profile/seed \
  -H "X-Admin-Token: change-me-admin"

# Получить полный профиль
curl http://localhost:8300/profile/accounts/example \
  -H "X-Token: change-me"

# Передать в script (end-to-end)
curl -X POST http://localhost:8200/script/generate \
  -H "X-Worker-Token: ..." \
  -H "Content-Type: application/json" \
  -d '{
    "template": "reels_hook_v1",
    "profile": <вывод предыдущего curl>,
    "params": {"topic": "Автоматизация на AI", "duration_sec": 45}
  }'

# Тесты
cd Modules/profile && pytest tests/ -v
```
