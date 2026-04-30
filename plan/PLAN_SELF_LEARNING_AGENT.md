# PLAN: Self-Learning Agent для VIRAL_MPV

## Vision (одной строкой)

**Каждый пользователь со временем получает свой собственный AI-сценарист, который:**
- знает его tone-of-voice / целевую аудиторию / ниши,
- помнит примеры **одобренных** и **отклонённых** им сценариев,
- умеет применять методики из его учебных материалов (Демина, Claude-чаты, тренинги),
- **улучшается с каждым ★/🔥/💧 фидбеком** без ручной перенастройки.

Цель: чтобы пользователь только **ставил оценки** и через 2-3 недели использования получал стабильно хорошие сценарии под свой стиль, не правя промпты.

---

## Что УЖЕ есть (отличная база)

| Что | Где | Готово |
|---|---|---|
| Аккаунты + `language` + `niche_slugs` | `Modules/profile/storage.py` (accounts) | ✅ |
| **Brand book**: `tone_preset, formality, energy, humor, expertise, forbidden_words, CTA` | `brand_books` table | ✅ |
| **Audience profile**: `age_range, geography, gender, pain_points, desires` | `audience_profiles` table | ✅ |
| **Prompt profile** с версионированием: `system_prompt, modifiers, hard/soft constraints` | `prompt_profiles` table (UNIQUE account_id+version) | ✅ |
| Передача profile в script-сервис на генерацию | `script.generator._build_user_prompt` (`Profile: {json}`) | ⚠️ примитивно |
| KeyResolver + биллинг | `viral_llm/keys/resolver.py` | ✅ |

**Чего нет**:
- Persistent **feedback** (рейтинги/voтes) на сценарии — сейчас только локально в `studioState.procItems[].userRating`
- **Knowledge base** (RAG) — пользовательские материалы не используются
- **Few-shot examples** — system prompt не получает реальные «вот такие сценарии этому юзеру нравились»
- **Continuous loop** — `prompt_profile` пишется вручную, не самообучается
- **UI Settings** — пользователь не может загрузить TOV/ЦА/материалы через интерфейс

---

## Архитектура (целевая)

```
┌─────────────────── USER ───────────────────┐
│ ★ rates / 🔥 votes / "Переписать" / "Усилить" │
└──────────────────┬─────────────────────────┘
                   │ feedback events
                   ▼
        ┌──────────────────────┐
        │ FEEDBACK STORE        │  (новая таблица в profile_db)
        │ script_id / rating /  │
        │ vote / comment / ts   │
        └──────────┬───────────┘
                   │
                   ▼
   ┌──────────────────────────────────────────┐
   │ AGENT CONTEXT BUILDER (новый модуль)      │
   │ собирает per-user prompt-context:         │
   │  • base system_prompt (template)          │
   │  • user.brand_book (TOV)                  │
   │  • user.audience_profile (ЦА)             │
   │  • user.system_prompt_overlay (custom)    │
   │  • few-shot: top-3 ★≥4 sripts             │
   │  • avoid:    top-3 ★≤2 sripts             │
   │  • RAG: chunks из knowledge_base          │
   └──────────────────┬───────────────────────┘
                      │ enriched prompt
                      ▼
            ┌──────────────────┐
            │ script-сервис    │
            │ (existing)       │
            └──────────────────┘

┌────────── KNOWLEDGE BASE (новый модуль) ──────────┐
│ user uploads: PDF/MD/txt тренинги, Claude-чаты    │
│ ↓                                                  │
│ chunker (recursive, ~500 tokens chunks)           │
│ ↓                                                  │
│ embeddings (text-embedding-3-small / nomic)       │
│ ↓                                                  │
│ vector store: SQLite + sqlite-vss / Chroma local  │
└───────────────────────────────────────────────────┘

┌─── SELF-IMPROVEMENT LOOP (фоновый, daily) ────────┐
│ агрегирует feedback за неделю                      │
│ ↓                                                  │
│ если есть pattern: «★≤2 у скриптов с длинным CTA» │
│ ↓                                                  │
│ LLM-meta-prompt: «улучши system_prompt user-у»    │
│ ↓                                                  │
│ создаёт prompt_profile vN+1, делает active        │
└───────────────────────────────────────────────────┘
```

---

## Этапы реализации (инкрементально)

Все этапы — **независимые**, можно делать в любом порядке. Каждый даёт value сам по себе.

---

### Этап 1 — **Feedback persistence** (~3-4 ч)

**Задача:** ★/🔥/💧/комментарии сохраняются в БД, видны в истории.

