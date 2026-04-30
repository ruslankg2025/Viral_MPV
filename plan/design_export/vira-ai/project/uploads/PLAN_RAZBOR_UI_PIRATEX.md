# PLAN_RAZBOR_UI — страница «Разбор» в стиле Piratex.ai

> Дата: 2026-04-29
> Референс: 7 скриншотов Piratex.ai от пользователя
> Статус: спецификация, моя текущая Этап-3.5-реализация (Perplexity-вертикальный pipeline) → переделать.
> Связанные файлы: [Modules/shell/static/app/index.html](../Modules/shell/static/app/index.html), [Modules/shell/orchestrator/runs/runner.py](../Modules/shell/orchestrator/runs/runner.py), [Modules/processor/](../Modules/processor/), [Modules/script/](../Modules/script/)

---

## 1. Что не так с текущей реализацией (Этап 3.5)

Я сделал **вертикальный stepped-timeline на 3 шага** (download → analyze → generate). Это не то.

Piratex.ai даёт совершенно другой UX:
- **Горизонтальная 6-шаговая прогресс-полоса** сверху
- **Прогрессивное раскрытие контента** — по мере прохождения шагов снизу появляются новые секции (видео-meta → грид кадров → сценарий и т.д.)
- **Done-состояние полностью другое** — pipeline исчезает, остаётся multi-card интерфейс с интерактивными элементами (правка LLM, варианты зацепки, копировать, рейтинг)
- **Frame-by-frame анализ** — каждый кадр показан отдельной карточкой с типом сцены, текстом на экране, визуальным описанием
- **4 артефакта** генерации (не один сценарий): телесуфлёр, описание поста, инструкция монтажёра, стратегия — плюс хэштеги и варианты зацепки

Моя текущая реализация — годная заглушка для smoke-теста, но в продакшн UX надо переделать.

---

## 2. Pipeline: 6 шагов вместо 3

### 2.1 Список шагов (порядок и иконки из скринов)

| # | Step ID | Label (RU)            | Icon (концепт)              | Backend mapping                             |
|---|---------|-----------------------|------------------------------|---------------------------------------------|
| 1 | `download`    | Скачиваем видео   | стрелка вниз ⬇             | downloader `/jobs/download`                 |
| 2 | `audio`       | Извлекаем аудио   | нота 🎵                     | processor `tasks/extract_audio.py`          |
| 3 | `transcribe`  | Транскрибируем    | заглавная T                  | processor `tasks/transcribe.py`             |
| 4 | `frames`      | Извлекаем кадры   | сетка 4 квадратов            | processor `tasks/extract_frames.py`         |
| 5 | `vision`      | Анализ кадров     | концентрические круги        | processor `tasks/vision_analyze.py` (per-frame) |
| 6 | `script`      | Генерация сценария| перо/палочка ✍              | script `/scripts/generate` (multi-artifact) |

### 2.2 Визуальные состояния шага

```
PENDING:    o    темно-серый круг, контурный, серый label
RUNNING:    ⊙    БЕЛЫЙ заполненный круг с лёгким glow/pulse, иконка чёрная,
                 label жирнее и ярче
DONE:       ✓    тёмно-серый круг с белой галочкой, label dim
FAILED:     ✗    красный круг, label красный
```

Линии между шагами:
- Между двумя `done` — сплошная белая/teal линия
- Активная (`done → running`) — частично белая (от done) → dim (к running)
- `pending → pending` — точечная серая

### 2.3 Заголовок над пайплайном

Постоянная строка с пульс-индикатором + текущее действие + процент:

```
● Скачиваем видео…              5%
● Извлекаем аудио…             18%
● Транскрибируем…              32%
● Извлекаем кадры…             45%
● Анализируем кадры…           56%
● Генерируем сценарий…         80%
```

Процент — это **общий прогресс pipeline** (не процент текущего шага). Можно мапить:
- shape: `0–8% download`, `8–20% audio`, `20–35% transcribe`, `35–50% frames`, `50–75% vision`, `75–100% script`
- Или брать реальный backend-прогресс там, где он доступен.

### 2.4 Бэкенд-требования для granular progress

Текущий orchestrator знает только 3 макро-шага. Нужно:

**Вариант A: orchestrator вызывает processor мелкими job-ами**
- `POST /jobs/extract-audio` (уже есть в processor — добавить explicit endpoint, сейчас только в `full-analysis`)
- `POST /jobs/transcribe` (есть)
- `POST /jobs/extract-frames` (есть)
- `POST /jobs/vision-analyze` (есть)
- Orchestrator делает их последовательно, каждый шаг = свой step в `runs.steps_json`
- + Возможна параллельность audio∥frames → transcribe∥vision (как уже в `full_analysis.py`)

