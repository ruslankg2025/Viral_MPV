<!-- generated from claudeclaw/templates/CLAUDE.template.md v1.2 -->

# VIRAL_MPV — Viral Monitor

Платформа мониторинга вирусных видео с AI-пайплайном для контент-мейкеров.
Прод: https://vira.roxber.com

## Запуск

Локально (Windows + Docker Desktop):
```
docker compose up -d --build
# 7 сервисов: shell, processor, script, profile, monitor, downloader, knowledge
```

Прод (DigitalOcean Droplet "Roxber-mentor", IP 188.166.40.149):
```
cd /opt/viral_mpv && git pull && docker compose up -d --build <service>
```
cron auto-pull каждые ~2 мин (только `git pull`, без recreate). После деплоя — Ctrl+Shift+R в браузере.

## Среда

- Корень: `D:\PROGRAMS\VIRAL_MPV\` (локально), `/opt/viral_mpv/` (прод)
- Стек: Python (FastAPI микросервисы), Vanilla JS frontend, docker-compose
- 7 микросервисов и порты:
  - `shell` (8000) — API gateway/BFF + frontend SPA в `Modules/shell/static/app/`
  - `processor` (8100) — AI обработка видео
  - `script` (8200) — генерация сценариев
  - `profile` (8300) — аналитика профилей
  - `monitor` (8400) — мониторинг вирусности (Apify)
  - `downloader` (8500) — скачивание видео
  - `knowledge` (8600) — Knowledge Base + RAG (этап 4 self-learning)
- Секреты: 7 env-файлов в корне (`.env.<service>`). Образцы — `.env.<service>.example`. Реальные `.env.*` в `.gitignore`.

## Структура

- `Modules/` — код микросервисов (`Modules/shell/`, `Modules/processor/`, и т.д.)
- `Modules/shared/` — общий код между сервисами
- `Modules/shell/static/app/` — frontend SPA (vanilla JS, без билда)
- `data/` — рантайм-данные (media, db) — в .gitignore
- `deploy/` — скрипты деплоя
- `docs/` — техническая документация (бывший `manuals/`)
- `plan/` — архитектурная декомпозиция (12 PLAN_*.md по компонентам — НЕ оперативные планы)
- `plans/` — оперативные задачи в формате `YYYY-MM-DD-name.md`
- `.business/` — продуктовый/бизнес-контекст
- `convert.py` — утилита (legacy конверсия)

## Карта плана

`plan/` содержит архитектурные ТЗ по компонентам (НЕ оперативные таски):
- [PLAN_VIDEO_DOWNLOADER.md](plan/PLAN_VIDEO_DOWNLOADER.md) — архитектура downloader
- [PLAN_VIDEO_PROCESSOR.md](plan/PLAN_VIDEO_PROCESSOR.md) — архитектура processor
- [PLAN_PROFILE.md](plan/PLAN_PROFILE.md) — архитектура profile
- [PLAN_SCRIPT.md](plan/PLAN_SCRIPT.md) — архитектура script
- [PLAN_A2_MONITOR.md](plan/PLAN_A2_MONITOR.md), [PLAN_MONITOR_ACTIVATION.md](plan/PLAN_MONITOR_ACTIVATION.md) — monitor
- [PLAN_ANALYTICS.md](plan/PLAN_ANALYTICS.md), [PLAN_BACKEND_DATA_AND_ANALYTICS.md](plan/PLAN_BACKEND_DATA_AND_ANALYTICS.md) — аналитика
- [PLAN_AI_STUDIO_DESIGN_BRIEF.md](plan/PLAN_AI_STUDIO_DESIGN_BRIEF.md), [PLAN_DESIGN_PATCH.md](plan/PLAN_DESIGN_PATCH.md), [PLAN_RAZBOR_UI_PIRATEX.md](plan/PLAN_RAZBOR_UI_PIRATEX.md) — UI/дизайн
- [PLAN_SELF_LEARNING_AGENT.md](plan/PLAN_SELF_LEARNING_AGENT.md) — самообучающийся агент

`plans/` — оперативные задачи: `YYYY-MM-DD-feature.md`. Формат описан в [plans/README.md](plans/README.md).

## Внешние сервисы и API

| Сервис | env переменная | Где используется | Статус |
|--------|----------------|------------------|--------|
| Apify | `APIFY_TOKEN` | `.env.monitor`, `.env.downloader` | работает (Default unrestricted) |
| OpenAI | `BOOTSTRAP_OPENAI_API_KEY` | `.env.processor`, `.env.knowledge` | ключ не подключён |
| Anthropic | `ANTHROPIC_API_KEY` | `.env.processor`, `.env.knowledge` | ключ не подключён |
| AssemblyAI | `ASSEMBLYAI_API_KEY` | `.env.processor` | ключ не подключён |
| Deepgram | `DEEPGRAM_API_KEY` | `.env.processor` | ключ не подключён |
| Groq | `GROQ_API_KEY` | `.env.processor` | ключ не подключён |
| Gemini | `GEMINI_API_KEY` | `.env.processor`, `.env.knowledge` | ключ не подключён |

> Apify Default-токен светился в чате — запланирована ротация (см. [.business/INDEX.md](.business/INDEX.md) → Tech debt).

## Метрики (на 2026-05-07)

- Подписчики: 507
- 7д ER (engagement rate): 1.4%
- Apify spending: $1.11 / $30 (3.7%)

## Roadmap

- **Текущая фаза:** аналитика 1д/7д, Services Dashboard в ИИ-агенте
- **Следующая:** Mode 5 «Загрузка авторского MP4» — multipart upload, thumbnail, опционально deep_analyze
- **Дальше:** интеграция OpenAI/Anthropic/AssemblyAI и т.д. (по мере появления ключей)
- **Фаза 2 (по ТЗ):** полный SaaS — multi-tenant, auth, BYOK, AI Studio, автопубликация, биллинг

Полный roadmap и контекст — [.business/INDEX.md](.business/INDEX.md). Источник правды по продуктовым требованиям — [ТЗ_ВИРАЛ-монитор.md](ТЗ_ВИРАЛ-монитор.md).

## Известные проблемы

См. [.business/INDEX.md](.business/INDEX.md) → Tech debt.

## Правила работы

- Перед коммитом: `git diff --cached | grep -iE "API_KEY|TOKEN|PASSWORD"` — проверка на утечку ключей
- НЕ коммитить `.env.<service>` (только `.env.<service>.example`)
- SSH на прод через DigitalOcean web console (paste бажит — печатать вручную или однострочниками)
- Тестирование в браузере + Chrome DevTools, не в SSH
- Изменения в Python-коде сервиса: `git pull && docker compose up -d --build <service>`
- Frontend (`index.html`, `*.js` в `Modules/shell/static/app/`) — без билда, обновляется после `--build shell`
- Перед коммитом всегда уточнять у Руслана (кроме очевидных one-line фиксов)

## Команды

| Команда | Действие |
|---------|----------|
| `docker compose up -d --build <service>` | Пересобрать и запустить сервис |
| `docker compose logs -f <service>` | Логи в режиме онлайн |
| `docker compose ps` | Статус всех сервисов |
| `git pull` | Подтянуть изменения (на проде — авто через cron каждые ~2 мин) |
| `?force=true` (URL) | Bypass 5-мин кэша Services Dashboard |
| `?debug=true` (URL) | Диагностика Services Dashboard |
