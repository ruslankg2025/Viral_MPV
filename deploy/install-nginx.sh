#!/usr/bin/env bash
# Устанавливает nginx-vhost для vira.roxber.com + TLS через certbot.
# Предполагается что nginx уже установлен (для ccpm/sync).
# Caddy мы убрали из docker-compose — nginx теперь единственный фронт.
#
# Usage (от root):
#   bash /opt/viral_mpv/deploy/install-nginx.sh
#
set -euo pipefail

DOMAIN="${DOMAIN:-vira.roxber.com}"
EMAIL="${EMAIL:-ruslankg2025@gmail.com}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VHOST_TEMPLATE="$SCRIPT_DIR/nginx-vira.conf"
VHOST_AVAIL="/etc/nginx/sites-available/${DOMAIN}"
VHOST_ENAB="/etc/nginx/sites-enabled/${DOMAIN}"
WEBROOT="/var/www/html"

log() { echo "[$(date -Iseconds)] $*"; }

# 1) certbot — если ещё нет
if ! command -v certbot >/dev/null 2>&1; then
    log "Installing certbot..."
    apt-get update -qq
    apt-get install -y -qq certbot python3-certbot-nginx
fi

# 2) docker shell должен слушать 127.0.0.1:8080
if ! ss -tlnp "sport = :8080" 2>/dev/null | grep -q :8080; then
    log "WARNING: никто не слушает 127.0.0.1:8080. Запусти:"
    log "  cd /opt/viral_mpv/deploy && docker compose -f docker-compose.prod.yml up -d shell"
fi

# 3) ACME webroot
mkdir -p "$WEBROOT/.well-known/acme-challenge"

# 4) Временный HTTP-only vhost чтобы certbot мог пройти challenge
log "Writing temporary HTTP-only vhost for ACME challenge"
cat > "$VHOST_AVAIL" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ {
        root $WEBROOT;
    }
    location / {
        return 404;
    }
}
EOF
ln -sfn "$VHOST_AVAIL" "$VHOST_ENAB"
nginx -t
systemctl reload nginx

# 5) Выпускаем cert через webroot (не --nginx — чтобы certbot не лез
#    переписывать конфиг, мы хотим наш собственный template).
log "Requesting Let's Encrypt cert for $DOMAIN..."
certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" \
    --non-interactive --agree-tos -m "$EMAIL" --keep-until-expiring

# 6) Теперь пишем полный vhost из шаблона и включаем SSL-строки
log "Installing full vhost with HTTPS + proxy_pass"
cp "$VHOST_TEMPLATE" "$VHOST_AVAIL"
# Раскомментируем ssl_* строки (в шаблоне они с префиксом `# `)
sed -i \
    -e "s|^    # ssl_certificate |    ssl_certificate |" \
    -e "s|^    # ssl_certificate_key |    ssl_certificate_key |" \
    -e "s|^    # include /etc/letsencrypt|    include /etc/letsencrypt|" \
    -e "s|^    # ssl_dhparam |    ssl_dhparam |" \
    "$VHOST_AVAIL"

nginx -t
systemctl reload nginx

log ""
log "================================================================"
log "  ✅ Done. Открывай: https://$DOMAIN/"
log "  Cert автообновляется через systemd-таймер certbot.timer"
log "================================================================"
