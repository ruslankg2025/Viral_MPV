#!/usr/bin/env bash
# Apply latest origin/main on prod-сервере.
#
# Запускается:
#   - cron каждые 2 минуты через /usr/local/bin/vira-update (обёртка)
#   - вручную: bash /opt/viral_mpv/deploy/update.sh [--force]
#
# Флаг --force прогоняет sync-env + docker compose даже когда HEAD=REMOTE
# (полезно если предыдущий тик упал на середине и stack нужно дорастить).
set -e
cd /opt/viral_mpv

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

git fetch --quiet origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ] && [ "$FORCE" = "0" ]; then
    echo "up-to-date ($LOCAL)"
    exit 0
fi

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "[$(date -Iseconds)] updating $LOCAL → $REMOTE"
    git reset --hard origin/main
else
    echo "[$(date -Iseconds)] forced redeploy at $LOCAL"
fi

# Материализовать .env.* из .example для новых сервисов
chmod +x /opt/viral_mpv/deploy/sync-env.sh
/opt/viral_mpv/deploy/sync-env.sh

cd /opt/viral_mpv/deploy
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
