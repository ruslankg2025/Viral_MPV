# Plan: Monitor Activation (ревизия после critique Агента 2)

## Context

Два требования: (1) чистый floating-nav без просвечивания контента при скролле, (2) Монитор-таб показывает реальные ролики реальных авторов из Apify вместо захардкоженного мок-массива.

Первая оценка «1-2 часа» оказалась заниженной. Critique вскрыл:
- 4 из 6 фильтров на Monitor-табе **не реализованы**: `setSeg/setSrt/setSN/setTR` только CSS, `rT()` не дёргают.
- `TrendingItem` НЕ отдаёт `platform`, `niche_slug`, `thumbnail_url`, `likes`, `comments` — без этих полей карточки с мок-шапкой не построить.
- Алгоритм trending в `analytics/trending.py` плох для цели «поймать идею в моменте»: окно 7 дней (IG виралы пикуют за 24-72ч), z-score только within-channel, нет velocity, `views_24h_ago` фактически != 24h.
- Нет UI контроля расходов. Apify native controls (caps, alerts) — ответственность юзера в console.apify.com, **обязателен preflight до `MONITOR_FAKE_FETCH=0`**.

План разбит на 3 пасса, каждый — независимо работающий инкремент.

## Part A — nav buffer ~1см (5 минут, перед Pass 1)

