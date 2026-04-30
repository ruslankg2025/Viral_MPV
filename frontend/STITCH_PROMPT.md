# Google Stitch Prompt — Viral Monitor

Design a dark-theme SaaS web application for analyzing viral video content and generating scripts for content creators. The tool is called **"Viral Monitor"**. All UI text is in **Russian**. The app has 5 screens.

---

## Global Design System

**Theme:** Dark mode only.

**Colors:**
- Page background: `#0a0a0a`
- Card / panel background: `#111111`
- Input / nested background: `#1a1a1a`
- Border (default): `#2a2a2a`
- Border (accent/hover): `rgba(229, 184, 0, 0.3)`
- Text primary: `#e5e5e5`
- Text secondary: `#a0a0a0`
- Text muted: `#666666`
- Accent (gold/amber): `#e5b800` — used sparingly for: active nav, primary CTA buttons, progress indicators, badges
- Success: `#22c55e`
- Error: `#ef4444`
- Warning/Processing: `#f59e0b`

**Typography:**
- Font family: Inter (400, 500, 600, 700)
- Monospace: JetBrains Mono (for timestamps, URLs, code)
- Base size: 14px body, 13px secondary, 12px muted
- Headings: 32px hero, 22px page title, 16px section title

**Spacing:** Base-4 system: 4, 8, 12, 16, 24, 32px

**Border radius:** 6px (buttons, badges), 8px (cards, inputs), 12px (large cards, modals)

**Shadows:** Minimal. Only on floating elements (dropdowns, modals): `0 4px 16px rgba(0,0,0,0.3)`

**Layout:**
- NO sidebar. Horizontal top navigation bar only.
- Max content width: 1200px, centered horizontally.
- Responsive: works on desktop (1440px), tablet (768px), and mobile (375px).

---

## Top Navigation Bar (all pages)

Horizontal bar, height 56px, background `#0a0a0a`, bottom border `1px solid #2a2a2a`.

**Left:** Logo icon (lightning bolt, amber) + text "Viral Monitor" (16px, bold, white)

**Center:** 3 navigation tabs:
- **"Разбор"** (Analysis) — main entry point
- **"Мои видео"** (My Videos) — analyzed video history
- **"Тренды"** (Trends) — monitored channels + trending

Active tab: amber text + amber bottom border (2px). Inactive: secondary gray text.

**Right:** Settings gear icon (gray, 20px) + user avatar circle (32px, amber border)

---

## Screen 1: "Разбор" (Analysis Landing) — route `/analyze`

**Purpose:** Single-action page. User pastes a video URL and gets full AI analysis.

**Layout:** Centered content, vertically centered in viewport.

**Content (top to bottom):**

1. **Subtitle:** "РЕНТГЕН ДЛЯ ВИРУСНЫХ ВИДЕО" — uppercase, 11px, letter-spacing 3px, muted text, centered

2. **Hero heading:** "Разберите любое видео" — 40px, bold, white, centered

3. **Description:** "Вставьте ссылку и получите покадровый разбор, транскрипцию и готовый сценарий для адаптации" — 15px, secondary text, centered, max-width 520px

4. **Input row (centered, max-width 600px):**
   - Large text input: placeholder "Вставьте ссылку на видео (YouTube, Instagram, TikTok)", height 48px, bg `#1a1a1a`, border `#2a2a2a`, border-radius 8px, font 14px
   - Button "Разобрать" — amber background `#e5b800`, black text, font-weight 700, height 48px, padding 0 24px, border-radius 8px, attached to input right side

5. **Step indicators (3 items in a row, centered, gap 48px):**
   - Each step: circle icon (32px, border `#2a2a2a`, muted icon inside) + label below (12px, muted)
   - Steps connected by horizontal dashed lines between circles
   - Step 1: upload icon + "Вставьте ссылку"
   - Step 2: clock icon + "ИИ анализирует"
   - Step 3: file-text icon + "Готовый сценарий"

