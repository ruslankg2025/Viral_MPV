#!/usr/bin/env bash
# Bootstrap VIRA on a fresh Ubuntu 22.04+ droplet.
# Idempotent — можно запускать повторно.
#
# Usage (на сервере под root):
#   curl -fsSL https://raw.githubusercontent.com/ruslankg2025/Viral_MPV/main/deploy/bootstrap.sh | bash
#
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/ruslankg2025/Viral_MPV.git}"
REPO_DIR="${REPO_DIR:-/opt/viral_mpv}"
BRANCH="${BRANCH:-main}"

log() { echo "[$(date -Iseconds)] $*"; }

# 1) Docker + compose
if ! command -v docker >/dev/null 2>&1; then
    log "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
fi
if ! docker compose version >/dev/null 2>&1; then
    log "Installing docker-compose-plugin..."
    apt-get update -qq
    apt-get install -y -qq docker-compose-plugin
fi

# 2) git
if ! command -v git >/dev/null 2>&1; then
    apt-get install -y -qq git
fi

# 3) Clone / pull
if [ ! -d "$REPO_DIR/.git" ]; then
    log "Cloning $REPO_URL → $REPO_DIR"
    git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
else
    log "Repo exists, pulling latest $BRANCH"
    git -C "$REPO_DIR" fetch --quiet origin "$BRANCH"
    git -C "$REPO_DIR" reset --hard "origin/$BRANCH"
fi

cd "$REPO_DIR"

# 4) Prepare data dir
mkdir -p data/media

# 5) Create env files with auto-generated random tokens (безопасный fake-старт).
#    .env.* не перезаписываются если уже есть.
if [ ! -f .env.monitor ] || [ ! -f .env.profile ]; then
    MT=$(openssl rand -hex 32)
    PT=$(openssl rand -hex 32)
    AT=$(openssl rand -hex 32)

    if [ ! -f .env.monitor ] && [ -f .env.monitor.example ]; then
        cp .env.monitor.example .env.monitor
        sed -i "s|^MONITOR_TOKEN=.*|MONITOR_TOKEN=$MT|; \
                s|^MONITOR_ADMIN_TOKEN=.*|MONITOR_ADMIN_TOKEN=$AT|; \
                s|^PROFILE_TOKEN=.*|PROFILE_TOKEN=$PT|" .env.monitor
        log "Created .env.monitor (auto tokens, fake_mode для IG/TT/YT пока без ключей)"
    fi
    if [ ! -f .env.profile ] && [ -f .env.profile.example ]; then
        cp .env.profile.example .env.profile
        sed -i "s|^PROFILE_TOKEN=.*|PROFILE_TOKEN=$PT|; \
                s|^PROFILE_ADMIN_TOKEN=.*|PROFILE_ADMIN_TOKEN=$AT|" .env.profile
        log "Created .env.profile (auto tokens)"
    fi
fi
for svc in processor script; do
    if [ ! -f ".env.$svc" ] && [ -f ".env.$svc.example" ]; then
        cp ".env.$svc.example" ".env.$svc"
        log "Created .env.$svc from example"
    fi
done

# Processor требует Fernet-ключ для шифрования API-токенов провайдеров.
# Генерируем если пусто — чтобы контейнер стартанул без ручного вмешательства.
if [ -f .env.processor ] && ! grep -qE '^PROCESSOR_KEY_ENCRYPTION_KEY=.+$' .env.processor; then
    if command -v python3 >/dev/null 2>&1 && python3 -c "import cryptography" 2>/dev/null; then
        FKEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    else
        # fallback: 32 случайных байта в urlsafe-base64 — валидный Fernet-ключ
        FKEY=$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=')=
    fi
    if grep -q '^PROCESSOR_KEY_ENCRYPTION_KEY=' .env.processor; then
        sed -i "s|^PROCESSOR_KEY_ENCRYPTION_KEY=.*|PROCESSOR_KEY_ENCRYPTION_KEY=$FKEY|" .env.processor
    else
        echo "PROCESSOR_KEY_ENCRYPTION_KEY=$FKEY" >> .env.processor
    fi
    log "Generated PROCESSOR_KEY_ENCRYPTION_KEY"
fi

# 6) Установить update-hook в cron (каждые 2 минуты)
cat > /usr/local/bin/vira-update <<'EOF'
#!/usr/bin/env bash
set -e
cd /opt/viral_mpv
git fetch --quiet origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" != "$REMOTE" ]; then
    echo "[$(date -Iseconds)] updating $LOCAL → $REMOTE"
    git reset --hard origin/main
    cd /opt/viral_mpv/deploy
    docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
fi
EOF
chmod +x /usr/local/bin/vira-update

CRON_LINE="*/2 * * * * /usr/local/bin/vira-update >> /var/log/vira-update.log 2>&1"
( crontab -l 2>/dev/null | grep -v vira-update ; echo "$CRON_LINE" ) | crontab -
log "Cron auto-update installed (every 2 min)"

# 7) Открыть порты в ufw если он есть
if command -v ufw >/dev/null 2>&1; then
    ufw allow 80/tcp  >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
fi

# 8) Первый старт compose
cd "$REPO_DIR/deploy"
log "Starting stack (first build can take 3-5 min)..."
docker compose -f docker-compose.prod.yml up -d --build

log ""
log "================================================================"
log "  ✅ Stack up. Следующие шаги:"
log ""
log "  1. Отредактируй прод-секреты:"
log "     nano /opt/viral_mpv/.env.monitor    # APIFY_TOKEN, YOUTUBE_API_KEY, MONITOR_TOKEN"
log "     nano /opt/viral_mpv/.env.profile    # PROFILE_TOKEN"
log "     # токены в .env.monitor и .env.profile должны совпадать для shell"
log ""
log "  2. Перезапусти затронутые сервисы:"
log "     cd /opt/viral_mpv/deploy && docker compose -f docker-compose.prod.yml up -d"
log ""
log "  3. Настрой DNS: vira.roxber.com A → $(curl -s ifconfig.me 2>/dev/null || echo YOUR_IP)"
log ""
log "  4. Проверь: https://vira.roxber.com/app/ (TLS Caddy получит автоматически)"
log "================================================================"