**[Modules/shell/static/app/index.html:41-47](Modules/shell/static/app/index.html#L41-L47)**

```css
/* было: padding:22px 28px 10px; margin:0 -28px 6px; */
padding:40px 28px 40px;
margin:0 -28px 0;
```

## Pass 1 — Реальные данные видны (~3 часа, эта сессия)

### 1.1 Backend: `TrendingItem` расширить

**[Modules/monitor/schemas.py](Modules/monitor/schemas.py)** — добавить в `TrendingItem`:
- `platform: Platform` — для иконки
- `niche_slug: str | None` — для фильтра ниш на клиенте
- `thumbnail_url: str | None` — для превью в карточке
- `likes: int`, `comments: int` — для опциональных метрик
- `channel_external_id: str` — для отображения `@handle`
- `hours_since_published: float | None` — готово к отображению

**[Modules/monitor/router.py](Modules/monitor/router.py)** — `list_trending` + `get_trending_detail`: заполнить эти поля из уже загруженных `video`/`source`/`latest_snapshot`. `hours_since_published` — ISO parse `published_at` → разница в часах.

### 1.2 UI: async `rT()` на реальных данных

**[Modules/shell/static/app/index.html:805](Modules/shell/static/app/index.html#L805)** — удалить `const R=[...]`, заменить на `let trendingVideos = []`.

**[:926](Modules/shell/static/app/index.html#L926)** — переписать `rT()` async:
- `fetch /api/monitor/trending?account_id=X&limit=50`
- применить client-side фильтры (niche через `NICHE_BY_EN[curN].slug === video.niche_slug`, sort, search)
- render cards с thumbnail_url (или fallback-плейсхолдер), platform icon, views, ago, zscore badge

### 1.3 Fix 4 сломанных handlers

**[:932](Modules/shell/static/app/index.html#L932)** — добавить `rT()` в конец каждого: `setSeg`, `setSrt`, `setSN`, `setTR`. Вся логика фильтрации — внутри `rT()`.

- **Segment** «Для вас / Мои авторы / Все» — в MVP все три маппятся на `/trending?account_id=X`. Tooltip/disabled на «Для вас» (recommendation не реализован).
- **Time-range** — «Всё время», «Сегодня», «Неделя» работают в 7-дневном окне бэкенда. «Месяц»/«Год» — показать hint «доступно с Pass 2» или отключить.
- **Platform filter** — локально по `video.platform`.
- **Sort** — локально по `current_views` / `zscore_24h` / `published_at`.

### 1.4 Trigger crawl из UI

В `renderAuthors` — в строке автора **новая кнопка** «▶ Обновить»:
- `onclick` → `await apiMon('POST', '/sources/{id}/crawl')`
- disabled + spinner во время запроса (до 180с таймаут)
- обработка: `apify_token_invalid`, `channel_not_found`, таймаут
- после завершения: `loadAuthors()` + `rT()`

### 1.5 Verification Pass 1

- Backend tests `python -m pytest Modules/monitor/tests/ -q` — все проходят (добавляем 1-2 теста на новые поля).
- End-to-end через UI: создать профиль → добавить @natgeo → нажать «▶ Обновить» → карточки в Мониторе с thumbnail.
- Фильтры ниш, сортировки, платформы реально меняют видимый список.

## Pass 2 — Алгоритм «идеи в моменте» (1 день, отдельная сессия)

### 2.1 Velocity + Rising/Peaked
`analytics/trending.py`:
- `velocity = current_views / max(hours_since_published, 1)` — views/час.
- `is_rising` — velocity-производная > 0 между последними 3 snapshots.
- «Горячее» = velocity DESC; «X-factor» = velocity × zscore.

### 2.2 Cross-niche percentile
Метод `get_niche_velocity_percentile(niche_slug, velocity)` — % видео в нише ниже текущего. Badge «Top 5% в нише» в карточке.

### 2.3 Fix `views_24h_ago`
`crawler.py:161-164` — `snaps[1]` (предыдущий snapshot, ~6ч назад) заменить на запрос `snapshot WHERE captured_at ≤ now - 24h ORDER BY captured_at DESC LIMIT 1`. Новый метод в `storage.py`.

### 2.4 Default window 48h
`crawler.py:146` — `cutoff_7d` → `cutoff_48h` для trending. 7-дневная вкладка через новый `/videos/recent`.

### 2.5 `/videos/recent` для Месяц/Год
`GET /monitor/videos/recent?account_id=X&days=N&limit=M` — все ролики за N дней, без trending-фильтра. Подключить к time-range «Месяц»/«Год».

## Pass 3 — Контроль расходов Apify (полдня, отдельная сессия)

### 3.1 Apify console preflight (**обязателен перед `MONITOR_FAKE_FETCH=0`**)
- console.apify.com → Billing → Monthly limit $10.
- Actor `apify~instagram-scraper` → Run options → `maxItems` = plan.max_results_limit × 1.5.
- Notifications → email alert на 80% от monthly cap.

### 3.2 Non-admin usage endpoint
`GET /monitor/usage/today?account_id=X` — `{instagram_items, tiktok_items, estimated_usd}`. Consumer UI показывает банер «Сегодня: N постов ≈ $X».

### 3.3 Per-source cost estimate
В строке автора: «~$N/мес при Imin × Rmax». Пересчёт на любое изменение контролов.

### 3.4 Hard ceiling
Env `MONTHLY_BUDGET_USD`. При превышении scheduler паузит, admin флаг `budget_exceeded`, UI баннер.

## Критические файлы (Pass 1)

- [Modules/shell/static/app/index.html](Modules/shell/static/app/index.html) — CSS nav, JS rT/setSrt/setSN/setTR, trigger button
- [Modules/monitor/schemas.py](Modules/monitor/schemas.py) — `TrendingItem`
- [Modules/monitor/router.py](Modules/monitor/router.py) — заполнить новые поля в trending
- (Pass 2) [Modules/monitor/analytics/trending.py](Modules/monitor/analytics/trending.py), [Modules/monitor/crawler.py](Modules/monitor/crawler.py), [Modules/monitor/storage.py](Modules/monitor/storage.py)

## Переиспользуем

- `apiMon`, `flashSave`, `authorColor/Initial`, `NICHE_BY_SLUG/EN` — в index.html.
- `MonitorStore.latest_snapshot` — уже вызывается в `list_trending`, нужны likes/comments.
- Fake-mode на фикстурах — сохраняем для dev.

## Scope этой сессии

**Part A** (5 мин) + **Pass 1** (~3 часа). В конце — рабочий Monitor-таб с реальными данными из backend, трейс от добавления автора до видимых карточек. Pass 2/3 — следующими сессиями.