**Backend (script-сервис):**
- Новая таблица `script_feedback` в `script_db`:
  ```sql
  CREATE TABLE script_feedback (
    id INTEGER PRIMARY KEY,
    script_id TEXT NOT NULL,           -- из script-сервиса
    account_id TEXT,                    -- кто поставил
    rating INTEGER,                     -- 1-5
    vote TEXT,                          -- 'fire' | 'water' | NULL
    comment TEXT,
    refine_request TEXT,                -- пользовательский комментарий "Усилить ..." / "Сократить"
    created_at TEXT NOT NULL
  )
  CREATE INDEX idx_feedback_script ON script_feedback(script_id);
  CREATE INDEX idx_feedback_account ON script_feedback(account_id, created_at);
  ```
- Новые endpoint-ы:
  - `POST /script/{version_id}/feedback` — body: `{rating, vote, comment, refine_request}`
  - `GET /script/{version_id}/feedback` — список фидбеков (для отладки)
  - `GET /accounts/{account_id}/feedback?days=30&min_rating=4` — для агрегации

**Frontend:**
- `_setProcRating(pid, n)` / `_setProcVote(pid, vote)` сейчас локальные → теперь POST в `/api/script/.../feedback` (через shell-proxy)
- Поле `refine_request` (текстовое поле) — UI уже есть («Напишите, что изменить...»), привязать к POST

**Файлы:**
- `Modules/script/storage.py` — добавить таблицу + методы (`save_feedback`, `list_feedback`)
- `Modules/script/router.py` — 3 endpoint-а
- `Modules/script/schemas.py` — `FeedbackReq`
- `Modules/shell/static/app/index.html` — заменить локальные `_setProcRating/_setProcVote` на async fetch

**Тесты:** ~5 тестов в `Modules/script/tests/test_feedback.py`

---

### Этап 2 — **Settings UI: brand book + ЦА + system prompt overlay** (~4-5 ч)

**Задача:** пользователь редактирует свой профиль через интерфейс, не через JSON-API.

**Frontend:**
- Новый раздел **«Настройки» → «Мой бренд»** в shell:
  - **TOV form**: presets (`expert`, `friendly`, `motivational`, `ironic`, `neutral`) + слайдеры `formality/energy/humor/expertise` (0-10) + textarea `forbidden_words` + textarea `cta_examples`
  - **ЦА form**: `age_range` (slider 18-65), `geography`, `gender`, текстовая `pain_points` (multi-line), `desires`
  - **Custom system prompt overlay** — большое textarea `system_prompt_overlay` («Ты пишешь в стиле Деминой...»)
- Все формы → `PUT /api/profile/accounts/{id}/brand-book` + `/audience-profile` + `/prompt-profile`

**Backend:** уже почти всё есть в `profile/router.py` — проверить что endpoint-ы exposed на shell-gateway.

**Файлы:**
- `Modules/shell/static/app/index.html` — новая секция `v-settings-brand` с формами
- `Modules/profile/router.py` — возможно добавить недостающие PUT endpoint-ы

---

### Этап 3 — **Context Builder: enriched prompt с few-shot из feedback** (~5-6 ч)

**Задача:** при генерации скрипта автоматически подмешать **примеры одобренных и отклонённых** скриптов того же user-а.

**Backend (script-сервис):**
- В `generator.py::_build_user_prompt` добавить:
  ```python
  if profile.get('account_id'):
      top_loved = feedback_store.top_rated(account_id, limit=3, min_rating=4)
      top_hated = feedback_store.bottom_rated(account_id, limit=3, max_rating=2)
      if top_loved:
          parts.append("# Сценарии этому пользователю НРАВИЛИСЬ:")
          for s in top_loved:
              parts.append(f"- {s.body.hook.text} → ★{s.rating}")
      if top_hated:
          parts.append("# Сценарии этому пользователю НЕ ПОНРАВИЛИСЬ:")
          for s in top_hated:
              parts.append(f"- {s.body.hook.text} → ★{s.rating} (\"{s.comment}\")")
  ```
- Новый метод `feedback_store.top_rated(account_id, limit, min_rating)` — JOIN feedback ↔ script_versions

**Эффект:** Claude видит реальные примеры «вот такое нравится этому юзеру» и адаптируется. **Это самое мощное** — few-shot работает лучше любых инструкций.

**Файлы:**
- `Modules/script/generator.py` — расширить prompt builder
- `Modules/script/storage.py` — `top_rated`, `bottom_rated` queries

---

### Этап 4 — **Knowledge Base + RAG** (~8-12 ч, самое крупное)

**Задача:** пользователь грузит PDF/MD/txt (тренинги, методички, шаблоны) → они индексируются → при генерации скрипта релевантные куски подмешиваются в prompt.

