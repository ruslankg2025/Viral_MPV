#!/usr/bin/env bash
# Создаёт отсутствующие .env.* из .example с автогенерацией токенов.
# Идемпотентен — существующие файлы не трогает.
#
# Запускается:
#   - bootstrap.sh при первой установке
#   - vira-update перед docker compose up (чтобы новые env-файлы из git
#     автоматически появились на уже развёрнутых серверах)
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/viral_mpv}"
cd "$REPO_DIR"

log() { echo "[$(date -Iseconds)] sync-env: $*"; }

# ── Простые сервисы — просто cp из example, если файла нет ──────────────
for svc in processor script downloader; do
    if [ ! -f ".env.$svc" ] && [ -f ".env.$svc.example" ]; then
        cp ".env.$svc.example" ".env.$svc"
        log "Created .env.$svc from example"
    fi
done

# ── monitor + profile создаются вместе (общий MONITOR_TOKEN, PROFILE_TOKEN) ──
if [ ! -f .env.monitor ] || [ ! -f .env.profile ]; then
    MT=$(openssl rand -hex 32)
    PT=$(openssl rand -hex 32)
    AT=$(openssl rand -hex 32)
    if [ ! -f .env.monitor ] && [ -f .env.monitor.example ]; then
        cp .env.monitor.example .env.monitor
        sed -i "s|^MONITOR_TOKEN=.*|MONITOR_TOKEN=$MT|; \
                s|^MONITOR_ADMIN_TOKEN=.*|MONITOR_ADMIN_TOKEN=$AT|; \
                s|^PROFILE_TOKEN=.*|PROFILE_TOKEN=$PT|" .env.monitor
        log "Created .env.monitor (auto tokens)"
    fi
    if [ ! -f .env.profile ] && [ -f .env.profile.example ]; then
        cp .env.profile.example .env.profile
        sed -i "s|^PROFILE_TOKEN=.*|PROFILE_TOKEN=$PT|; \
                s|^PROFILE_ADMIN_TOKEN=.*|PROFILE_ADMIN_TOKEN=$AT|" .env.profile
        log "Created .env.profile (auto tokens)"
    fi
fi

# ── Processor: Fernet-ключ для шифрования API-токенов провайдеров ──
if [ -f .env.processor ] && ! grep -qE '^PROCESSOR_KEY_ENCRYPTION_KEY=.+$' .env.processor; then
    if command -v python3 >/dev/null 2>&1 && python3 -c "import cryptography" 2>/dev/null; then
        FKEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    else
        FKEY=$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=')=
    fi
    if grep -q '^PROCESSOR_KEY_ENCRYPTION_KEY=' .env.processor; then
        sed -i "s|^PROCESSOR_KEY_ENCRYPTION_KEY=.*|PROCESSOR_KEY_ENCRYPTION_KEY=$FKEY|" .env.processor
    else
        echo "PROCESSOR_KEY_ENCRYPTION_KEY=$FKEY" >> .env.processor
    fi
    log "Generated PROCESSOR_KEY_ENCRYPTION_KEY"
fi

# ── Shell: создаём + связываем токены с downloader/processor ──
# .env.shell нужен shell-orchestrator для вызова downloader/processor/script
if [ ! -f .env.shell ] && [ -f .env.shell.example ]; then
    cp .env.shell.example .env.shell

    # Привязка DOWNLOADER_TOKEN к тому, что в .env.downloader
    if [ -f .env.downloader ]; then
        DT=$(grep -E '^DOWNLOADER_TOKEN=' .env.downloader | head -1 | cut -d= -f2-)
        if [ -n "$DT" ]; then
            sed -i "s|^DOWNLOADER_TOKEN=.*|DOWNLOADER_TOKEN=$DT|" .env.shell
        fi
    fi

    # Привязка PROCESSOR_TOKEN к тому, что в .env.processor
    if [ -f .env.processor ]; then
        PT=$(grep -E '^PROCESSOR_WORKER_TOKEN=' .env.processor | head -1 | cut -d= -f2-)
        if [ -n "$PT" ]; then
            sed -i "s|^PROCESSOR_TOKEN=.*|PROCESSOR_TOKEN=$PT|" .env.shell
        fi
    fi

    log "Created .env.shell (linked tokens to downloader/processor)"
fi
