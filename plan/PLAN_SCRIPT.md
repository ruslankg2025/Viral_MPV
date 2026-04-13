# План: модуль генерации сценариев (A5)

> Дата: 2026-04-13
> Статус: ⬜ не начат. Предусловие — `viral_llm` вынесен (✅ сделано), processor A3 работает (✅), processor A3.10 Prompts Registry работает (✅).

---

## 1. Цель

Пакет `Modules/script/` — генерация сценариев для коротких/длинных видео под профиль пользователя по заданному шаблону и набору ограничений. Объединяет модули **A5.1 – A5.4 + A5.6 + A5.7** из [ПЛАН_ВИРАЛ-монитор.md](../ПЛАН_ВИРАЛ-монитор.md#L76). **A5.5 A/B Testing Engine отложен** в отдельный микроплан после продового запуска A5.1-4.

**Что делает модуль:**
- Принимает: профиль аккаунта (пока — dict-стаб), шаблон (`vision_short_hook_v1` и т.п.), целевую длительность, формат (Reels/Shorts/long), доп. параметры (topic, language, tone).
- Вызывает LLM через `viral_llm.clients` + `viral_llm.keys.resolver` → получает структурированный JSON-сценарий.
- Валидирует hard/soft constraints. Возвращает fail при нарушении hard'ов.
- Сохраняет версию сценария в `scripts.db` с parent_id для fork-истории.
- Экспортирует в Markdown / JSON. DOCX — deferred.
- Экспонирует CRUD + generate + fork + export через FastAPI роутер.

**Зачем выделять в отдельный модуль:**
- Не переплетается с processor'ом — processor работает с *чужим* контентом (анализ ролика), script работает с *собственным* контентом (генерация). Разные домены, разные БД, разные клиенты.
- MVP живёт как standalone FastAPI-сервис; позже складывается в `api` монолит ([ПЛАН_ВИРАЛ-монитор.md:278](../ПЛАН_ВИРАЛ-монитор.md#L278)) без изменений кода — роутер просто include'ится в главный app.

**Принципы:**
1. **Script ничего не знает о видео.** На входе — текстовый контекст (topic, profile, pattern-hint). Генерируется текст сценария, не видеоряд. Продакшн — это A6 (AI Studio).
2. **LLM только через `viral_llm`.** Никаких прямых вызовов anthropic/openai — только через общий shared-пакет, чтобы routing, ключи, usage-трекинг работали единообразно.
3. **Версионирование — first-class.** Каждая генерация — запись в `script_versions`. Fork = создать новую запись с `parent_id`. История не переписывается, даже если пользователь «откатил» — откат = новая запись с тем же body, что родитель.

---

## ⚡ Тестовый сценарий MVP

1. Админ создаёт в script хотя бы один активный ключ провайдера через `POST /script/admin/api-keys` (или он bootstrap'ится из env `BOOTSTRAP_ANTHROPIC_API_KEY` при старте). Builtin-шаблоны (`reels_hook_v1`, `shorts_story_v1`, `long_explainer_v1`) bootstrap'ятся автоматически.
2. Клиент: `POST /script/generate` с payload:
   ```json
   {
     "template": "reels_hook_v1",
     "profile": {"niche": "tech", "voice": "casual", "target_audience": "devs 25-35"},
     "params": {"topic": "Почему все ушли с VS Code", "duration_sec": 45, "language": "ru"}
   }
   ```
3. Script вызывает `viral_llm.clients.registry.get_text_client("anthropic_claude_text")` (см. §2.2), получает JSON-сценарий.
4. Constraints валидируют длительность (в пределах ±15% от запрошенной) и обязательные секции (`hook`, `body`, `cta`).
5. Версия сохраняется в `script_versions`. Возвращается `{id, root_id, parent_id: null, status: "ok", body, cost_usd, ...}`.
6. Клиент: `POST /script/{id}/fork` с override `{params: {tone: "провокационный"}}` → новая генерация с `parent_id=id`.
7. Клиент: `GET /script/{id}/export/markdown` → `text/markdown`.

Всё это работает без A3.6 Viral Patterns и A7 Account Profile — pattern принимается free-text в `params`, profile — dict.

---

## 2. Точки решения

### 2.1 Что входит в первую итерацию

- [ ] **A. Generator Core** (A5.1) — один entry point `generate(ctx: GenContext) -> ScriptVersion`.
- [ ] **B. Prompt Templates** (A5.2) — хранилище версионированных шаблонов, копия паттерна из [processor/prompts/store.py](../Modules/processor/prompts/store.py). См. §2.4.
- [ ] **C. Constraints Engine** (A5.3) — hard/soft валидация. См. §2.5.
- [ ] **D. Storage + Versioning** (A5.4) — `script_versions` таблица, fork по `parent_id`, diff утилита. См. §2.6.
- [ ] **E. Export Service** (A5.7) — Markdown + JSON. DOCX deferred.
- [ ] **F. Router** (A5.6) — FastAPI APIRouter `/script/...`. Монтируется в standalone-app MVP или в `api` монолит позже.

**Не входит в MVP:**
- ❌ A5.5 A/B Testing Engine — отдельный микроплан после продового запуска.
- ❌ DOCX export — пока только MD+JSON (DOCX требует `python-docx`, легко добавить после).
- ❌ Collaborative cursors в редакторе — autosave + last-write-wins достаточно для одного юзера.
- ❌ Интеграция с A3.6 Viral Patterns — pattern-hint приходит free-text'ом от клиента; реальное подключение к реестру паттернов — после реализации A3.6.
- ❌ Интеграция с A7 Account Profile — profile приходит dict'ом от клиента; реальное чтение из БД профилей — после реализации A7.

### 2.2 Какой LLM-клиент использовать

**Проблема:** существующие `viral_llm.clients` — это transcription + vision, **text-only generation клиента нет**. Для генерации сценариев нужен именно text → text.

**Решение: расширить `viral_llm.clients` семейством `TextGenerationClient` и регистрировать клиентов под существующим `kind="vision"`.**

Обоснование `kind="vision"` а не нового `"text_generation"`: у Anthropic и OpenAI один и тот же API-endpoint обслуживает и vision (с картинками), и text-only (без картинок). Биллинг идентичный — `input_per_1m` / `output_per_1m` те же. Разделять их — значит заводить лишнюю сущность в `KeyKind` literal, дублировать pricing-entry, писать миграцию схемы `api_keys.kind`, и при этом админ будет вынужден создавать два ключа с одним и тем же секретом для каждой модели. Не оправдано.

Конкретные изменения:
- `viral_llm/clients/base.py`: новый класс `TextGenerationClient` + dataclass `GenerationResult(text, provider, model, input_tokens, output_tokens, latency_ms)`.
- Новый файл `viral_llm/clients/anthropic_text.py` — `AnthropicTextClient(provider="anthropic_claude_text", default_model="claude-sonnet-4-6")`. HTTP POST к `api.anthropic.com/v1/messages` через `httpx` без новых SDK-зависимостей.
- Новый файл `viral_llm/clients/openai_text.py` — `OpenAITextClient(provider="openai_gpt4o_text", default_model="gpt-4o")`. HTTP POST к `api.openai.com/v1/chat/completions`.
- `viral_llm/clients/registry.py`: новый `TEXT_GENERATION_CLIENTS: dict[str, TextGenerationClient]` + `get_text_client(provider)`.
- `viral_llm/keys/pricing.py`: две новых entry `anthropic_claude_text` и `openai_gpt4o_text` с `kind="vision"` (тот же биллинг).
- `viral_llm/keys/store.py`: **без изменений** — `KeyKind` literal не трогаем.

Этот под-этап — **Этап 0** плана (см. §5), до Этапа 1. Он блокирует всё остальное.

### 2.3 Структура сценария (`ScriptBody` schema v1)

```json
{
  "meta": {
    "template": "reels_hook_v1",
    "template_version": "v1",
    "language": "ru",
    "target_duration_sec": 45,
    "format": "reels"
  },
  "hook": {
    "text": "...",
    "estimated_duration_sec": 3.0
  },
  "body": [
    {"scene": 1, "text": "...", "estimated_duration_sec": 8.0, "visual_hint": "..."},
    {"scene": 2, "text": "...", "estimated_duration_sec": 10.0, "visual_hint": "..."}
  ],
  "cta": {
    "text": "...",
    "estimated_duration_sec": 4.0
  },
  "hashtags": ["#tag1", "#tag2"],
  "_schema_version": "1.0"
}
```

**Зачем `_schema_version`:** как в processor'овских result_v2 — позволяет эволюционировать схему без breaking changes у потребителей. Storage хранит `_schema_version` в отдельной колонке для быстрого фильтра.

### 2.4 Prompt Templates (A5.2)

**Решение: для MVP — копия PromptStore из processor.** На момент написания плана это уже второй потребитель паттерна (первый — A3.10), но они живут в разных процессах/контейнерах и разделены доменно (vision промпты vs script промпты). Вытаскивать в `Modules/shared/prompts/` сейчас — ранняя абстракция без третьего клиента.

`Modules/script/templates.py`:
- Класс `TemplateStore` — идентичная схема `templates(id, name, version, body, is_active, metadata_json, created_at, UNIQUE(name, version))` как у [processor/prompts/store.py](../Modules/processor/prompts/store.py).
- Bootstrap встроенных шаблонов: 2-3 начальных (`reels_hook_v1`, `shorts_story_v1`, `long_explainer_v1`).
- CRUD + activate + delete (активную удалить нельзя).
- Admin-роутер `/script/admin/templates/...` — копия [processor/prompts/router.py](../Modules/processor/prompts/router.py), меняется только префикс и store.

**Технический долг:** код PromptStore существует в двух местах. Пометить в обоих файлах `# DUP: keep in sync with Modules/script/templates.py` — чтобы при доработке одного не забыли другой. На третьем клиенте — выносим в shared.

### 2.5 Constraints Engine (A5.3)

**Hard constraints** (нарушение = fail генерации):
- `duration_within(target, tolerance_pct=15)` — сумма `estimated_duration_sec` всех секций попадает в `[target * 0.85, target * 1.15]`.
- `required_sections(["hook", "body", "cta"])` — все присутствуют, непустые.
- `body_min_scenes(2)` — минимум 2 сцены в `body`.
- `max_total_chars(limit)` — LLM не укатил в простыню (limit зависит от format).

**Soft constraints** (warning в ответе, не fail):
- `language_matches(declared)` — эвристика по langdetect.
- `hashtags_count_reasonable(min=3, max=10)`.
- `hook_duration_short(max=5)` — hook должен быть ≤5 секунд.

**Ретрай-цикл:** если hard-constraint failed, генератор делает **один retry** с добавлением в промпт `system_addendum` вида `"Previous attempt failed validation: {reason}. Fix it."`. Hard cap — ровно один retry.

**Что пишется в БД:**
- Первая попытка сохраняется **всегда**, даже если constraints не прошли, с `status="validation_failed"` и полным `constraints_report_json`.
- Retry сохраняется отдельной записью с `parent_id = первая_попытка.id` и `root_id = первая_попытка.id`, `status` — `ok` или `validation_failed` в зависимости от результата повторной валидации.
- В ответе роутера возвращается **финальная** запись (retry если был, иначе первая попытка). Клиент может пойти по `parent_id` и посмотреть историю, если нужен UX «что пошло не так».

Это переиспользует существующую fork-историю вместо заведения отдельной таблицы `generation_attempts`. Retry — это просто особый вид fork'а с автоматически сгенерированным override.

Constraints — чистые функции, легко юнит-тестятся.

### 2.6 Storage schema

`Modules/script/storage.py`, БД `scripts.db` (отдельный файл от processor, persistent volume).

```sql
CREATE TABLE script_versions (
  id              TEXT PRIMARY KEY,              -- uuid4
  parent_id       TEXT,                          -- NULL для корня, иначе id родителя
  root_id         TEXT NOT NULL,                 -- id корня дерева. Для корня = id.
  template        TEXT NOT NULL,
  template_version TEXT NOT NULL,
  schema_version  TEXT NOT NULL,                 -- _schema_version body
  status          TEXT NOT NULL,                 -- 'ok' | 'validation_failed' | 'error'
  body_json       TEXT NOT NULL,                 -- весь ScriptBody
  params_json     TEXT NOT NULL,                 -- входные params (topic, duration_sec, ...)
  profile_json    TEXT NOT NULL,                 -- snapshot профиля на момент генерации
  constraints_report_json TEXT,                  -- результат валидации
  cost_usd        REAL NOT NULL DEFAULT 0,
  input_tokens    INTEGER,
  output_tokens   INTEGER,
  latency_ms      INTEGER,
  provider        TEXT NOT NULL,
  model           TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  FOREIGN KEY (parent_id) REFERENCES script_versions(id)
);
CREATE INDEX idx_script_parent ON script_versions(parent_id);
CREATE INDEX idx_script_root ON script_versions(root_id);
```

**Fork:** новая запись с `parent_id = source.id`, `root_id = source.root_id`. Сам `body` — всегда свежая LLM-генерация, не копия старого (иначе fork вырождается в дубликат).

**Diff НЕ в MVP** — без UI сравнения эта утилита бесполезна. Если клиенту нужно сравнить две версии, он делает два `GET /script/{id}`.

**Data retention:** версии хранятся **навсегда** в MVP. Это пользовательский контент, стоимость удаления > стоимости хранения. Оценка: ~5-10 KB/запись × 1000 users × 100 scripts × 3 версии ≈ 1.5-3 GB SQLite — нормально. Политику retention вводим отдельным этапом при превышении 5 GB.

### 2.7 Export Service (A5.7)

`Modules/script/export.py` — две чистые функции:

```python
def to_markdown(body: ScriptBody) -> str: ...
def to_json(body: ScriptBody) -> str: ...   # pretty-printed с schema_version
```

**Markdown-шаблон:**

```markdown
# {params.topic}

**Language:** {meta.language}  |  **Target duration:** {meta.target_duration_sec}s  |  **Format:** {meta.format}

## Hook ({hook.estimated_duration_sec}s)
{hook.text}

## Body
### Scene 1 ({body[0].estimated_duration_sec}s)
{body[0].text}
> Visual: {body[0].visual_hint}

...

## CTA ({cta.estimated_duration_sec}s)
{cta.text}

---
{hashtags}
```

**Эндпоинт:** `GET /script/{id}/export/{format}` → соответствующий Content-Type (`text/markdown`, `application/json`). DOCX возвращает 501 Not Implemented в MVP.

### 2.8 Router (A5.6) API

```
POST   /script/generate
  body: {template, profile, params}
  → 201 { id, root_id, parent_id: null, version: 1, status, body, cost_usd, ... }

GET    /script/{id}
  → { ... полная запись ... }

GET    /script/tree/{root_id}
  → [{ id, parent_id, created_at, status, params_summary }, ...]    # история fork'ов

POST   /script/{id}/fork
  body: { override: {params?, template?, profile?} }
  → 201 { ... новая запись с parent_id=id ... }

DELETE /script/{id}
  → 204     # только если нет детей (иначе 409)

GET    /script/{id}/export/{format}
  → 200 <content>   # format: markdown | json | docx (докс: 501)

POST   /script/admin/templates
  → 201    # CRUD шаблонов, копия /admin/prompts из processor
GET    /script/admin/templates
GET    /script/admin/templates/{name}
PATCH  /script/admin/templates/{name}/activate/{version}
DELETE /script/admin/templates/{name}/{version}
```

**Авторизация:** worker-token для `/script/*` (генерация и чтение) и admin-token для `/script/admin/*` — по аналогии с processor'ом. В MVP-standalone — свои `SCRIPT_TOKEN` (default `dev-worker-token-change-me`) и `SCRIPT_ADMIN_TOKEN` (default `dev-admin-token-change-me`) из env, читаются pydantic-settings'ом как в processor. Когда сложат в `api` монолит — переключатся на общую auth.

### 2.9 Бюджет и usage tracking

**Решение: script имеет собственную `keys.db` в своём volume.** Никакой шаринг БД между processor и script — SQLite + concurrent writers из разных контейнеров = гарантированные проблемы, даже с WAL.

Следствия:
- Script создаёт свой `KeyStore`, свою таблицу `api_keys` + `api_key_usage` + `bootstrap_meta` (всё реиспользуется из `viral_llm.keys.store`, без дублирования).
- Bootstrap ключей из env — такой же как в processor: `BOOTSTRAP_ANTHROPIC_API_KEY`, `BOOTSTRAP_OPENAI_API_KEY` (теперь доедет до `anthropic_claude_text` / `openai_gpt4o_text` через fan-out, см. ниже).
- `viral_llm.keys.bootstrap.LLMBootstrapConfig` уже поддерживает эти 2 поля. Fan-out в `bootstrap.py` надо расширить, чтобы `anthropic_api_key` создавал и vision-, и text-провайдеров (одним ключом — все модели одного вендора). Это изменение в `viral_llm` выполняется на Этапе 0.
- Аггрегация стоимости за пользователя/месяц — на уровне приложения в последующих этапах (когда появится user_id и multi-tenant). В MVP — total + by_provider.
- Admin-роутер ключей в script — **переиспользует `api/admin_keys.py`** pattern из processor почти one-to-one, с заменой префикса на `/script/admin/api-keys`.

**Последствие**: админу первое время придётся ввести API-ключи дважды (processor и script). Это acceptable trade-off в MVP — централизация придёт отдельным этапом выноса `keys.db` в общий volume с координатором (или переезд на Postgres).

### 2.10 Concurrency

**Решение: MVP — асинхронный handler, 1 uvicorn worker, без своей очереди.** `POST /script/generate` определён как `async def`, ждёт httpx через `await`. Один uvicorn worker на asyncio event loop уже обслуживает N concurrent запросов одновременно — дополнительные workers нужны только под CPU-bound код, которого у script'а нет.

Конкретно:
- `httpx.AsyncClient` с `Timeout(60.0, connect=10.0)` — таймаут на LLM-вызов (anthropic/openai streaming внутри одного запроса).
- FastAPI handler `async def` — uvicorn event loop не блокируется; пока один handler ждёт httpx, другие принимают запросы и запускаются в параллель.
- `SCRIPT_TOKEN` авторизация и rate-limiting на уровне reverse proxy (nginx/traefik) — **не в scope** этого плана.

**Почему НЕ `--workers 2+`**: на первом старте создаётся race на bootstrap'е builtin-шаблонов (два worker'а одновременно INSERT → UNIQUE constraint). Добавлять race-tolerance в bootstrap — преждевременно, пока одного worker'а хватает. Если реальная нагрузка потребует 2+ workers — сначала решаем race (lock-файл или одноразовый init-контейнер), потом поднимаем.

**Что НЕ делаем**:
- Свою очередь как в processor'е (`JobQueue`) — script'у она не нужна, LLM-вызов короткий. Для processor'а очередь оправдана, т.к. ffmpeg + анализ кадров занимает минуты.
- Streaming ответов (SSE) — выйдет в отдельном этапе если реально понадобится для UX.

---

## 3. Целевая структура папки

```
Modules/script/
├── Dockerfile                    # MVP standalone
├── requirements.txt              # fastapi, uvicorn, pydantic, pydantic-settings, structlog
├── main.py                       # FastAPI app, lifespan, монтирует роутеры
├── config.py                     # Settings: SCRIPT_TOKEN, SCRIPT_ADMIN_TOKEN, DB_DIR, BOOTSTRAP_*
├── logging_setup.py              # DUP: копия processor/logging_setup.py
├── state.py                      # AppState: template_store, version_store, key_store
├── auth.py                       # DUP: копия processor/auth.py, свои токены
├── generator.py                  # A5.1 — generate(ctx) → ScriptVersion
├── templates.py                  # A5.2 — TemplateStore (DUP: sync with processor/prompts/store.py)
├── builtin_templates.py          # 3 встроенных шаблона: reels_hook_v1, shorts_story_v1, long_explainer_v1
├── constraints.py                # A5.3 — validate(body, params) → ConstraintsReport
├── storage.py                    # A5.4 — VersionStore (fork, tree walk)
├── export.py                     # A5.7 — to_markdown, to_json
├── schemas.py                    # Pydantic: GenContext, ScriptBody, GenerateReq, ForkReq
├── router.py                     # A5.6 — FastAPI APIRouter /script/*
├── admin_keys.py                 # DUP: копия processor/api/admin_keys.py с префиксом /script/admin
├── admin_templates.py            # admin CRUD шаблонов
└── tests/
    ├── __init__.py
    ├── conftest.py               # tmp sqlite + FAKE text client
    ├── test_constraints.py
    ├── test_storage.py
    ├── test_templates.py
    ├── test_export.py
    ├── test_generator.py         # FakeTextClient, retry-цикл на failed constraint
    └── test_router.py            # TestClient + моки через monkeypatch
```

**Рядом с processor, не внутри.** `Modules/script/` — equal sibling `Modules/processor/`, `Modules/downloader/`. Оба подключают `Modules/shared/llm/` через `pip install /opt/viral_llm` в Dockerfile.

**Долг по DUP:** `logging_setup.py`, `auth.py`, `admin_keys.py`, `TemplateStore` дублируются между processor и script. На третьем клиенте (или на моменте сборки `api` монолита) — одним рефакторингом выносим в `Modules/shared/{logging,auth,keys_admin,prompts}/`. Комментарии `# DUP:` в файлах отмечают точки синхронизации.

---

## 4. Зависимости (на момент старта A5)

| Dep | Статус | Как MVP живёт |
|---|---|---|
| `Modules/shared/llm` | ✅ | Editable install + расширение под text-generation clients (§2.2). |
| processor A3 (vision/transcription) | ✅ | **Не используется script'ом напрямую**, но подтверждает, что paths/infra работают. |
| processor A3.10 Prompts Registry | ✅ | Референс-имплементация, из которой копируется TemplateStore (§2.4). |
| A3.6 Viral Patterns | ⬜ | Patterns-hint приходит free-text в `params`. |
| A7 Account Profile | ⬜ | Profile приходит dict'ом. Когда A7 появится — клиент начинает читать из БД профилей и передаёт snapshot в `POST /script/generate`. |
| `api` монолит | ⬜ | MVP — standalone container `script`. Позже — `api/main.py` делает `from script.router import router as script_router; app.include_router(script_router)`. |

---

## 5. Этапы реализации

**Этап 0 — Предусловие (shared/llm расширение, §2.2)**
- Добавить `TextGenerationClient` + `GenerationResult` в `viral_llm/clients/base.py`.
- `viral_llm/clients/anthropic_text.py` — `AnthropicTextClient` через httpx.
- `viral_llm/clients/openai_text.py` — `OpenAITextClient` через httpx.
- `viral_llm/clients/registry.py`: `TEXT_GENERATION_CLIENTS` + `get_text_client`.
- `viral_llm/keys/pricing.py`: entries `anthropic_claude_text` и `openai_gpt4o_text` с `kind="vision"`.
- `viral_llm/keys/bootstrap.py`: `_FANOUT` расширить, чтобы `anthropic_api_key` → `anthropic_claude` + `anthropic_claude_text`, а `openai_api_key` → `openai_gpt4o` + `openai_gpt4o_mini` + `openai_gpt4o_text`.
- Тесты `Modules/shared/llm/tests/test_text_clients.py` — мок httpx, проверка парсинга ответов.
- `pip install /opt/viral_llm` в Dockerfile processor'а пересобирает без изменений pyproject.

**Этап 1 — Скелет модуля**
- `Modules/script/` с пустыми `__init__.py` + `main.py` со stub lifespan + `config.py` + `logging_setup.py` + `state.py` + `auth.py`.
- Dockerfile + requirements.txt + docker-compose.yml добавка сервиса `script`.
- `GET /script/healthz` — возвращает `{status: "ok", viral_llm_version: ...}`.
- CI: контейнер поднимается, healthz отвечает 200.

**Этап 2 — Templates store**
- `templates.py` + `schemas.py` (pydantic модели).
- 3 встроенных шаблона хардкодом + `bootstrap_builtin_templates`.
- `admin_templates.py` роутер CRUD.
- `tests/test_templates.py`.

**Этап 3 — Constraints + Storage**
- `constraints.py` — hard/soft + `ConstraintsReport`.
- `storage.py` — `VersionStore` CRUD, fork, tree walk.
- `tests/test_constraints.py`, `tests/test_storage.py`.

**Этап 4 — Generator core**
- `generator.py` — `generate(ctx)` с retry-циклом.
- Подключает `viral_llm.clients.get_text_client`, `viral_llm.keys.resolver`, `viral_llm.keys.pricing.estimate_cost`.
- `tests/test_generator.py` — FakeTextClient, проверка retry на failed constraint.

**Этап 5 — Router + Export**
- `router.py` — `/script/generate`, `/script/{id}`, `/script/{id}/fork`, `/script/tree/{root_id}`.
- `export.py` + endpoint `/script/{id}/export/{format}`.
- `tests/test_export.py`, `tests/test_router.py` (TestClient, моки LLM через monkeypatch).
- Smoke: `docker compose up script && curl POST /script/generate ...`.

**Этап 6 — End-to-end с реальным ключом (опционально перед мержем)**
- В dev-env положить BOOTSTRAP_ANTHROPIC_API_KEY через `.env.script`.
- Запустить реальную генерацию `reels_hook_v1` на тестовом profile.
- Проверить: версия в БД, стоимость не 0, Markdown-экспорт валиден.

**Этап 7 — A5.5 A/B Testing (deferred, отдельный план)**
- Не в этом файле. Создать `plan/PLAN_SCRIPT_AB.md` после продового запуска A5.1-4.

---

## 6. Risks & rollback

- **Text generation клиенты тянут новые SDK-ы** (anthropic text completion ≠ vision). Решение — использовать существующий `httpx` напрямую по HTTP API, как в уже реализованных vision-клиентах, без новых зависимостей.
- **ScriptBody schema эволюция** — обязательный `_schema_version` + колонка `schema_version` в БД; bumping правила описать в комментарии к `schemas.py`.
- **Code duplication с processor** (prompts store, auth, logging, admin_keys) — пометить `# DUP:` в каждом файле; завести issue-reminder «выделить в shared при сборке api-монолита».
- **Retry-цикл в генераторе** может зациклиться — hard cap 1 retry (§2.5), дальше `validation_failed` возвращается как финальная версия.
- **Синхронный handler под LLM-вызовом** — 5-15s на запрос блокирует worker. Митигейт — uvicorn с `--workers 2+` (§2.10) и жёсткий httpx timeout 30s. Перед продом оценить нагрузку; если > 100 RPM — заводить очередь отдельным этапом.
- **SQLite concurrent writers** между processor и script исключены — у каждого своя `keys.db` (§2.9).
- **Windows-совместимость volume'ов** — `scripts.db` и `keys.db` скриптового контейнера лежат в **named volume** `script_db` (а не bind-mount), по той же причине, что и `processor_db`: WAL несовместим с Windows bind-mount-ами.
- **Откат** — standalone сервис, полный rollback = `docker compose rm script && git revert ...`. processor не затрагивается.

---

## 7. Верификация

**После Этапа 0 (shared/llm text clients):**
0. `pip install -e Modules/shared/llm && pytest Modules/shared/llm/tests/test_text_clients.py` — зелёное.
0.1 `python -c "from viral_llm.clients.registry import get_text_client; print(get_text_client('anthropic_claude_text').default_model)"` — печатает `claude-sonnet-4-6`.

**После Этапа 5 (script модуль):**
1. `pytest Modules/script/tests` — зелёное.
2. `docker compose build script && docker compose up -d script` — healthz 200.
3. `POST /script/admin/templates` — создание нового шаблона с admin-token.
4. `POST /script/generate` с FakeClient (через env переключатель `SCRIPT_FAKE_LLM=1`) — получен JSON, сохранена версия, markdown-экспорт возвращает валидный текст.
5. `POST /script/{id}/fork` — создана вторая версия с parent_id.
6. `DELETE /script/{id}` на версии с детьми — 409.
7. Один прогон с реальным ключом (Этап 6) — стоимость > 0, latency < 10s, constraints all ok.

---

## 8. Что НЕ делаем в этом плане

- A5.5 A/B Testing Engine — отдельный план после MVP.
- DOCX export — заглушка 501.
- Интеграция с реальными A3.6 (patterns DB) и A7 (profile DB) — dict-стабы.
- Вынос TemplateStore / auth / logging / admin_keys в `Modules/shared/` — только при появлении третьего клиента или сборке `api` монолита.
- `user_id` в `script_versions` — YAGNI до появления A7.
- `diff_versions` утилита — бесполезна без UI, не в MVP.
- Collaborative cursors — не для single-user MVP.
- Streaming ответов LLM (SSE/websocket) — синхронный handler, см. §2.10.
- Оптимизации кеша генерации (как в processor'овском `CacheStore`) — каждая генерация уникальна (профиль + topic + версия шаблона), кеш почти не хитается.
- Шаринг `keys.db` с processor'ом — каждый сервис держит свою БД (§2.9).
- Переезд в `api` монолит — standalone контейнер MVP, fold in — отдельный микроплан, когда появится `api/`.
