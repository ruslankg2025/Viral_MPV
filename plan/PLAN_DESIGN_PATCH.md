# PATCH: апдейт брифа для Claude Design — VIRAL_MPV

> Это **дополнение** к основному брифу `plan/PLAN_AI_STUDIO_DESIGN_BRIEF.md` (он же в `~/.claude/plans/`). Прочитай его сначала, потом этот патч — он сужает скоп и обновляет дизайн-систему.

## Контекст

Пока ты рисовал прототип, мы реализовали часть фронта своими руками (страницы Аналитика и Хэштеги). Поэтому скоп сузился — в брифе есть пункты, которые **больше не нужны**, и есть пункты, которые становятся **важнее**.

---

## ❌ Что УЖЕ СДЕЛАНО — не рисуй

### 1. Отдельная страница "Хэштеги" — УДАЛЕНА из проекта
- Кнопка "Хэштеги" убрана из навигации
- Секция `<section id="v-hashtags">` снесена из `Modules/shell/static/app/index.html`
- Все JS-функции (`initHashtags`, `loadHashtags`, `renderHashtagsTable`, `expandTag`, ...) удалены
- Хэштеги теперь живут как **встроенный блок внутри Аналитики**
- → Если бриф упоминал страницу Хэштеги — игнорируй, её больше не существует

### 2. Таблица рилсов в Аналитике — УЖЕ РЕАЛИЗОВАНА
Карточка "Сводная по рилсам" в Аналитике уже имеет:
- Sticky-header с прокруткой (max-height 540px)
- Фильтр-бар: поиск по тексту, сортировка (новые/старые/просмотры/ER/velocity), кнопка "только HIT", CSV-экспорт
- HIT-строки с оранжевой полоской слева (velocity ≥ 10K/ч)
- → Эту таблицу **не перерисовывай** — текущая реализация финальная

### 3. Графики Аналитики — УЖЕ ОБНОВЛЕНЫ
- 30 дней → точка каждый день
- 90+ дней → последние 5 понедельников (snapshot ≤ Пн)
- ER trend: granularity=day для ≤30 дн, granularity=week для >30 дн
- → Графики не трогаем

---

## 🎯 Что ОСТАЁТСЯ В ФОКУСЕ — на этом сосредоточься

### A. AI-студия с вкладкой "Загрузка" (главная задача)
Полностью как в исходном брифе:
- 5 табов: Все · 📥 Загрузка · ⚙ В обработке · ✓ Готовые · 🚀 Размещённые
- Вкладка "Загрузка" — сетка карточек разобранных видео (4 кол на десктопе)
- Карточка: 9:16 thumbnail + INSTAGRAM/YT/TT badge + title + author + ER/views/likes/velocity + кнопка "Скопировать" + bookmark
- Клик "Скопировать" → анимированный переход в "В обработке" с pipeline-индикатором + стриминг материалов

### B. Покадровый разбор (новая важная секция результата "Разбор")
Полностью как в брифе:
- Сетка карточек 4 кол, aspect-ratio 9:16
- Тайминги: первый кадр 00:00 (длится 3с), дальше каждые 4с (00:03, 00:07, 00:11...)
- Каждая карточка: timestamp + scene-tag + thumbnail + текст-на-экране + визуал-описание + теги объектов
- 5 цветов scene-тегов: голубой/янтарный/серый/фиолетовый/teal

### C. Транскрипт (после кадров)
Бокс с заголовком "Транскрипт (N)" + кнопка "Копировать" + строки `0:00 текст`. Уже в брифе.

---

## 🎨 Уточнения к дизайн-системе (после рефакторинга)

В нашем коде сейчас живут эти стилевые паттерны — **держись их**, потому что портирование пойдёт назад в `index.html`:

```css
/* Mini-controls внутри ana-card-h (новые) */
.ana-mini-inp{background:var(--bg2);border:1px solid var(--br2);border-radius:8px;padding:5px 10px;font-size:12px;color:var(--t1);min-width:140px}
.ana-mini-sel{background:var(--bg2);border:1px solid var(--br2);border-radius:8px;padding:5px 10px;font-size:12px;color:var(--t1);cursor:pointer}
.ana-mini-btn{background:transparent;border:1px solid var(--br2);color:var(--t2);font-family:var(--mono);font-size:10.5px;padding:5px 11px;border-radius:8px;letter-spacing:.05em}
.ana-mini-btn.on{background:#2a1108;border-color:#fb923c;color:#fb923c}
.ana-mini-btn.primary{color:var(--teal-hi);border-color:color-mix(in oklab,var(--teal) 35%,var(--br2))}

/* Sticky-table паттерн (для табов "Загрузка" / "В обработке") */
.scroll-container{max-height:540px;overflow-y:auto;border:1px solid var(--br);border-radius:8px}
.scroll-container::-webkit-scrollbar{width:4px}
.scroll-container::-webkit-scrollbar-thumb{background:var(--br2);border-radius:2px}
table thead th{position:sticky;top:0;background:var(--bg2);z-index:2;border-bottom:1px solid var(--br2)}

/* HIT-индикатор (для маркеров выдающихся видео) */
tr.is-hit td:first-child{position:relative}
tr.is-hit td:first-child::before{content:'';position:absolute;left:0;top:0;bottom:0;width:2px;background:#fb923c}

/* Pipeline-индикатор для "В обработке" — в Phase A pipeline уже есть */
@keyframes circForge{
  0%,100%{box-shadow:0 0 0 4px color-mix(in oklab,var(--teal) 12%,transparent),0 0 28px color-mix(in oklab,var(--teal) 45%,transparent)}
  50%{box-shadow:0 0 0 6px color-mix(in oklab,var(--teal) 18%,transparent),0 0 40px color-mix(in oklab,var(--teal) 65%,transparent)}
}
```

