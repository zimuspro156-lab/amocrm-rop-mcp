#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/amocrm-rop-mcp"
APP_USER="amocrm-mcp"
BIN="${APP_DIR}/runtime/cloudflared"
SERVICE="amocrm-cloudflared"
PORT="${MCP_PORT:-8000}"

log() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

if [[ "${EUID}" -ne 0 ]]; then
  die "Run as root: sudo ./install-cloudflare.sh"
fi
if [[ "$(pwd -P)" != "${APP_DIR}" ]]; then
  die "cd ${APP_DIR} first, then sudo ./install-cloudflare.sh"
fi
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  die "Run ./install.sh first"
fi
if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
  die "Run ./install.sh first"
fi

log "Installing a separate Cloudflare quick tunnel for amoCRM MCP."
log "This does not touch wb-cloudflared or /opt/wb-readonly-mcp."

"${APP_DIR}/.venv/bin/python" "${APP_DIR}/scripts/install_cloudflared.py"
mkdir -p "${APP_DIR}/runtime" "${APP_DIR}/secrets"
chmod 755 "${APP_DIR}/runtime"
chmod 700 "${APP_DIR}/secrets"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/runtime" "${APP_DIR}/secrets"
chmod 755 "${BIN}"

cat >/etc/systemd/system/${SERVICE}.service <<UNIT
[Unit]
Description=Cloudflare Tunnel for amoCRM ROP MCP
After=network-online.target amocrm-rop-mcp.service
Wants=network-online.target
Requires=amocrm-rop-mcp.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment=HOME=${APP_DIR}
ExecStart=${BIN} tunnel --no-autoupdate --url http://127.0.0.1:${PORT}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "${SERVICE}"
systemctl restart "${SERVICE}"

log "Waiting for Cloudflare to issue a URL..."
URL=""
for _ in $(seq 1 20); do
  sleep 2
  URL="$(journalctl -u "${SERVICE}" -n 80 --no-pager | grep -oE 'https://[a-z0-9.-]+\.trycloudflare\.com' | tail -n 1 || true)"
  if [[ -n "${URL}" ]]; then
    break
  fi
done

if [[ -z "${URL}" ]]; then
  log "Tunnel started, but URL is not in logs yet. Check:"
  log "  journalctl -u ${SERVICE} -n 80 --no-pager"
  log "WB tunnel was not changed."
  exit 0
fi

"${APP_DIR}/.venv/bin/python" "${APP_DIR}/scripts/setup_mcp_oauth.py" \
  --env-file "${APP_DIR}/.env" \
  --non-interactive \
  --public-url "${URL}"
chmod 640 "${APP_DIR}/.env"
chown "${APP_USER}:${APP_USER}" "${APP_DIR}/.env"
systemctl restart amocrm-rop-mcp

log
log "Cloudflare URL for this project only:"
log "  ${URL}"
log "ChatGPT connector: ${URL}/mcp"
log "Saved to MCP_PUBLIC_URL and OAUTH_ISSUER."
log "Separate systemd unit: ${SERVICE}"
log "WB unit wb-cloudflared was not restarted."
