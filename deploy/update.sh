#!/usr/bin/env bash
# Ручной запуск апдейта (обычно запускается по cron из bootstrap.sh).
set -e
cd /opt/viral_mpv
git fetch --quiet origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" = "$REMOTE" ]; then
    echo "up-to-date ($LOCAL)"
    exit 0
fi
echo "[$(date -Iseconds)] updating $LOCAL → $REMOTE"
git reset --hard origin/main
cd /opt/viral_mpv/deploy
docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