6. **Footer:** Copyright text, legal links (12px, muted), at page bottom

---

## Screen 2: Analysis Result — route `/analyze/:jobId`

**Purpose:** Shows full analysis of a video: progress during processing, then frame breakdown, generated script, and transcript.

**Layout:** Single column, max-width 1000px, centered.

### Section A: Video Info Bar (top)

Horizontal card, bg `#111111`, border `#2a2a2a`, border-radius 8px, padding 16px.

**Content (left to right):**
- Small vertical thumbnail (80px wide, 9:16 aspect ratio, border-radius 6px)
- Video title (14px, bold, white, max 2 lines with ellipsis)
- Platform badge (small colored pill: YouTube red, Instagram pink, TikTok cyan)
- Metrics in a row: "views" icon + count, "heart" icon + count, "message" icon + count (13px, secondary)
- Author: "@username" (13px, muted)

### Section B: Progress Pipeline (shown during analysis, collapses when done)

Horizontal pipeline with 6 connected steps, centered.

```
[Download] ── [Audio] ── [Transcribe] ── [Frames] ── [Vision] ── [Script]
     ✓           ✓          ●──75%          ○          ○          ○
```

Each step:
- Circle (28px diameter): completed = amber fill + white checkmark, active = amber border + pulsing amber dot, pending = gray border + gray dot
- Label below circle (11px, muted when pending, white when active/done)
- Percentage text below active step (12px, amber)
- Connecting line: amber solid for completed, gray dashed for pending

### Section C: Frame Breakdown

**Heading:** "ПОКАДРОВЫЙ РАЗБОР — 08" (section title, 14px, uppercase, letter-spacing 2px, amber accent for the number)

**Grid:** 4 columns × 2 rows of frame cards. Each card:
- Frame image (square-ish, border-radius 6px, aspect-ratio approximately 9:16 but cropped to 1:1 for grid)
- Timestamp overlay on frame bottom-left (pill badge: "00:16", 10px, bg rgba(0,0,0,0.7))
- Below frame:
  - "ТЕКСТ НА ЭКРАНЕ:" (11px, uppercase, muted) + actual text (13px, white)
  - "АНАЛИЗ:" (11px, uppercase, muted) + analysis text (13px, secondary)
  - Small copy icon button (bottom-right of text area)

### Section D: Script Tabs

**Tab bar:** 4 tabs, horizontal, with icons:
- 📝 "Текст для телесуфлёра" (active by default)
- 📋 "Описание для публикации"
- 📖 "Инструкция для монтажника"
- 📈 "Стратегия продвижения"

Active tab: amber text + amber bottom border. Inactive: secondary text.

**Tab 1 — Teleprompter:**
- Large textarea, bg transparent, border none, font 15px Inter, line-height 1.8, color white
- Copy button in top-right corner (gray icon, shows "Скопировано!" tooltip on click)
- Below textarea: **Improvement bar** — row of 5 pill buttons:
  - "Усилить зацепку" / "Сократить" / "Добавить конкретики" / "Упростить" / "Сделать энергичнее"
  - Each pill: bg `#1a1a1a`, border `#2a2a2a`, 12px text, border-radius 20px, hover: amber border
- Below pills: chat-style input "Свой промпт для доработки..." + send button (amber arrow)

**Tab 2 — Description:**
- Formatted text block with reel description
- Hashtag chips at bottom (each chip: bg `rgba(229,184,0,0.1)`, border `rgba(229,184,0,0.2)`, amber text, border-radius 20px)
- Copy button

**Tab 3 — Instructions:**
- Numbered list of scenes (1, 2, 3, ...)
- Each scene: scene number badge (amber circle, 20px) + scene description + visual hint text (in italic, secondary color)
- Duration estimate per scene (muted, right-aligned)

