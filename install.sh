#!/usr/bin/env bash
set -euo pipefail

APP_NAME="amocrm-rop-mcp"
APP_USER="amocrm-mcp"
APP_DIR="/opt/${APP_NAME}"
SERVICE_NAME="${APP_NAME}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"

log() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

if [[ "${EUID}" -ne 0 ]]; then
  die "Run as root: sudo ./install.sh"
fi

if [[ ! -f /etc/os-release ]]; then
  die "Unsupported OS: /etc/os-release is missing"
fi
# shellcheck disable=SC1091
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" && "${ID_LIKE:-}" != *"debian"* ]]; then
  log "Warning: this installer targets Ubuntu. Continuing anyway."
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi
command -v "${PYTHON_BIN}" >/dev/null 2>&1 || die "Python 3.11+ is required"

PY_VERSION="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
"${PYTHON_BIN}" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(1)
PY
log "Using Python ${PY_VERSION} (${PYTHON_BIN})"

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip git curl ca-certificates rsync

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --home "${APP_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
  log "Created system user ${APP_USER}"
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "${APP_DIR}"
if [[ "${SOURCE_DIR}" != "${APP_DIR}" ]]; then
  rsync -a --delete --exclude '.venv' --exclude 'data/tokens.json' --exclude '.env' "${SOURCE_DIR}/" "${APP_DIR}/" 2>/dev/null || \
    cp -a "${SOURCE_DIR}/." "${APP_DIR}/"
fi
mkdir -p "${APP_DIR}/data"

if [[ ! -f "${APP_DIR}/.env" ]]; then
  if [[ -f "${APP_DIR}/.env.example" ]]; then
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
    log "Created ${APP_DIR}/.env from example. Fill amoCRM credentials before OAuth setup."
  else
    die ".env.example is missing"
  fi
fi

cd "${APP_DIR}"
"${PYTHON_BIN}" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

chmod 750 "${APP_DIR}"
chmod 640 "${APP_DIR}/.env"
chmod 700 "${APP_DIR}/data"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
# Keep root able to update via git while the service user owns runtime files.
chown root:root "${APP_DIR}/install.sh" "${APP_DIR}/update.sh" || true
chmod 755 "${APP_DIR}/install.sh" "${APP_DIR}/update.sh"

cat >/etc/systemd/system/${SERVICE_NAME}.service <<UNIT
[Unit]
Description=amoCRM ROP MCP Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/python server.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
sleep 2
systemctl --no-pager --full status "${SERVICE_NAME}" || true

if curl -fsS "http://127.0.0.1:8000/health" >/dev/null; then
  log "Health check passed: http://127.0.0.1:8000/health"
else
  log "Service installed, but /health is not answering yet. Check: journalctl -u ${SERVICE_NAME} -n 100 --no-pager"
  log "If .env or tokens are incomplete, that is expected until OAuth setup."
fi

log
log "MCP endpoint stays on 127.0.0.1:8000 by default. Do not publish port 8000 to the internet."
log "Put HTTPS (Caddy / Nginx / Traefik / Cloudflare Tunnel) in front of /mcp."
log "Next:"
log "  sudo -u ${APP_USER} ${APP_DIR}/.venv/bin/python ${APP_DIR}/scripts/oauth_setup.py"
log "  sudo -u ${APP_USER} ${APP_DIR}/.venv/bin/python ${APP_DIR}/scripts/check_connection.py"
log "  sudo systemctl restart ${SERVICE_NAME}"