**Вариант B: processor exposes progress hook**
- `full_analysis` пишет в новый поле `jobs.progress_json`: `{stage: 'transcribe', percent: 32}`
- Orchestrator поллит `GET /jobs/{id}` и видит progress
- Меньше HTTP-вызовов, но связывает state machine UI с внутренней реализацией processor

**Рекомендация: Вариант A.** Контракт чище, orchestrator владеет state machine, легче добавлять/менять шаги. Производительность — все вызовы внутрисетевые, разница ~50-100ms на лишний round-trip.

Расширение state machine orchestrator:

```python
RunStatus = Literal[
    "queued",
    "downloading",     # step download
    "extracting_audio",
    "transcribing",
    "extracting_frames",
    "analyzing_vision",
    "generating",      # step script
    "done", "failed",
]
```

И `steps_json`:
```json
{
  "download":    { status, downloader_job_id, file_path, sha256, duration_ms },
  "audio":       { status, processor_job_id, audio_path, duration_ms },
  "transcribe":  { status, processor_job_id, text_preview, words_count, language, cost_usd, duration_ms },
  "frames":      { status, processor_job_id, frames_dir, frames_count, kept, dropped, duration_ms },
  "vision":      { status, processor_job_id, frames_analyzed, scenes_summary, cost_usd, duration_ms },
  "script":      { status, script_job_id, script_id, artifacts: [...], cost_usd, duration_ms }
}
```

---

## 3. Раскладка страницы по фазам

### 3.1 Empty state — hero (pasting URL)

```
┌──────────────────────────────────────────────────────────────────┐
│ [logo Piratex.ai]  Разбор · Мои видео · Тренды         RU [G]   │ navbar
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│              ┄┄ РЕНТГЕН ДЛЯ ЗАЛЕТЕВШИХ РИЛСОВ ┄┄                │ eyebrow
│                                                                  │
│                       Разберите рилс                             │ h1 serif huge
│                                                                  │
│   Вставьте ссылку и получите покадровый разбор, транскрипцию    │ subtitle
│            и готовый сценарий для адаптации                      │
│                                                                  │
│   ┌──────────────────────────────────────────────┐  ┌────────┐  │
│   │ 🔗 https://www.instagram.com/p/...           │  │Разобрать│  │ pill input
│   └──────────────────────────────────────────────┘  └────────┘  │
│                                                                  │
│              Использовано 15 из 50 в этом месяце                 │ usage
│                                                                  │
│       (•) ── (•) ── (•)                                          │ 3-step indicator
│       Вставьте  ИИ        Готовый                                │ (статичный, не pipeline)
│       ссылку    анализирует сценарий                             │
└──────────────────────────────────────────────────────────────────┘
```

Уже близко к моей реализации. Поправки:
- Eyebrow меньше и тише (subtle gray, monospace, letter-spacing 0.22em)
- H1 ещё больше — ~88px
- Footer-step-indicator оставляем, но он маркетинговый (не реактивный)

### 3.2 Active state — pipeline в процессе

```
┌──────────────────────────────────────────────────────────────────┐
│ [navbar]                                                         │
├──────────────────────────────────────────────────────────────────┤
│ [warning bar если есть]                                          │
│                                                                  │
│           ● Анализируем кадры…              56%                  │ status line + %
│                                                                  │
│   ✓ ─── ✓ ─── ✓ ─── ✓ ─── ⊙ ─── o                              │ horizontal pipeline
│  Скач  Аудио  Транс  Кадры  Анал.  Генер.                       │ labels (одна строка)
│  видео        крибир.        кадров сценария                    │
│                                                                  │
│  [INSTAGRAM] positivityasparents 0:56 8.7K просм. 1.6K ❤ 10💬   │ video meta strip
│                                                                  │
│              ┄┄ ПОКАДРОВЫЙ РАЗБОР · 20 ┄┄                       │ section eyebrow
│                                                                  │
│   ┌────┐ ┌────┐ ┌────┐ ┌────┐                                   │ frames grid
│   │00:00│ │00:02│ │00:05│ │00:08│  4 cols (responsive)          │
│   │ #1 │ │ #2 │ │ #3 │ │ #4 │                                   │
│   │[Анима] [Анима] [Раздел] [Переб]                             │ scene type chip
│   │ img │ │ img │ │ img │ │ img │  (Reels 9:16)                 │
│   │ ⊙   │                                                       │ ◐ "АНАЛИЗ..." на текущем
│   └────┘ └────┘ └────┘ └────┘                                   │
│   ТЕКСТ НА ЭКРАНЕ                                                │
│   world... (мир...)                                              │
│   ВИЗУАЛ                                                         │
│   На картинке изображен человек…                                 │ frame description
│                                                                  │
│   ┌────┐ ┌────┐ … (все 20 кадров грид-ом, прогрессивно)        │
└──────────────────────────────────────────────────────────────────┘
```