**Backend (новый микросервис `knowledge` ИЛИ в profile-сервис):**
- Endpoint `POST /knowledge/upload` — multipart с файлом
- Парсер:
  - PDF: `pypdf2` / `pdfplumber`
  - MD/txt: native
  - Чанкер: рекурсивный по headings/paragraphs, max ~500 tokens
- Embeddings:
  - **Default**: OpenAI `text-embedding-3-small` ($0.02/1M tokens — копейки)
  - **Альтернатива (free)**: `nomic-embed-text` через Ollama локально
- Vectorstore:
  - **Самое простое**: `sqlite-vss` (SQLite + vector search extension) — нулевая инфраструктура
  - **Альтернатива**: ChromaDB local
- Endpoint `POST /knowledge/query` — `{account_id, query_text, top_k=5}` → возвращает релевантные chunks

**В script-сервисе:**
- В `_build_user_prompt`: запрос `knowledge.query(account_id, transcript_text, k=5)` → подмешать chunks как «# Релевантный материал из твоей библиотеки:»

**UI:**
- В «Настройки» → «База знаний»: загрузка файлов, список загруженных (с возможностью удалить)

**Стоимость:** embeddings одноразово (~$0.001 за 50-страничный PDF). Query — ~$0.0001 на запрос.

**Файлы:**
- `Modules/knowledge/` (новый сервис) — main.py, storage.py, router.py, embeddings.py, parser.py
- `Modules/script/generator.py` — RAG-injection
- `docker-compose.yml` — knowledge service

---

### Этап 5 — **Continuous self-improvement loop** (~6-8 ч)

**Задача:** раз в неделю агрегировать feedback → LLM пишет себе **новый улучшенный system prompt** → сохраняется как новая версия `prompt_profile`.

**Backend (новый scheduler-job в profile-сервисе или отдельный worker):**
- Cron еженедельно (понедельник 4:00 UTC):
  ```python
  for account in accounts:
      recent_feedback = feedback_store.list_for_account(account.id, days=7)
      if len(recent_feedback) < 5: continue  # мало данных
      # Группируем по rating
      loved = [f for f in recent_feedback if f.rating >= 4]
      hated = [f for f in recent_feedback if f.rating <= 2]
      if not (loved and hated): continue  # нет паттерна
      # Meta-prompt
      current_prompt = profile_store.get_active_prompt_profile(account.id).system_prompt
      new_prompt = await llm.generate(
          system="Ты улучшаешь системный prompt сценариста на основе фидбека пользователя.",
          user=f"""
          Текущий prompt: {current_prompt}
          
          Сценарии что ПОНРАВИЛИСЬ ★≥4:
          {format_examples(loved)}
          
          Сценарии что НЕ понравились ★≤2 (с комментариями):
          {format_examples(hated)}
          
          Напиши УЛУЧШЕННЫЙ системный prompt — добавь правила, чтобы избежать паттернов
          из плохих сценариев и усилить паттерны из хороших. Только итоговый prompt, без объяснений.
          """,
      )
      profile_store.create_prompt_profile_version(account.id, version=f"auto-v{N+1}", system_prompt=new_prompt)
  ```
- Endpoint `GET /accounts/{id}/prompt-profile/history` — для UI чтобы видеть эволюцию
- Endpoint `POST /accounts/{id}/prompt-profile/{version}/activate` — откатить версию

**UI:**
- В «Настройки» → «Эволюция AI»: timeline версий с diff и кнопками «Активировать»
- Уведомление: «За неделю AI улучшил твой prompt v3 → v4 на основе 12 оценок»

**Файлы:**
- `Modules/profile/jobs/improve_prompts.py` (новый)
- `Modules/profile/main.py` — scheduler-trigger
- `Modules/profile/router.py` — history/activate endpoints

---

### Этап 6 — **Refine actions: «Усилить», «Сократить», «Переписать»** (~3-4 ч)

**Задача:** пользователь не только оценивает, но и **активно правит** результат — кнопками или текстом.

**Backend (script-сервис):**
- Новый endpoint `POST /script/{version_id}/refine` — body: `{action: 'amplify_hook'|'shorten'|'rewrite_intro'|'simplify'|'free', custom_text: str}`
- Создаёт **fork** существующего script с inject-ом инструкции:
  - `amplify_hook` → «Усиль зацепку, сделай её резче»
  - `shorten` → «Сократи на 30% сохранив ключевое»
  - `rewrite_intro` → «Перепиши вступление полностью»
  - `simplify` → «Упрости язык, убери термины»
  - `free` → подставляется `custom_text`
- Forked script сохраняется с `parent_id = original_version_id` (уже есть в schemas)

**UI:** кнопки уже есть в `_renderProcDoneCard`, привязать к POST.

