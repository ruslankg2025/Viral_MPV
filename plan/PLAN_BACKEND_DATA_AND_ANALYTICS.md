# План: Track A (бэкенд-данные) + Аналитика-Хэштеги

## Контекст

Параллельно с тем как Claude Design рисует прототипы (бриф в `PLAN_AI_STUDIO_DESIGN_BRIEF.md`):
- **Track A** — расширить данные, которые orchestrator сохраняет из processor-а: для покадрового разбора нужен полный массив кадров с timestamp + scene description + thumbnail URL; для транскрипта — timestamped segments. Без этого новая UI будет показывать пустоту.
- **Хэштеги внутрь Аналитики** — убрать отдельный пункт меню, вставить компактный блок над таблицей рилсов.
- **Таблица рилсов** — первые 20 видимы, остальные через scroll, фильтры сверху как у Хэштегов сейчас.
- **Графики** — 30д = точка каждый день; 90д+ = последние 5 недель с якорем на понедельник.

---

## Часть 1 — Track A: расширить данные с processor → orchestrator

### 1.1 Vision: сохранять полный массив кадров

Сейчас [runner.py:298-307](Modules/shell/orchestrator/runs/runner.py) патчит только `frames_count` и `cost_usd`. Processor возвращает массив `frames.extracted[]` с полями `index, timestamp_sec, file_path, diff_ratio` плюс `vision.raw_json` (результат Claude с описанием каждой сцены).

**Что меняем:**
- Из `result.frames.extracted[]` сохранять весь массив, но `file_path` транслировать в публичный URL вида `/api/frames/{job_id}/frame_NNN.jpg` (см. п. 1.3).
- Из `result.vision.raw_json` сохранять полный объект — там описания сцен от Claude.
- Из `result.vision` — `provider`, `model`, `prompt_version` (для отладки и future re-analysis).

**Структура `steps_json["vision"]` после расширения:**
```json
{
  "status": "done",
  "processor_job_id": "abc123",
  "frames_count": 24,
  "frames_dir_url": "/api/frames/abc123",
  "frames": [
    {"index": 1, "timestamp_sec": 0.0, "thumb_url": "/api/frames/abc123/frame_001.jpg", "diff_ratio": 1.0},
    {"index": 2, "timestamp_sec": 3.2, "thumb_url": "/api/frames/abc123/frame_002.jpg", "diff_ratio": 0.42}
  ],
  "analysis": { /* vision.raw_json — описания сцен от Claude */ },
  "provider": "anthropic_claude",
  "model": "claude-sonnet-4-6",
  "cost_usd": 0.0354
}
```

**Файлы:**
- [`Modules/shell/orchestrator/runs/runner.py`](Modules/shell/orchestrator/runs/runner.py) — `_step_vision` (строки 274-321)

### 1.2 Transcribe: сохранять полный текст + segments

Сейчас [runner.py:248-258](Modules/shell/orchestrator/runs/runner.py) сохраняет `transcript_preview` (первые 300 симв) и `words_count`. Processor через Whisper возвращает только монолитный `text` без segments — это НУЖНО исправить в processor-е, иначе на UI не будет тайм-кодов транскрипта.

**Что меняем в processor:**
- В [`Modules/processor/tasks/transcribe.py`](Modules/processor/tasks/transcribe.py) — переключить Whisper-вызов на `response_format="verbose_json"` (OpenAI Whisper) или `timestamp_granularities=["segment"]`. Получим `segments[]` с `id, start, end, text`.
- Сохранять в `result.transcript.segments` массив `{"start": float, "end": float, "text": str}`.

**Что меняем в orchestrator:**
- В `_step_transcribe` сохранять полный `text` (без обрезки), `segments[]`, `language`, `provider`, `model`.

**Структура `steps_json["transcribe"]` после расширения:**
```json
{
  "status": "done",
  "processor_job_id": "xyz789",
  "text": "Полный транскрипт без обрезки...",
  "transcript_preview": "Первые 300 символов...",
  "segments": [
    {"start": 0.0, "end": 2.4, "text": "Привычки номер один."},
    {"start": 2.4, "end": 4.8, "text": "Платить сначала себе."}
  ],
  "language": "ru",
  "words_count": 87,
  "provider": "openai_whisper",
  "model": "whisper-1",
  "cost_usd": 0.00125
}
```

**Файлы:**
- [`Modules/processor/tasks/transcribe.py`](Modules/processor/tasks/transcribe.py) — переключить на verbose_json
- [`Modules/shell/orchestrator/runs/runner.py`](Modules/shell/orchestrator/runs/runner.py) — `_step_transcribe` (строки 223-272)

### 1.3 Раздача frame thumbnails

Processor сохраняет JPG-файлы в `/media/frames/{job_id}/frame_NNN.jpg`. Этот volume нужно сделать доступным через shell.