Прогрессивное раскрытие:
1. Шаги 1-3 (download → audio → transcribe): показываем только pipeline + meta + eyebrow «АНАЛИЗ ВИДЕО» (заглушка)
2. Шаг 4 done (frames extracted): начинают прорастать карточки кадров **с placeholder-описанием** («АНАЛИЗ…»)
3. Шаг 5 (vision): кадры заполняются текстом постепенно (один за другим, по мере того как vision возвращает результат для каждого кадра)
4. Шаг 6 (script generation): над грид-ом кадров появляется секция «ПИШЕМ СЦЕНАРИЙ» с 4 skeleton-карточками (телесуфлёр / описание / инструкция монтажёру / стратегия). Каждая — пустая болванка с пульсирующими прямоугольниками-плейсхолдерами.

### 3.3 Done state — multi-card результат

Pipeline исчезает, заменяется на:

```
┌──────────────────────────────────────────────────────────────────┐
│ [navbar]                                                         │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│           [ Новый разбор ]    [ Скопировать ссылку ]            │ top action buttons (центр)
│                                                                  │
│  [INSTAGRAM] positivityasparents 0:56 8.7K просм. 1.6K ❤ ...    │ video meta strip
│                                                                  │
│              ┄┄ ГОТОВЫЙ РЕЗУЛЬТАТ ┄┄                            │ eyebrow
│                                                                  │
│         Оцените качество сценария  ☆ ☆ ☆ ☆ ☆                   │ rating
│                                                                  │
│  ┌────────────────────────────────────────────────┐ [✎][📋]    │
│  │ Сценарий для телесуфлёра                       │            │ карточка-сценарий
│  │                                                │            │
│  │ Когда твой ребёнок несётся к тебе со всех ног  │            │ длинный текст
│  │ – это лучшее чувство в мире.                   │            │ paragraphs
│  │                                                │            │
│  │ Вот эти мелочи. Как лицо загорается, едва      │            │
│  │ ты открываешь дверь. Как ноги несут быстрее...│            │
│  │                                                │            │
│  │ ~57 секунд                                     │            │ длительность
│  └────────────────────────────────────────────────┘            │
│                                                                  │
│  [+ Улучшить][Усилить зацепку][Сократить]                       │ LLM-actions chips
│  [Добавить конкретики][Переписать начало][Упростить]            │
│                                                                  │
│  ┌────────────────────────────────────────────────┐             │ free-form input
│  │ Напишите, что изменить...                      │             │ → custom LLM prompt
│  └────────────────────────────────────────────────┘             │
│                                                                  │
│  ┌────────────────────────────────────────────────┐             │
│  │ Варианты зацепки                               │             │
│  │ ┌──────────────────────────────────────────┐   │             │
│  │ │(1) Когда твой ребёнок несётся к тебе…   │ [✎][Используется]│
│  │ │    Техника: sensory vivid imagery…      │   │             │
│  │ │    [Сильный] ~4.9 сек — длинновато для зацепки           │
│  │ ├──────────────────────────────────────────┤   │             │
│  │ │(2) Когда твой ребёнок бежит к тебе так… │ [✎][Использовать]│
│  │ │    Техника: specificity + validation…    │   │             │
│  │ │    [Лучший выбор] ~8.1 сек — длинновато  │   │             │
│  │ └─ ... 5 вариантов всего ─                 │   │             │
│  └────────────────────────────────────────────────┘             │
│                                                                  │
│  ┌────────────────────────────────────────────────┐ [✎][📋]    │
│  │ Описание к рилсу                                │             │
│  │ Когда они несутся к тебе - и ничего важнее…❤  │             │
│  │ ...                                             │             │
│  └────────────────────────────────────────────────┘             │
│  [+ Улучшить][Усилить призыв к действию][Сократить][Эмодзи]    │
│  [free-form input]                                              │
│                                                                  │
│  ┌────────────────────────────────────────────────┐             │
│  │ Хэштеги                                         │ [📋]        │
│  │ #воспитание #родительство #семья                │             │
│  │ #эмоциональнаясвязь #присутствие #любовь        │             │
│  │ #мамаиребёнок #папаиребёнок #развитиеребёнка    │             │
│  │ #позитивноеродительство                         │             │
│  └────────────────────────────────────────────────┘             │
│                                                                  │
│  ┌────────────────────────────────────────────────┐ [✎][📋]    │
│  │ Инструкция для монтажёра                       │             │
│  │ VIDEO REFERENCE: https://...                    │             │
│  │ FORMAT: верхние 30% – говорящая голова…         │             │
│  │ DURATION: ~57 сек                               │             │
│  │                                                 │             │
│  │ - 0:00–0:03                                     │             │
│  │   Визуал: анимация - ребёнок бежит…             │             │
│  │   Текст на экране: ЛУЧШЕЕ ЧУВСТВО               │             │
│  │   Переход: cut                                  │             │
│  │                                                 │             │
│  │ - 0:03–0:07                                     │             │
│  │   ...                                           │             │
│  └────────────────────────────────────────────────┘             │
│  [+ Улучшить][Больше деталей][Упростить]                       │
│  [free-form input]                                              │
│                                                                  │
│              ┄┄ СТРАТЕГИЯ ПРОДВИЖЕНИЯ ┄┄                        │
│  ▸ Разбор залёта                              [Развернуть]      │ collapsible
│                                                                  │
│              ┄┄ ПОКАДРОВЫЙ РАЗБОР · 20 ┄┄                       │
│  [grid 4×N с теми же кадрами что в active state]                │
│                                                                  │
│              ┄┄ ОРИГИНАЛЬНЫЙ ТРАНСКРИПТ ┄┄                      │
│  ┌────────────────────────────────────────────────┐ [Копировать]│
│  │ Транскрипт (18)                                │             │
│  │ 0:00  Seeing your kids excited to see you…     │             │
│  │ 0:03  It's the way their face lights up…       │             │
│  │ 0:07  The way their feet run faster…           │             │
│  │ ...                                             │  ↕ scroll  │
│  │ 0:27  and no title or amount of success…       │             │
│  └────────────────────────────────────────────────┘             │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. Frame card — анатомия

Каждая карточка кадра в гриде:

```
┌────────────────────────────┐
│ [00:02] [Анимация]    #2  │ — header: timestamp chip + scene-type chip + rank
│ ┌────────────────────────┐ │
│ │                        │ │
│ │         IMAGE          │ │ — Reels-aspect (9:16)
│ │      9:16 vertical     │ │   image обрезается top/bottom если нужно
│ │                        │ │
│ │     world...           │ │   (опц. overlay-текст экрана если присутствует)
│ │                        │ │
│ │  ⊙ АНАЛИЗ...          │ │   pulse-бэйдж для текущего кадра в обработке
│ └────────────────────────┘ │
│ ТЕКСТ НА ЭКРАНЕ            │ — small uppercase label
│ world... (мир...)          │ — extracted text + перевод в скобках
│                            │
│ ВИЗУАЛ                     │ — small uppercase label
│ На картинке изображен      │ — paragraph (LLM vision-описание)
│ человек в дверном проёме,  │
│ который протягивает руки…  │
│                            │
│ [логотип @posit… в нижней   │ — UI-elements pill (опционально)
│  части изображения]        │
└────────────────────────────┘
```

**Цвета чипов scene-type** (примерные из скриншотов):
- `Анимация` — фиолетовый (#8b5cf6 на тёмном)
- `Перебивка` — серо-голубой
- `Разделённый экран` — оранжевый
- `Говорящая голова` — синий
- `Текст` — зелёный
- `B-roll` — нейтральный

**Backend для frame card** (новое):
- `vision_analyze` сейчас возвращает один общий `vision: {hook, structure, scenes:[...], why_viral}`. Нужно расширить:
  - Per-frame `scenes` уже есть в structured output, но нужно гарантировать поля: `timestamp_sec, scene_type, text_on_screen (orig + перевод), visual_description, ui_elements`
  - Новый prompt template `vision_per_frame_v1` выдаёт ровно эту структуру
  - Возможно отдельный API endpoint для streaming per-frame results, чтобы UI заполнял карточки прогрессивно

---

## 5. Hook variants card — анатомия

```
┌────────────────────────────────────────────────────────────────┐
│ Варианты зацепки                                               │
│                                                                │
│ ┌────────────────────────────────────────────────────────────┐│
│ │ (1) Когда твой ребёнок несётся к тебе со всех ног –        ││
│ │     это лучшее чувство в мире.                              ││
│ │     Техника: sensory vivid imagery (описание физических    ││
│ │       деталей). Психологический механизм – первая строка   ││
│ │       'Seeing your kids excited' напрямую адресует базовое ││
│ │       желание родителя (быть нужным и любимым). …          ││
│ │     [Сильный]  ~4.9 сек – длинновато для зацепки           ││
│ │                                       [✎] [Используется]   ││
│ ├────────────────────────────────────────────────────────────┤│
│ │ (2) Когда твой ребёнок бежит к тебе так, как будто ты      ││
│ │     его спасение – это не просто момент. Это…              ││
│ │     Техника: specificity + validation. …                    ││
│ │     [Лучший выбор]  ~8.1 сек – длинновато для зацепки      ││
│ │                                       [✎] [Использовать]   ││
│ ├────────────────────────────────────────────────────────────┤│
│ │ (3) … (Сильный) ~7.5 сек                                    ││
│ │ (4) … (Альтернатива) ~7.8 сек                               ││
│ │ (5) … (Альтернатива) ~7.2 сек                               ││
│ └────────────────────────────────────────────────────────────┘│
└────────────────────────────────────────────────────────────────┘
```

Цвета бейджей оценки:
- `Сильный` — голубой
- `Лучший выбор` — зелёный (выделенный)
- `Альтернатива` — серый

**Backend**: script-gen возвращает массив `hook_variants` каждый с полями `{text, technique_explanation, strength: 'strong'|'best'|'alt', estimated_duration_sec, length_warning?}`.

---

## 6. LLM-action chips и free-form input

Каждый редактируемый блок (сценарий, описание, инструкция) сопровождается:

```
[+ Улучшить] [Усилить зацепку] [Сократить] [Добавить конкретики] [Переписать начало] [Упростить]
[Напишите, что изменить...                                                            ]
```

Каждый chip — это preset-промпт (`{action: 'улучшить', target: 'script'}`).
Free-form input → custom-промпт (`{action: 'custom', user_prompt: '...', target: 'script'}`).

**Backend требования**:
- Новый endpoint `POST /scripts/{id}/transform`
  ```json
  {
    "target": "script|hook|description|editor_brief",
    "action": "improve|strengthen_hook|shorten|simplify|...|custom",
    "user_prompt": "..."  // если action=custom
  }
  ```
- Возвращает обновлённый артефакт + ID новой версии (история версий)

**UI**:
- Action chip click → POST → loading state на карточке (skeleton overlay) → swap content
- Кнопка `[✎]` в шапке карточки → inline edit mode (редактируемый textarea с двумя кнопками «Сохранить» / «Отменить»)
- Кнопка `[📋]` → копирование в буфер с feedback «✓ скопировано»

---

## 7. Editor brief card (Инструкция для монтажёра)

Особый формат — техническое ТЗ:

```
VIDEO REFERENCE: https://www.instagram.com/p/DXsEyErDrJA
FORMAT: верхние 30% экрана - говорящая голова, нижние 70% - визуал
DURATION: ~57 сек