**Tab 4 — Strategy:**
- "Почему это видео стало вирусным" — bold heading
- Ordered list of reasons with numbered amber badges
- "Ключевой инсайт" — highlighted quote block (left amber border, bg `rgba(229,184,0,0.05)`, italic text)
- Tags section: technique tags as pills (amber outline)

### Section E: Transcript

**Heading:** "ПРАКТИЧЕСКИЙ ТРАНСКРИПТ" (section title)

**Timestamp toggle:** "Показать таймкоды" toggle switch

**Transcript lines:**
- Each line: timestamp (amber, monospace, 12px, left column 60px wide) + text (white, 14px, right column)
- Example: `0:00` · "Скажите, почему 80% людей теряют деньги на фондовом рынке?"
- Alternating subtle bg stripes for readability (every other line: bg `rgba(255,255,255,0.02)`)

---

## Screen 3: "Мои видео" (My Videos) — route `/my-videos`

**Purpose:** Grid of previously analyzed videos.

**Layout:** Full width (within 1200px max), centered.

**Header row:**
- Page title "Мои разборы" (22px, bold)
- Right side: amber CTA button "Новый разбор" (links to /analyze)

**Filter pills row:**
- "Все" / "Готовые" / "В обработке" / "Ошибка"
- Active pill: amber bg, black text. Inactive: bg `#1a1a1a`, secondary text

**Video grid:** `repeat(auto-fill, minmax(220px, 1fr))`, gap 16px

**Each video card (vertical, 9:16 emphasis):**
```
┌──────────────────────────┐
│                          │
│   [Thumbnail 9:16]       │  bg #1a1a1a if no image
│                          │
│   Platform badge ──┐     │  top-left corner overlay
│   Status badge  ───┘     │  top-right corner overlay
│   Duration "1:15" ──     │  bottom-right on thumbnail
│                          │
├──────────────────────────┤
│ Video title (2 lines)    │  14px, bold, white
│ @authorname              │  12px, muted
│ 136K views · 2 дн. назад │  12px, secondary
│                          │
│ [ER: 5.7%] [CR: 3.2%]   │  metric pills (colored)
│                          │
│ [Разобрать]              │  text button, amber, 13px
└──────────────────────────┘
```

Status badge styles:
- "Готово" — green bg, white text, 10px
- "Анализирую" — amber bg with pulse animation, black text
- "Ошибка" — red bg, white text

---

## Screen 4: "Тренды" (Trends) — route `/trends`

**Purpose:** Monitor channels and discover trending videos.

**Layout:** Two panels on desktop, stacked on mobile.

### Left Panel (width 320px on desktop, full width on mobile)

**Header:** "Источники" (16px, bold) + "+ Добавить" button (amber outline, small)

**Search input:** "Поиск по авторам..." (full width, height 36px)

**Source list (vertical scroll):**
Each source card (padding 12px, border-bottom `#2a2a2a`):
- Platform icon (16px, colored) + Channel name (14px, bold)
- Last crawled: "2 ч. назад" (12px, muted)
- Status dot: green (active) or gray (paused)
- Hover: bg `#1a1a1a`
- Click: filters right panel to this source

**Bottom:** "Обновить все" button (secondary style, full width)

### Right Panel (flex: 1)

**Header:** "Тренды за 24ч" (16px, bold) + time window selector pills: "24ч" / "7д" / "30д"

**Category filter row:** Horizontal scrollable pills: "Все" / "Бизнес" / "Лайфстайл" / "Финансы" / "Развлечения" / etc.

**Trending video grid:** Same vertical card style as My Videos, but with additional z-score and growth data:
- Z-score badge: amber bg pill, "z=3.2" text
- Growth rate: green arrow up icon + "+45%" or red arrow down + "-12%"
- Sort: by z-score descending (highest virality first)

### Add Source Modal (opened by "+ Добавить" button)

Modal dialog, centered, bg `#111111`, border `#2a2a2a`, border-radius 12px, padding 32px, width 400px.