---

## Приоритезация

| Этап | Польза | Сложность | Делать когда |
|---|---|---|---|
| **1** Feedback persistence | 🔥🔥🔥 — без неё ничего не работает | Малая | **СРАЗУ** после стабилизации текущего цикла |
| **2** Settings UI | 🔥🔥 — user может настроить себя сразу | Средняя | После 1 |
| **3** Context Builder (few-shot) | 🔥🔥🔥🔥 — самый большой uplift качества | Средняя | После 1 (нужен feedback store) |
| **6** Refine actions | 🔥🔥🔥 — превращает single-shot в итеративный workflow | Малая | После 1 |
| **4** Knowledge Base + RAG | 🔥🔥🔥 — даёт «знание Деминой» сценаристу | **Большая** | Когда хотите масштабировать обучение |
| **5** Continuous loop | 🔥🔥 — авто-улучшение, нужен критмасс данных | Средняя | После 30+ feedback событий накопится |

**Рекомендуемый порядок: 1 → 3 → 6 → 2 → 4 → 5**

---

## Метрики качества (как поймём что работает)

1. **Avg rating новых скриптов** — через 4 недели должен расти (с ★3.0 до ★4.0+)
2. **% «Использовать» на вариантах зацепки** — растёт (LLM попадает в нужный стиль с первого раза)
3. **Среднее число «Переписать» на скрипт** — падает
4. **Cost per "approved" script** — должен снижаться (меньше попыток до удовлетворения)

---

## Риски и trade-offs

| Риск | Митигация |
|---|---|
| RAG ухудшит качество если у user плохие материалы | Опционально включается в settings; default off |
| Continuous loop сломает prompt | Версионирование уже есть → откат одной кнопкой |
| Биллинг растёт от few-shot (длиннее prompt) | +200-500 input tokens = +$0.001 за script. Незначимо |
| feedback predates accounts.id (legacy data) | Связываем по script_version.parent → account через JOIN |
| Privacy: knowledge base может содержать чувствительное | RAG только в read-only, эмбеддинги per-account, не cross-user |

---

## Что НЕ делаем (явно)

- ❌ **Fine-tuning Claude/GPT моделей** — слишком дорого и привязка к одному провайдеру. Few-shot + RAG дают 80% эффекта за 1% бюджета
- ❌ **Свой LLM-сервис на Ollama** — поддержка ad-hoc моделей не нужна, у нас уже multi-provider через KeyResolver
- ❌ **«AI-агент» с многошаговым reasoning через function-calling** — сейчас single-shot достаточно, добавим если упрёмся в потолок качества

---

## Open вопросы для обсуждения

1. **Knowledge base scope**: только сценарии (TOV) или ещё и **факты про продукт** (для bizness-аккаунтов)? Если факты — нужна отдельная коллекция per-account
2. **Multi-tenant**: один user — много accounts (мульти-бренд)? Сейчас уже да (accounts), но AI-обучение — per-account или per-user?
3. **Экспорт user-данных**: GDPR-friendly — пользователь должен иметь возможность скачать свой knowledge base + всю историю. Это часть Этапа 4
4. **Pricing**: эти фичи — для Pro-тарифа? Или включены в base?
5. **Холодный старт**: пока feedback мало (< 10 событий), few-shot пустой — какой fallback? Использовать «лучшие сценарии ниши» из других user-ов (с обезличиванием)?

---

## Критические файлы для будущей имплементации

| Этап | Файл | Что делать |
|---|---|---|
| 1 | `Modules/script/storage.py` | + table `script_feedback`, методы CRUD |
| 1 | `Modules/script/router.py` | + 3 endpoint-а feedback |
| 1 | `Modules/shell/static/app/index.html` | заменить `_setProcRating/_setProcVote` на async fetch |
| 2 | `Modules/shell/static/app/index.html` | новый раздел `v-settings-brand` |
| 2 | `Modules/profile/router.py` | проверить exposed PUT-endpoint-ы |
| 3 | `Modules/script/generator.py` | расширить `_build_user_prompt` few-shot |
| 4 | `Modules/knowledge/` (новый) | embeddings, parser, RAG API |
| 4 | `docker-compose.yml` | knowledge service |
| 5 | `Modules/profile/jobs/improve_prompts.py` (новый) | weekly cron auto-bump |
| 6 | `Modules/script/router.py` | `POST /{id}/refine` |

---

## Когда начнём

Текущий блокер: **доделать текущий цикл** (donestate scenario rendering, "Смонтировать ролик" заглушка, Refine actions hook). После этого — Этап 1 (~3-4 ч) даёт сразу осязаемый эффект, открывает дорогу остальному.
