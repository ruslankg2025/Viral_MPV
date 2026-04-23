# VIRA — продакшн-деплой

Разворачивает стек на `vira.roxber.com` через один Caddy-реверс-прокси с авто-HTTPS.
Сервисы (profile / monitor / shell / processor / script) — во внутренней docker-сети,
наружу торчит только Caddy на :80/:443.

## Одноразовая установка

**На сервере** (DO droplet `188.166.40.149`, Ubuntu 24.04, от root):

```bash
curl -fsSL https://raw.githubusercontent.com/ruslankg2025/Viral_MPV/main/deploy/bootstrap.sh | bash
```

Скрипт:

- поставит docker + compose если нет
- склонирует репо в `/opt/viral_mpv`
- создаст `.env.*` из `.example` (с дефолтными dev-токенами — их надо поменять)
- установит cron-хук, который каждые 2 минуты делает `git pull` и пересобирает изменённые сервисы
- запустит стек

## Секреты — отредактировать на сервере

```bash
cd /opt/viral_mpv
nano .env.monitor   # APIFY_TOKEN, YOUTUBE_API_KEY, MONITOR_TOKEN, MONITOR_FAKE_FETCH=false
nano .env.profile   # PROFILE_TOKEN
```

⚠ Токены `MONITOR_TOKEN` и `PROFILE_TOKEN` должны совпадать с теми, что shell использует
для вызова upstream (они читаются из тех же файлов — shell грузит `.env.monitor` и
`.env.profile`, поэтому достаточно заполнить значения один раз в каждом файле).

⚠ Для боевого Apify:
- Сначала в [console.apify.com](https://console.apify.com) → Billing → Monthly limit $10
- Actor `apify~instagram-scraper` → Run options → `maxItems` = 45
- Notifications → email alert на 80% cap
- Только **после этого** ставь `MONITOR_FAKE_FETCH=false`

После правки env:

```bash
cd /opt/viral_mpv/deploy
docker compose -f docker-compose.prod.yml up -d
```

## DNS

В панели регистратора Roxber:

```
A    vira.roxber.com    188.166.40.149
```

Caddy автоматически получит TLS-сертификат от Let's Encrypt при первом HTTPS-запросе.

## Как работает авто-деплой из git

1. Локально делаешь изменения → `git push origin main`
2. На сервере cron каждые 2 мин делает `git fetch` и сравнивает SHA
3. Если есть новый коммит — `git reset --hard origin/main` + `docker compose up -d --build`
4. Compose пересобирает только затронутые сервисы (благодаря слоистому кешу)

Лог апдейтов: `/var/log/vira-update.log`

## Ручные команды на сервере

```bash
# Статус
cd /opt/viral_mpv/deploy && docker compose -f docker-compose.prod.yml ps

# Логи конкретного сервиса
docker compose -f docker-compose.prod.yml logs -f monitor

# Перезапустить один сервис
docker compose -f docker-compose.prod.yml restart monitor

# Пересобрать вручную
/usr/local/bin/vira-update

# Полная перезагрузка стека
docker compose -f docker-compose.prod.yml down && docker compose -f docker-compose.prod.yml up -d --build
```

## Откат на предыдущий коммит

```bash
cd /opt/viral_mpv
git log --oneline -5          # найти SHA нужного коммита
git reset --hard <SHA>
cd deploy
docker compose -f docker-compose.prod.yml up -d --build
```

## Мониторинг использования Apify

Без админ-интерфейса: `docker compose -f docker-compose.prod.yml logs monitor | grep apify_usage`
— в логах видны run-ы и item-ы. Cap контролируется в console.apify.com (см. выше).

## Архитектура прод-окружения

- **nginx на хосте** — единый reverse-proxy и TLS-терминатор. Уже обслуживает `ccpm.roxber.com`, `sync.roxber.com`. Для VIRA добавляется vhost `vira.roxber.com` через [install-nginx.sh](install-nginx.sh), который делает `certbot` и подключает шаблон [nginx-vira.conf](nginx-vira.conf).
- **docker-compose стек** — 5 сервисов во внутренней сети. Shell выставляет порт `127.0.0.1:8080` (только loopback). Nginx проксирует `vira.roxber.com` → `127.0.0.1:8080`.
- **Auto-update:** cron на хосте каждые 2 мин делает `git pull` + `docker compose up -d --build`, если есть новый коммит.

## Что экспонируется наружу (через nginx)

- `https://vira.roxber.com/` → Consumer UI (nginx переписывает путь в `/app/` для shell, префикс для браузера не виден)
- `https://vira.roxber.com/app/*` → 301-редирект на `/` (канонизация)
- `https://vira.roxber.com/api/profile/*`, `/api/monitor/*` → через shell gateway с серверной подстановкой токена

**НЕ экспонируется:**
- `/monitor/admin/*` — блокируется на уровне shell gateway
- `/profile/seed` — блокируется на уровне shell gateway
- Прямые порты 8100 (profile), 8400 (monitor), etc. — только во внутренней сети compose

Для админ-задач — `docker exec vira-monitor ...` или VPN + прямой вызов портов.