- Title: "Добавить автора" (18px, bold)
- Input: "Ссылка или @username" (full width, height 44px)
- Platform selector: 4 icon buttons in a row (YouTube / Instagram / TikTok / VK), selected = amber border
- Submit button: "Добавить автора" (amber, full width, height 44px)

---

## Screen 5: Settings — route `/settings`

**Purpose:** Configure service tokens, user profile, brand voice, audience, and AI provider keys.

**Layout:** Max-width 720px, centered. Tab navigation at top.

**Tabs:** "Токены" / "Профиль" / "Brand Book" / "Аудитория" / "AI Ключи"

### Tab 1: Токены (Service Tokens)

4 input fields, each with:
- Label (13px, bold): "Processor Token", "Script Token", "Monitor Token", "Profile Token"
- Text input with show/hide toggle button (eye icon)
- "Проверить" verification button (secondary, right side) — shows green checkmark or red X after test
- Description text below (12px, muted): "Токен для подключения к сервису обработки видео"

### Tab 2: Профиль (Profile)

- Name input: "Имя аккаунта" (full width)
- Niche dropdown: "Выберите нишу" with categories from taxonomy
- Description textarea: "Обо мне и аудитории" (height 120px)
  - Hint below: "Опишите себя, свой канал и целевую аудиторию. ИИ будет использовать этот контекст при генерации сценариев."
- Topic chips: Label "Темы генерации" + chip input area
  - Existing chips: colored pills with X button to remove
  - Input to add new chips
  - Example chips: "Финансы 💰", "Бизнес 📊", "Лайфстайл 🌍"
- Save button (amber, bottom right)

### Tab 3: Brand Book

**Tone sliders (4 horizontal sliders):**
Each slider: Label on left (13px, bold) + value display on right (amber number) + full-width slider
- "Формальность" (1-10)
- "Энергичность" (1-10)
- "Юмор" (1-10)
- "Экспертность" (1-10)
Slider track: bg `#2a2a2a`, filled portion: amber. Thumb: white circle, 16px.

**Forbidden words:** Label + chip input
- Add input with plus button
- Existing words as gray pills with X button

**CTA шаблоны:** Same chip input pattern
- Example: "Подписывайтесь!", "Ссылка в описании"

**Save button** (amber)

### Tab 4: Аудитория (Audience)

Form fields:
- "Возраст": input "25-35"
- "География": input "Россия, СНГ"
- "Пол": radio group — "Мужской" / "Женский" / "Смешанный"
- "Уровень экспертности": radio group — "Новичок" / "Средний" / "Эксперт"
- "Боли аудитории": chip input (add/remove pain points)
- "Желания аудитории": chip input (add/remove desires)
- Save button (amber)

### Tab 5: AI Ключи (API Keys)

**Provider status bar:** Horizontal row of provider indicators
- Each: colored dot (green=active, gray=inactive) + provider name (12px)
- Providers: Anthropic, OpenAI, Gemini, AssemblyAI, Deepgram, Groq

**Keys table:**
| Provider | Label | Status | Monthly Limit | Used | Actions |
Each row: provider name, custom label, active/inactive badge, $limit, $used of $limit progress bar, edit/delete icons

**"Добавить ключ" button** (amber outline) — opens form:
- Provider dropdown
- Label input
- Secret input (password-masked)
- Monthly limit input ($)
- Priority number input
- Submit button

---

## Interaction Patterns

**Loading states:** Use skeleton loaders (shimmer animation) for cards, pulse animation for progress steps
**Error states:** Red border on card, error icon + message text
**Empty states:** Dashed border container, muted icon (48px), description text, CTA button
**Hover effects:** Cards: border color transitions to `rgba(229,184,0,0.2)`. Buttons: slight brightness increase
**Transitions:** All 150ms ease-out
**Toasts:** Slide in from top-right, auto-dismiss 4s, 4 variants (success/error/warning/info)