**Решение**: новый FastAPI-endpoint в shell с защитой от path traversal:

```
GET /api/frames/{job_id}/{filename}
GET /api/audio/{job_id}.mp3
```

С валидацией:
- `job_id` — только `[a-f0-9]{32}`
- `filename` — только `frame_\d+\.jpg`
- Использовать `FileResponse` с правильным Content-Type

**Где монтируется `/media`:** в `docker-compose.yml` shell-сервис должен иметь `volumes: - media:/media` (как у processor-а). Проверить и добавить если нет.

**Файлы:**
- [`Modules/shell/main.py`](Modules/shell/main.py) — добавить роутер `media_router`
- Новый файл `Modules/shell/media/router.py` с endpoint
- `docker-compose.yml` — проверить volume mount media для shell

### 1.4 Тесты для Track A

- `Modules/processor/tests/test_transcribe.py` — мок OpenAI, проверить что `verbose_json` парсится в `segments[]`
- `Modules/shell/tests/test_orchestrator_runner.py` — мок processor.wait_done, проверить что `_step_vision` сохраняет полный `frames[]` массив, `_step_transcribe` сохраняет `segments[]` и `text`
- `Modules/shell/tests/test_media_router.py` — path-traversal проверка (`../etc/passwd` 400)

---

## Часть 2 — Слияние Хэштегов в Аналитику

### 2.1 Удалить пункт меню

Файл [`Modules/shell/static/app/index.html`](Modules/shell/static/app/index.html):
- Строка 851: удалить `<button class="nl" data-view="hashtags" onclick="go('hashtags')">Хэштеги</button>`
- В `go()` (~строка 1364) убрать `'hashtags'` из массива sections (если перечисляются явно)
- В `goS()` (~строка 1382) — то же
- Секцию `<section id="v-hashtags">` (строки 1024+) **оставить** для обратной совместимости — но сделать невидимой (или удалить).

### 2.2 Компактный блок хэштегов в Аналитике

В [`renderAnalytics()`](Modules/shell/static/app/index.html) (строка 1632) после блока с топ-хэштегами и сводкой, перед таблицей рилсов вставить полную таблицу хэштегов в свёрнутом виде:

```html
<div class="ana-card ana-hashtags-mini">
  <div class="ana-card-h">
    <span class="ana-card-title">Хэштеги</span>
    <div class="ht-controls-mini">
      <input id="ana-ht-q" placeholder="поиск #тега">
      <select id="ana-ht-sort">
        <option>по count</option><option>↑ за неделю</option>
        <option>по просмотрам</option><option>по ER</option>
      </select>
    </div>
  </div>
  <div id="ana-ht-table"><!-- 10 строк, scroll если больше --></div>
</div>
```

**Логика**: при выборе автора в Аналитике дополнительно делать fetch на `/hashtags?account_id={author}&days={range}&sort={ana-ht-sort}&limit=50`, рендерить через адаптированный `renderHashtagsTable()` (без drill-down или с компактным).

**JS**: новая функция `renderAnalyticsHashtags(account_id, days, sort, q)`. Реиспользует существующий `loadHashtags`-код но рендерит в `#ana-ht-table` вместо `#ht-body`.

**Файлы**: всё в `index.html`.

---

## Часть 3 — Таблица рилсов: первые 20 + scroll + фильтры

Сейчас в [`renderAnalytics()`](Modules/shell/static/app/index.html) после графиков рендерится "Сводная статистика по рилсам" — большая таблица без фильтров.

**Что меняем:**
- Контейнер таблицы: `max-height: 480px; overflow-y: auto;` — сразу видны первые ~20 строк (по 24px каждая = 480px)
- Sticky header через `position:sticky; top:0;`
- Над таблицей — фильтр-бар:
  - Поиск по тексту/title (debounced 300ms)
  - Сортировка: по дате / просмотрам / ER / velocity (toggle ASC/DESC)
  - Фильтр-бейдж "только HIT" (velocity > X)
  - Кнопка "Экспорт CSV" — уже есть `exportReelsToCSV()`
- Все фильтры в localStorage (как у hashtags), ключ `viral:ana-reels-filters`

**HTML-структура:**
```html
<div class="ana-card ana-reels-summary">
  <div class="ana-card-h">
    <span class="ana-card-title">Сводная по рилсам</span>
    <div class="rl-controls">
      <input id="ana-rl-q" placeholder="поиск по тексту/автору">
      <select id="ana-rl-sort">...</select>
      <button id="ana-rl-hit">только HIT</button>
      <button onclick="exportReelsToCSV()">CSV</button>
    </div>
  </div>
  <div class="ana-rl-scroll">
    <table>
      <thead style="position:sticky;top:0;background:var(--bg2)">...</thead>
      <tbody id="ana-rl-tbody">...</tbody>
    </table>
  </div>
</div>
```