**Иконки:** заменили все emoji-иконки в pipeline на чистые inline SVG (stroke 1.6, viewBox 16). В новых компонентах — тот же подход:
```html
<!-- Скачивание -->
<svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2v8.5M4.5 7l3.5 3.5L11.5 7M3 13.5h10"/></svg>
<!-- Транскрипция -->
<svg ...><path d="M3 4h10M3 7h7M3 10h10M3 13h6"/></svg>
<!-- Анализ кадров -->
<svg ...><rect x="2.2" y="3.5" width="11.6" height="9" rx="1.2"/><line x1="2.2" y1="6.5" x2="13.8" y2="6.5"/><line x1="2.2" y1="9.5" x2="13.8" y2="9.5"/><line x1="5.5" y1="3.5" x2="5.5" y2="12.5"/><line x1="10.5" y1="3.5" x2="10.5" y2="12.5"/></svg>
```

---

## 📂 Файлы для прочтения (у тебя есть доступ)

Читай в этом порядке:

1. **`plan/PLAN_AI_STUDIO_DESIGN_BRIEF.md`** — основной бриф (60% актуален)
2. **`plan/PLAN_BACKEND_DATA_AND_ANALYTICS.md`** — что доступно из API после backend Track A
3. **`Modules/shell/static/app/index.html`** — текущий фронт. Особенно посмотри:
   - Строки 549-580: дизайн-токены analytics (`.ana-card`, `.ana-table`, `.ana-mini-*`)
   - Строки 154-260: pipeline CSS (`.rz-status-line`, `.rzh-ico`, `.rz-frames`, эффект `circForge`)
   - Строка 1632: текущая `renderAnalytics()` — стиль hero/card/grid
   - Строка 2400-ish: `RZ_STEPS` — формат step config

4. **Дамп данных run-а** для покадрового разбора (бекенд уже сохраняет это после Track A):
```json
{
  "id": "run-uuid",
  "status": "done",
  "video_meta": {"title": "...", "thumbnail_url": "...", "platform": "instagram", "external_id": "..."},
  "steps": {
    "vision": {
      "frames_count": 18,
      "frames": [
        {"index": 1, "timestamp_sec": 0.0, "diff_ratio": 1.0, "thumb_url": "/api/media/frames/{job_id}/frame_001.jpg"},
        {"index": 2, "timestamp_sec": 3.2, "diff_ratio": 0.42, "thumb_url": "/api/media/frames/{job_id}/frame_002.jpg"}
      ],
      "analysis": { /* raw_json от Claude — описания сцен */ }
    },
    "transcribe": {
      "text": "полный транскрипт",
      "language": "ru",
      "words_count": 87,
      "segments": [
        {"start": 0.0, "end": 1.8, "text": "Привычки номер один."},
        {"start": 1.8, "end": 4.0, "text": "Платить сначала себе."}
      ]
    }
  }
}
```

---

## ✅ Что нужно отдать

Один HTML-файл (как в исходном брифе), но **только** с тремя экранами:

1. **AI-студия "Загрузка"** + переключение в "В обработке" по клику "Скопировать"
2. **Результат "Разбор"** — секция "Покадровый разбор · 18" с 12 mock-карточками кадров (тайминги 0:00, 0:03, 0:07...)
3. **Транскрипт** — бокс с 18 строками `start text` (берись за mock-данные про "Привычки миллионеров" — они в основном брифе)

Навигация — 5 табов (без "Хэштеги"). Mock-данные — реалистичные русские.

**Не рисуй**: Аналитику, страницу Хэштеги, Монитор, Разбор-pipeline (он уже сделан).

---

## 🚦 Запуск

После прототипа — я портирую CSS/HTML в `Modules/shell/static/app/index.html`, секции:
- AI-студия → внутрь `<section id="v-studio">` (~строка 815)
- Покадровый разбор → между `<div id="rz-frames">` и `<div id="rz-result">` (~строка 770)
- Транскрипт → внутрь `<div id="rz-result">` рендера

Backend готов: данные `frames[]` и `segments[]` уже летят в run-объекте, медиа-роут `/api/media/frames/...` отдаёт JPG.