- 0:00–0:03
Визуал: анимация - ребёнок бежит к взрослому в дверях, тёплый свет
Текст на экране: ЛУЧШЕЕ ЧУВСТВО
Переход: cut

- 0:03–0:07
Визуал: крупный план - лицо ребёнка, который улыбается, глаза светятся
Текст на экране: ЛИЦО ЗАГОРАЕТСЯ
Переход: cut

... (по таймкодам, всего 11 блоков для ролика 0:57)
```

**Стиль**: monospace, лёгкий dim для меток ("VIDEO REFERENCE:", "Визуал:", "Переход:"), яркий для контента, цвет-акцент для timestamp-заголовков (`- 0:00–0:03`).

**Backend**: `script-gen` возвращает `editor_brief: {format_note, duration_sec, scenes: [{ts_start, ts_end, visual, text_on_screen, transition}]}`.

---

## 8. Strategy section («Стратегия продвижения»)

Одна collapsible секция «▸ Разбор залёта». При открытии — длинный аналитический текст с разбором почему этот ролик сработал, что сделать чтобы свой залетел: целевая аудитория, эмоциональные триггеры, временные окна публикации, рекомендуемые хэштеги/звуки и т.п.

**Backend**: новый артефакт `growth_strategy` от script-gen — длинный markdown.
**UI**: collapse-by-default, expand button на правом краю.

---

## 9. Original transcript section

```
┌─────────────────────────────────────────────────┐
│ Транскрипт (18)                  [Копировать текст] │
├─────────────────────────────────────────────────┤
│ 0:00  Seeing your kids excited to see you…      │
│ 0:03  It's the way their face lights up…        │ ↕ scroll
│ 0:07  The way their feet run faster…            │
│ 0:11  The way they shout your name like…        │
│ ...                                              │
└─────────────────────────────────────────────────┘
```

Имя метки `Транскрипт (18)` — `(18)` это число фраз/таймкодов. Текст на оригинальном языке (тут английский).

**Backend**: уже есть в processor `result.transcript.text` + segments с timestamps. Достаточно отдать `segments[]` в API.

---

## 10. Top action buttons (done state)

Когда pipeline завершён, вместо горизонтального pipeline сверху:

```
              [ Новый разбор ]   [ Скопировать ссылку ]