**JS-функция**: `renderAnalyticsReelsTable()` — фильтрует/сортирует `stats.rows` локально (без перевызова бэкенда), рендерит tbody.

**Файлы**: только `index.html`.

---

## Часть 4 — Графики: 30д = точка/день, 90д+ = 5 недель по Пн

### Логика

Текущие графики (followers + ER trend):
- [`renderFollowersChart`](Modules/shell/static/app/index.html) (~строка 1756) — рисует все snapshots как точки, без агрегации
- [`renderErTrendChart`](Modules/shell/static/app/index.html) (~строка 1803) — рисует bucket-ы как точки (бэкенд агрегирует)

Новое поведение:
| Период (`#ana-range`) | Followers chart | ER trend chart |
|---|---|---|
| **30 дней** | точка каждый день из snapshots (≤30 точек) | granularity=day, ≤30 точек |
| **90 / 180 / 365 дней** | последние 5 понедельников (5 точек), значение = последний snapshot до этого Пн | granularity=week, последние 5 buckets с anchor=Mon |

### Реализация frontend

В `loadAnalytics()` (~1537):
- Если `days <= 30` → fetch `/profile-snapshots?days={days}` и `/er-trend?days={days}&granularity=day`
- Если `days > 30` → fetch `/profile-snapshots?days={days}` и `/er-trend?days={days}&granularity=week`

В `renderFollowersChart(snapshots, days)`:
- Если `days <= 30` — рисовать все точки
- Иначе — клиентское бакетирование: для каждого из последних 5 понедельников найти ближайший предыдущий snapshot, нарисовать 5 точек

Псевдокод хелпера:
```javascript
function _last5MondaysSnapshots(snapshots) {
  const today = new Date();
  // ближайший предыдущий понедельник (или сегодня если Пн)
  const lastMon = new Date(today);
  lastMon.setHours(0,0,0,0);
  const dow = (lastMon.getDay() + 6) % 7; // 0=Mon..6=Sun
  lastMon.setDate(lastMon.getDate() - dow);
  const points = [];
  for (let i = 4; i >= 0; i--) {
    const target = new Date(lastMon);
    target.setDate(target.getDate() - 7*i);
    // найти snapshot с date <= target, ближайший
    const snap = [...snapshots].reverse().find(s => new Date(s.date) <= target);
    if (snap) points.push({date: target.toISOString().slice(0,10), followers: snap.followers});
  }
  return points;
}
```

В `renderErTrendChart(buckets, days)`:
- Если `days > 30` — отрезать только последние 5 buckets (бэкенд уже агрегирует по неделям)
- Если `days <= 30` — нужно убедиться что бэкенд поддерживает `granularity=day`. Если нет — добавить.

### Backend проверка

[`Modules/monitor/router.py`](Modules/monitor/router.py) `/er-trend` — есть ли поддержка `granularity=day`? Если только `week` — добавить `day` (тривиальное расширение SQL `GROUP BY date(published_at)` вместо `strftime('%Y-%W', ...)`).

**Файлы**:
- `index.html` — хелпер `_last5MondaysSnapshots`, изменения в `renderFollowersChart` и `loadAnalytics`
- Возможно `Modules/monitor/router.py` если `granularity=day` отсутствует

---

## Порядок исполнения

1. **Часть 1.2 + 1.3** (бэкенд: full transcript + verbose Whisper + media routes) — не ломает текущий UI, только обогащает данные
2. **Часть 1.1** (full vision frames) — то же
3. **Часть 1.4** (тесты)
4. **Часть 2** (Хэштеги в Аналитику) — чисто фронт
5. **Часть 3** (таблица рилсов с фильтрами) — чисто фронт
6. **Часть 4** (графики 5 недель) — чисто фронт

Шаги 4-6 идут после возврата прототипа от Claude Design (если он повлияет на дизайн карточек).

---

## Верификация

### Track A
- Запустить разбор: `curl -XPOST localhost:8000/api/orchestrator/runs -d '{"video_id":"..."}'`
- После done: `curl localhost:8000/api/orchestrator/runs/{run_id}` — в `steps.vision.frames` должен быть массив с timestamp/thumb_url, в `steps.transcribe.segments` — массив с start/end/text
- Открыть `localhost:8000/api/frames/{job_id}/frame_001.jpg` — должна вернуться картинка
- В UI пока ничего не видно (UI обновим после получения макета от Claude Design)

### Аналитика
- Открыть `/app/` → Аналитика → выбрать автора с >2 неделями активности
- Над таблицей рилсов — блок хэштегов с поиском и сортировкой
- Таблица рилсов — sticky header, scroll если >20 строк
- Сменить период с 30 на 90 — на графике followers становится 5 точек по понедельникам
- Меню — нет пункта "Хэштеги"
