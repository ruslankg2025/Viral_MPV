# Shell SPA — `Modules/shell/static/app/`

Consumer-UI (VIRA Dashboard) — vanilla JS, **без сборки/фреймворков**. Один файл `index.html`
(разметка + `<style>` + один inline `<script>`). Раздаётся shell-сервисом по `/app/`.

## Навигация

Клиентского URL-роутера НЕТ. Навигация — функция `go(view)` (бывш. «gotoScreen»):
показывает `<section id="v-<view>">` и прячет остальные. Кнопки — `<button class="nl" data-view="..." onclick="go('...')">`.

Список экранов (`VIEWS` в JS):
`home`, `trendy` (Монитор), `studio` (AI-студия), `analytics` (Аналитика автора),
`agent` (ИИ-агент), `settings`, **`composer`**, **`queue`**, **`xanalytics`**.

## Раздел «Дистрибуция» (кросс-постинг)

Добавлен dropdown в nav («Дистрибуция») с тремя под-экранами:

| Экран | id секции | nav | Backend |
|-------|-----------|-----|---------|
| Композер | `v-composer` | go('composer') | `POST /api/publisher/publish` \| `/schedule` |
| Очередь публикаций | `v-queue` | go('queue') | `GET /api/publisher/publications`, `/retry/{id}`, `/cancel/{id}` |
| Кросс-аналитика | `v-xanalytics` | go('xanalytics') | `GET /api/analytics/cross-summary?period=` |

Существующий экран **`v-analytics`** (Аналитика автора) НЕ затронут — это отдельный экран.

### Композер (`v-composer`)
- Upload `<input type=file accept=video/*>`, заголовок, описание, теги (split по пробелам/запятым).
- Multi-select площадок: «VK Видео»/«VK Клипы» активны; Дзен/YT Shorts/TG/Pinterest — disabled (`скоро`).
- Schedule-toggle: «Сразу» / «Через…» / «Сегодня в HH:MM» / «Завтра в HH:MM».
- Справа — preview-мокап карточки на каждую выбранную площадку.
- Кнопка «Опубликовать»/«Запланировать» → `cmpSubmit()` → publisher API. Toast `_dxToast`.

### Очередь (`v-queue`)
- Таблица с фильтрами (status / platform) и сортировкой по любому столбцу (клик по `<th>`).
- Действия в строке: `retry` (failed), `cancel` (scheduled), `детали` (модалка с payload + error_message).
- Авто-refresh каждые 30 с (`initQueue` → setInterval; останавливается при уходе с экрана).

### Кросс-аналитика (`v-xanalytics`)
- Вкладка «Обзор»: 4 топ-виджета (охват/views/прирост подписчиков/конверсия в TG) + селектор периода 7d/30d/90d,
  per-platform таблица, топ-5 публикаций по reach (карточки).
- Вкладка «A/B сравнение»: выбрать 2 публикации → side-by-side метрики.

## Контракт публикации

```js
publication = {
  id, platform: "vk_video"|"vk_clips", external_id, title, description, tags:[],
  status: "dry_run"|"scheduled"|"publishing"|"published"|"failed",
  scheduled_at, published_at, error_message
}
```

## Мокинг (graceful fallback)

Бэкенды `publisher`/`analytics` могут быть ещё не подняты. Все вызовы обёрнуты в try/catch:
- `_fetchPublications()` → при ошибке возвращает `_PUB_MOCK` (5 демо-публикаций, все статусы) и
  выставляет `_pubUsingMock=true` (в таблице показывается баннер «publisher API недоступен — демо-данные»).
- `xanLoad()` → при ошибке использует `_ANA_MOCK` и баннер про analytics API.
- `queueRetry`/`queueCancel` в мок-режиме показывают «(demo)»-toast вместо ошибки.

Когда реальные бэкенды появятся — никаких изменений во фронте не требуется: контракт совпадает,
fallback просто перестанет срабатывать.

## Shell-прокси (`Modules/shell/main.py`)

Зарегистрированы ДО `include_router` по образцу `proxy_carousel`:
- `proxy_publisher`: `/api/publisher/{path}` → `${PUBLISHER_URL}/publisher`, header `X-Worker-Token`, blocked `{admin}`.
- `proxy_analytics`: `/api/analytics/{path}` → `${ANALYTICS_URL}/analytics`, header `X-Worker-Token`.

Env: `PUBLISHER_URL` (deflt `http://publisher:8000`), `ANALYTICS_URL` (deflt `http://analytics:8000`),
плюс `PUBLISHER_TOKEN`/`ANALYTICS_TOKEN`.

> ГОТЧА: catch-all `{path:path}` НЕ матчит пустой путь — фронт всегда дёргает именованные субпути
> (`/publications`, `/publish`, `/schedule`, `/cross-summary`), не корень `/api/publisher`.

## Smoke-тест (браузер)

После `docker compose up -d --build shell` открыть `/app/`:

1. **Nav** — навести/кликнуть «Дистрибуция» → dropdown с 3 пунктами. Проверить, что
   `go('home')`, `go('trendy')`, `go('studio')`, `go('analytics')`, `go('agent')`, `goS()` (Настройки)
   по-прежнему переключают экраны.
2. **Композер** — выбрать видео, ввести заголовок/описание/теги, отметить «VK Видео» → справа появляется
   мокап-карточка. Переключить schedule на «Через…»/«Завтра в…» — кнопка меняется на «Запланировать».
   Нажать «Опубликовать» → toast (success при живом publisher, иначе ошибка).
3. **Очередь** — открыть; без бэкенда видны 5 демо-строк + баннер. Фильтры status/platform работают,
   клик по заголовку столбца сортирует. «детали» открывает модалку с payload; «retry» у failed; «cancel» у scheduled.
4. **Кросс-аналитика** — «Обзор»: 4 виджета, per-platform таблица, топ-5 карточек, селектор периода.
   Вкладка «A/B»: выбрать A и B → side-by-side.
5. **DevTools Network** — запросы идут на `/api/publisher/*` и `/api/analytics/*` (а не напрямую на upstream).

Регрессия: убедиться, что существующие экраны (home/monitor/studio/analytics/agent/settings) открываются без ошибок в консоли.