```

- `Новый разбор` → reset state, обратно на hero
- `Скопировать ссылку` → копирует URL текущего разбора (deep-link для шеринга, например `/app/razbor/{run_id}`)

**Backend**: нужен deep-link router и persistent runs API:
- Сейчас runs живут в `runs.db` 30 минут (sessionStorage), и не «share-able» извне. Нужно:
  - Постоянное хранение runs (без TTL)
  - Маршрут `/app/razbor/{run_id}` который восстанавливает страницу
  - Возможно: `/app/razbor/{slug}` где slug — короткий хеш (для красоты ссылки)

---

## 11. Видео-meta strip (под pipeline / шапкой done)

```
[INSTAGRAM]  positivityasparents  0:56  8.7K просм.  1.6K лайков  10 комм.
```

- `[INSTAGRAM]` — chip с цветом платформы
- `positivityasparents` — handle автора (без @)
- `0:56` — длительность ролика (mm:ss)
- `8.7K просм.` — текущий счётчик просмотров (с форматированием K/M)
- `1.6K лайков` — лайки
- `10 комм.` — комментарии

Все значения берутся из последнего snapshot в `monitor.metric_snapshots` для этого видео (если запускали через video_id) либо из processor-fetch (если просто URL).

---

## 12. Skeleton-loading паттерн

Где используется (видно на скрине «Генерируем сценарий 80%»):

```
┌─────────────────────────┐
│ ● ТЕКСТ ДЛЯ ТЕЛЕСУФЛЁРА│
│                         │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓   │  ← shimmer rectangle
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓     │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓   │
│ ▓▓▓▓▓▓▓▓▓▓             │
└─────────────────────────┘
```

Цвет: `var(--bg2)` с лёгким горизонтальным shimmer-градиентом. Применяется к 4 карточкам одновременно во время `script` шага.

---

## 13. Phased delivery — что делать сначала

Текущая реализация (Этап 3.5 моя версия) — полностью переделать. Делю на 4 фазы:

### Фаза A — pipeline UI + granular backend (~2 дня)
- Расширить orchestrator state machine до 6 шагов (статусы + steps_json)
- Orchestrator вызывает 4 отдельных processor-job-а вместо одного `full-analysis`:
  `extract-audio → transcribe → extract-frames → vision-analyze` (с возможной параллельностью audio∥frames)
- Frontend: горизонтальный pipeline с 6 шагами, статусной строкой, %, Piratex-цветами
- Frontend: video-meta strip под pipeline

### Фаза B — frame grid + per-frame vision (~2 дня)
- Расширить vision_analyze prompt до per-frame structured output: `{frames:[{timestamp, scene_type, text_on_screen{orig, ru}, visual_description, ui_elements}]}`
- Реализовать прогрессивный stream: vision_analyze шлёт результат каждого кадра отдельно (через chunked HTTP или polling с накопительным `frames_done` в job result)
- Frontend: грид кадров 4 колонки, scene-type chips с цветами, прогрессивное появление АНАЛИЗ→текст
- Frontend: соблюдать pace — карточки появляются по мере того как vision возвращает данные

### Фаза C — script-gen multi-artifact + done-state UI (~3 дня)
- Расширить `/scripts/generate` так, чтобы возвращать 4 артефакта одним job-ом:
  `{script_text, hook_variants[], description, editor_brief, hashtags, growth_strategy}`
- Plus skeleton-плейсхолдеры для тех, что ещё не готовы (стримятся по мере готовности)
- Frontend: done-state с 6 карточками, рейтинг, copy-buttons, edit-buttons, action-chips
- Сохранение versions в БД (для отката)

### Фаза D — LLM-transform actions + sharing + сохранение (~2 дня)
- Endpoint `POST /scripts/{id}/transform` для action-chips и free-form input
- Frontend: chip click → loading overlay → swap content
- Inline edit mode (`[✎]` в шапке карточки)
- Постоянное хранение разборов (без 30-мин TTL), deep-link `/app/razbor/{run_id}`
- Кнопки top: «Новый разбор», «Скопировать ссылку»

**Итого: ~9 рабочих дней до полной фичи. После Фазы A уже выглядит «как Piratex».**

---

## 14. Что переиспользуем из текущего кода

- ✅ `runs` store — расширить `RunStatus` enum, добавить новые ключи в `steps_json`
- ✅ Orchestrator runner — основная архитектура остаётся (asyncio task, single-flight, recovery loop)
- ✅ Monitor lookup, video-meta caching — уже работает
- ✅ DownloaderClient + ProcessorClient — добавим `submit_extract_audio`, `submit_transcribe` etc.
- ✅ V13 schema, PATCH endpoint — без изменений
- ❌ `_renderRzSteps` (мой Perplexity-стиль) — **полностью переписать** под горизонтальный 6-шаговый layout
- ❌ `_renderRzResult` — **переписать** под multi-card done-state

---

## 15. Закрытые решения (закрепляем перед стартом Фазы A)

> Дата фиксации: 2026-04-29. Часть отвечает пользователь, часть — рекомендованные дефолты, согласованные одной командой.

### Q1. TTL разборов → **tied to plan** (Free 30 / Pro 90 / Business вечно)

- Поле `ttl_days` в новой таблице `razbor_plans` (или env-конфиг сейчас): `{free: 30, pro: 90, business: null}`.
- Пока нет billing-модуля (A10) — все юзеры считаются **«Pro» с TTL=90 дней**.
- `cleanup-loop` в shell/orchestrator раз в сутки удаляет `runs` + связанные артефакты, где `created_at + ttl_days < now`.
- Файлы mp4 уже удаляются раньше (после done из downloader), здесь чистим только метаданные/JSON.
- **Reference TTL** для downloader (failed-mp4) остаётся 24ч — это про другое.

### Q2. Квоты «15 из 50» → **profile.accounts.usage_***, инкремент при `run.status=done`

- Миграция profile-store: `ALTER TABLE accounts ADD COLUMN monthly_razbor_quota INTEGER DEFAULT 50, ADD COLUMN razbor_used_this_month INTEGER DEFAULT 0, ADD COLUMN quota_period_start TEXT`.
- Orchestrator после `run.status=done` вызывает `POST profile/accounts/{id}/usage/increment` (новый endpoint).
- **На failed — НЕ инкрементируем** (защита юзера от штрафа за технические сбои).
- Quota check **до** запуска run: `GET profile/accounts/{id}/quota` → если `used >= quota` → `429 quota_exceeded`.
- Сброс счётчика: cleanup-loop раз в сутки проверяет `quota_period_start` — если месяц прошёл, обнуляет (`razbor_used_this_month=0`, `quota_period_start=now`).
- Пока нет real plans → дефолтная квота `monthly_razbor_quota=50` для всех (env-override `DEFAULT_MONTHLY_QUOTA`).

### Q3. Hook variants количество → **фиксировано 5**

- Стандарт индустрии (как на скриншоте), достаточно для перебора без перегруза UI.
- Prompt в script-gen формирует строго 5 вариантов с typed-полями.
- Если LLM вернёт меньше (редко) — падаем с `script_generation_failed: insufficient_hook_variants`, retry на другой модели.

### Q4. Scene types → **фиксированный enum**

```python
SCENE_TYPES = Literal[
    "talking_head",       # говорящая голова — синий
    "animation",          # анимация — фиолетовый
    "split_screen",       # разделённый экран — оранжевый
    "cutaway",            # перебивка — серо-голубой
    "text_overlay",       # текст — зелёный
    "b_roll",             # b-roll — нейтральный
    "demo",               # демонстрация (товар, экран) — желтоватый
    "reaction",           # реакция (мем, screenshot) — розовый
]
```

- LLM-промпт жёстко указывает: вернуть один из этих 8 ключей. Если модель сочинит свой — fallback на `cutaway`.
- В UI каждый ключ → human label + цвет (mapping в `static/app/index.html`).
- **Преимущество фиксированного enum**: цветовая консистентность, фильтры по типу сцены в будущем, аналитика «процент talking_head в нише».

### Q5. Hook handoff → **отметка + кнопка «Отправить в AI-студию»**

- При клике «Использовать» на варианте N: `POST /scripts/{id}/hook/choose {variant_index: N}`.
- Backend помечает в `script.hook_variants[N].is_chosen=true` + автоматически перегенерирует `script_text`, чтобы он начинался с этой зацепки (`POST /scripts/{id}/regenerate-with-hook`).
- В UI вариант помечается бейджем «Используется», остальные — «Использовать».
- В шапке карточки **«Сценарий для телесуфлёра»** добавляется кнопка `[ → AI-студия ]` — отдельный явный handoff (open studio with prefilled script).
- Никакого автоматического переброса.

### Q6. Edit history → **last 10 версий per artifact**

- Новая таблица `script_versions(id, script_id, artifact_type, version_idx, content_json, created_at, prompt_used)`.
- При каждом transform (`POST /scripts/{id}/transform`): записываем новую версию, удаляем старейшую если `count > 10`.
- В UI на карточке возле `[✎]` появится `[history N]` (если N≥2) → выпадает list с timestamps + diff-preview, можно «Откатить».
- TTL версий = TTL run-а (см. Q1).

### Q7. Язык output → **dropdown в карточке сценария** (per-разбор выбор)

- В карточке «Сценарий для телесуфлёра» сверху-справа dropdown: `[ru | en | es | de | fr ...]`.
- Дефолт: язык из `profile.account.output_language` (если не задан → `ru`).
- Смена → автоматический regenerate сценария + описания + хэштегов (но не editor brief — он остаётся на оригинальном).
- Транскрипт всегда на языке оригинала (никакого перевода).
- Vision-описания кадров — **на языке аккаунта** (LLM-промпт говорит «отвечай на {output_language}»).
- Backend: `POST /scripts/{id}/regenerate {language: "en"}`.

### Q8. Mobile responsive → **4 → 2 → 1 cols** + compact pipeline

- Frame grid:
  - Desktop ≥1024px: 4 колонки
  - Tablet 640-1023px: 2 колонки
  - Mobile <640px: 1 колонка (full-width cards)
- Pipeline:
  - Desktop ≥768px: горизонтальный full с labels
  - Tablet/mobile <768px: горизонтальный compact (только иконки, labels по `tap → tooltip`)
  - Очень узкий <360px: вертикальный (timeline-style)
- Action chips:
  - Desktop: в одну строку wrap
  - Mobile: scrollable horizontal carousel с fade-edges
- Top action buttons (done state): на mobile складываются вертикально.
- Editor brief monospace-блок на mobile получает `overflow-x: auto` (не ломает layout).

---

## 16. Резюме

Текущая моя реализация (3 шага вертикальный Perplexity-стиль) — **выкинуть** и переделать под Piratex.ai-стиль:

- 6 шагов горизонтально
- Прогрессивное раскрытие контента (meta → frames grid → script cards)
- Done-state — multi-card интерфейс с рейтингом, action-chips, edit/copy и free-form transform
- Frame-by-frame vision-анализ с scene-type chips (фиксированный enum из 8 типов, цвет на каждый)
- Multi-artifact script-gen (телесуфлёр + 5 вариантов зацепки + описание + хэштеги + инструкция монтажёру + стратегия)
- Edit history (last 10 versions per artifact, можно откатываться)
- Language switcher в карточке сценария (regenerate сценария+описания+хэштегов; транскрипт всегда оригинал)
- TTL разборов tied to plan (Free 30 / Pro 90 / Business вечно; пока fallback 90 для всех)
- Квоты в profile.accounts (50/мес default, инкремент только при done)
- Hook handoff: отметка + явная кнопка в студию (никаких авто-перебросов)

Backend нужно расширить:
- Granular processor jobs (4 отдельных вместо одного `full-analysis`)
- Vision per-frame structured output (с фиксированным `scene_type` enum)
- Script multi-artifact + transform endpoint + version history
- Profile schema: monthly_razbor_quota, razbor_used_this_month, quota_period_start, output_language
- Cleanup-loop: TTL разборов + сброс месячных квот
- Persistent runs storage + deep-links (`/app/razbor/{run_id}`)

План разбит на 4 фазы по ~2-3 дня каждая. Ровно по принципу Piratex.ai: **«рентген для рилсов»** — детальный, многослойный разбор с возможностью править каждый артефакт.

---

## 17. Сводка решений на одной странице

| # | Вопрос | Решение | Backend impact |
|---|---|---|---|
| Q1 | TTL разборов | tied to plan, fallback 90 дн | новая таблица `razbor_plans` или env, cleanup-loop |
| Q2 | Квоты | profile.accounts.usage_*, +1 при done | миграция profile-store, `POST /accounts/{id}/usage/increment`, `GET /quota` |
| Q3 | Hook variants | строго 5 | LLM prompt enforcement |
| Q4 | Scene types | фиксированный enum из 8 | typed-output validation в vision_analyze |
| Q5 | Hook handoff | отметка + кнопка «→ AI-студия» | `POST /scripts/{id}/hook/choose` + `regenerate-with-hook` |
| Q6 | Edit history | last 10 версий per artifact | таблица `script_versions`, `POST /scripts/{id}/transform` пишет версию |
| Q7 | Язык output | dropdown в карточке (per-разбор) | `POST /scripts/{id}/regenerate {language}` + новое `output_language` в profile |
| Q8 | Mobile | 4→2→1 cols (1024/640 breakpoints), pipeline compact <768 | только CSS media queries |
