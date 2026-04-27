# PLAN_ANALYTICS — Аналитика автора

## Контекст

Раздел «Аналитика» в навбаре до 27.04.2026 был заглушкой («Подключить аккаунт»). Задача — наполнить его данными, которые уже собирает Monitor: рост подписчиков, эффективность рилсов, лучшие хэштеги, динамика метрик.

Целевой пользователь — контент-маркетолог, который хочет:
1. Увидеть рост подписчиков любого добавленного автора
2. Сравнить эффективность собственных рилсов vs конкурентов
3. Понять, какие хэштеги/длительности/время публикации работают
4. Не платить за интеграцию Meta Business Manager (1–2 месяца ревью)

## Архитектурное решение

**Аналитика — НЕ отдельный модуль/сервис.** Встроена в `Modules/monitor/`:
- Та же БД (`/db/monitor.db`, volume `monitor_db`)
- Тот же контейнер `vira-monitor`
- Те же роуты (`/api/monitor/sources/{id}/...`)
- UI добавлен на существующий фронт `vira-shell` (`Modules/shell/static/app/index.html`)

Причины:
- Snapshot профиля — это просто новая таблица рядом с `metric_snapshots`/`trending_scores`
- Reel-aggregates — JOIN через те же `videos` + `metric_snapshots` + `trending_scores`
- Отдельный сервис создал бы оверхед синхронизации без выгоды

Выделять в отдельный модуль имеет смысл только на этапе C (OAuth Graph API) — там другая авторизация, другая жизнь токенов, другие rate-limits.

---

## Реализовано (Этап A) — коммит `1e3f513`

### Backend

**SCHEMA_V10** ([storage.py:191](../Modules/monitor/storage.py)):
```sql
CREATE TABLE profile_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    captured_date   TEXT NOT NULL,
    followers_count INTEGER,
    posts_count     INTEGER,
    UNIQUE(source_id, captured_date)
);
```
Один снимок в сутки на source. ON CONFLICT DO UPDATE — если за день несколько `fetch_profile`, держим самое свежее значение.

**Storage helpers** ([storage.py](../Modules/monitor/storage.py)):
- `update_profile()` — расширен; после UPDATE источника пишет snapshot строкой `ON CONFLICT DO UPDATE`. Запись только если хотя бы один из `followers_count`/`posts_count` не None.
- `list_profile_snapshots(source_id, days=90)` → `[(date, followers, posts), ...]`
- `reel_stats_for_source(source_id, days=30)` → `{rows: [{published_at, views, likes, comments, ER, velocity, duration_sec, description}]}`

**API endpoints** ([router.py](../Modules/monitor/router.py)):
- `GET /api/monitor/sources/{id}/profile-snapshots?days=N` (1–365) — массив снимков
- `GET /api/monitor/sources/{id}/reel-stats?days=N` — KPI + полный список рилсов

**Когда snapshot пишется**: `update_profile` вызывается из:
1. `create_source` (первичная регистрация автора — initial snapshot)
2. `_maybe_refresh_profile` в `trigger_crawl` (cooldown 10 мин)

То есть на каждый ручной «Обновить» (если cooldown истёк) либо при добавлении нового автора. **Прямого ежедневного cron нет** — естественное накопление через 4 cron-обхода в сутки + ручные обновления.

### Frontend ([index.html](../Modules/shell/static/app/index.html))

**Структура**:
- Селектор автора (с иконками vitality `⚠`/`·`/без — для broken/silent/normal) + диапазон 30/90/180/365 дней
- 4 KPI-карточки: постов/нед, средний ER, медиана velocity, средние просмотры
- Hand-rolled SVG line chart (area + line) для followers — без сторонних либ
- Топ-10 хэштегов (пилюли с count)
- Текстовая сводка по агрегатам
- Таблица последних 20 рилсов с metrics

**JS-функции**:
- `initAnalytics()` — заполняет селектор авторами из `authorsSources`
- `loadAnalytics()` — `Promise.all([profile-snapshots, reel-stats])` → render
- `renderAnalytics(snaps, stats)` — собирает разметку
- `renderFollowersChart(snaps)` — генерит SVG (viewBox 800×240, padding 36px)

### Чего НЕТ в этапе A

- Графика ER trend over time (есть только агрегат за период)
- Графика velocity по конкретному рилсу с timeline (есть в `/videos/{id}/metrics`, но не отрисован в Аналитике)
- «Лучшее время публикации» (heatmap по часам)
- Sentiment по комментариям
- Сравнение 2+ авторов side-by-side

---

## Этап B — расширенная публичная аналитика (~3–4 недели)

Без новой авторизации, на тех же Apify-actors.

### B.1 — Comments analysis
Подключить `apify/instagram-comments-scraper`. Запускать по запросу пользователя на конкретный рилс (не на всех — слишком дорого).
- Sentiment: позитив/негатив/нейтрал по словарю или micro-LLM (gpt-4o-mini)
- Топ-5 фраз/тем
- Соотношение «отвечает ли автор»

**Стоимость**: ~$0.005 за пост. Кэшировать на 7 дней.

### B.2 — Hashtag tracking
Уже собираем хэштеги из `videos.description`. Добавить:
- Page «Хэштеги» с поиском по нише
- Кто из «Моих авторов» использует тег
- Динамика популярности тега (новых рилсов с тегом за неделю)

### B.3 — Сравнение авторов
В UI Аналитики добавить мульти-селект (до 3 авторов) → графики накладываются:
- Followers over time (3 линии)
- ER distribution (boxplot)
- Posts/week comparison (bar chart)

### B.4 — «Best posting time» heuristic
Не настоящий IG Insights, а аппроксимация:
- Группировать рилсы автора по часам публикации (UTC + локальное время аккаунта)
- Усреднять velocity первого 24-часового окна
- Показывать heatmap «час недели → средняя начальная velocity»

Точно не равно reach/impressions, но даёт сигнал.

### B.5 — ER trend over time
Sparkline за 90 дней — показать, растёт ли вовлечённость или падает.

**Файлы**:
- Новый actor wrapper `Modules/monitor/platforms/instagram_comments.py`
- Расширение `reel_stats_for_source` для timeline-агрегатов
- Новые UI-компоненты (heatmap, multi-line chart)

---

## Этап C — Instagram Graph API OAuth (1–2 месяца)

Полный путь к insights своего аккаунта (только Business/Creator профили, привязанные к Facebook Page).

### Что добавляется
- **Reach & impressions** per post / lifetime
- **Audience demographics** (age, gender, country, city, top locale, online followers heatmap)
- **Profile views**, website clicks, email/phone taps
- **Saves**, **shares** per post (то что Apify не даёт публично)
- **Stories**: views, replies, exits, taps_forward/back

### Архитектура

**Выделение в отдельный модуль** `Modules/insights/`:
- Своя авторизация (OAuth callback handler)
- Своя БД (или своя таблица в monitor с FK)
- Свой rate-limit (Graph API: 200 calls/hour/user)

**Зависимости**:
- Регистрация Meta-приложения
- App Review с обоснованием scopes (`instagram_basic`, `instagram_manage_insights`, `pages_read_engagement`) — 1–4 недели
- Production-домен с HTTPS callback

**Token lifecycle**:
- Short-lived token при OAuth (1 час)
- Обмен на long-lived (60 дней)
- Перед истечением — refresh
- Хранение: encrypted at rest (Fernet, ключ в env)

### Файлы (план)
- `Modules/insights/oauth.py` — OAuth flow (FB Login)
- `Modules/insights/graph_client.py` — обёртка над Graph API
- `Modules/insights/storage.py` — таблицы `insights_tokens`, `insights_metrics`
- `Modules/insights/router.py` — `/api/insights/connect`, `/api/insights/me`, `/api/insights/posts`
- `Modules/insights/Dockerfile` — отдельный контейнер `vira-insights`
- `docker-compose.yml` — добавить service

UI кнопка «Подключить Instagram» на странице Аналитика → редирект на FB Login → callback → токен → синк insights раз в 6 часов.

---

## Этап D (опционально) — Cookie-based scraping

Промежуточный путь между публичными actors и Graph API. Пользователь логинится в IG, выгружает cookie через Selenium/расширение, мы передаём в Apify-actor `apify/instagram-private-scraper`.

### Что даёт
- Saves count
- Иногда reach/impressions
- Story views (если cookie владельца)

### Минусы
- IG может временно банить аккаунт
- Юридически серая зона (нарушение IG TOS)
- Cookie expiration (30–90 дней) → пользователь должен периодически перевыгружать
- Cookie = доступ к аккаунту, нужно encrypted storage

**Рекомендация**: skip, идти сразу в Graph API. Cookie-based как fallback для не-Business аккаунтов.

---

## Verification (этап A)

После rebuild на сервере:

```bash
cd /opt/viral_mpv && git pull && docker compose build monitor shell && docker compose up -d monitor shell
```

1. **Schema migrated**:
```bash
docker exec -i vira-monitor python3 -c "
import sqlite3
c = sqlite3.connect('/db/monitor.db')
print('user_version:', c.execute('PRAGMA user_version').fetchone()[0])
print('snapshots:', c.execute('SELECT COUNT(*) FROM profile_snapshots').fetchone()[0])
"
# user_version: 10
# snapshots: 0 (изначально), будет расти
```

2. **API**:
```bash
SRC_ID=$(curl -fsS -H "X-Monitor-Token: $TOK" \
  "https://vira.roxber.com/api/monitor/sources?account_id=X" | python -c "import sys,json;print(json.load(sys.stdin)[0]['id'])")
curl -s -H "X-Monitor-Token: $TOK" \
  "https://vira.roxber.com/api/monitor/sources/$SRC_ID/reel-stats?days=30" | python -m json.tool
```

Ожидание: `posts`, `posts_per_week`, `avg_views`, `avg_er`, `median_velocity`, `top_hashtags`, `rows`.

3. **UI**:
- Открыть Монитор → клик «Аналитика»
- Выбрать автора из селектора
- Если ≥2 snapshot — должен появиться график; если меньше — сообщение «нужно ≥2 снимка»
- KPI, хэштеги, таблица — должны заполниться через 1–2 секунды

---

## Открытые вопросы / TODO

- [ ] Решить, нужен ли отдельный daily cron для snapshot — сейчас естественно на каждом cron-тике (4 раза в сутки), но если автор `is_active=false`, snapshot не пишется
- [ ] Ретроактивно создать snapshot за сегодня для всех 30 авторов (один INSERT) — чтобы график не был пустым в день деплоя
- [ ] Ограничить размер `top_hashtags` до 10 (сейчас 10, ок) и сделать сортируемым по UI
- [ ] Добавить экспорт CSV таблицы рилсов
- [ ] Скрин-тест: 30 авторов × 30 дней = 900 строк snapshots, проверить performance запроса
