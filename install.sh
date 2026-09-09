#!/usr/bin/env bash
set -euo pipefail

APP_NAME="amocrm-rop-mcp"
APP_USER="amocrm-mcp"
APP_DIR="/opt/${APP_NAME}"
SERVICE_NAME="${APP_NAME}"
PYTHON_BIN="${PYTHON_BIN:-}"

log() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

python_ok() {
  local bin="$1"
  command -v "${bin}" >/dev/null 2>&1 || return 1
  "${bin}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
}

pick_python() {
  local candidate
  for candidate in ${PYTHON_BIN:+"${PYTHON_BIN}"} python3.12 python3.11 python3; do
    if python_ok "${candidate}"; then
      PYTHON_BIN="${candidate}"
      return 0
    fi
  done
  return 1
}

if [[ "${EUID}" -ne 0 ]]; then
  die "Run as root: sudo ./install.sh"
fi

log "Starting ${APP_NAME} installer"
log "This installer only creates/restarts systemd unit ${SERVICE_NAME}."
log "Other MCP services (for example wb-readonly-mcp) are not stopped or rewritten."

if [[ ! -f /etc/os-release ]]; then
  die "Unsupported OS: /etc/os-release is missing"
fi
# shellcheck disable=SC1091
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" && "${ID_LIKE:-}" != *"debian"* ]]; then
  log "Warning: this installer targets Ubuntu. Continuing anyway."
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git curl ca-certificates rsync python3-pip
apt-get install -y python3.12 python3.12-venv || true
apt-get install -y python3.11 python3.11-venv || true
apt-get install -y python3-venv || true

if ! pick_python; then
  die "Python 3.11+ is required. On Ubuntu 22.04 run: apt-get install -y python3.11 python3.11-venv"
fi

PY_VERSION="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
log "Using Python ${PY_VERSION} (${PYTHON_BIN})"

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
chmod 640 "${APP_DIR}/.env" || true
chmod 700 "${APP_DIR}/data"
chmod 755 "${APP_DIR}/scripts/"*.py || true

SETUP_ARGS=(--env-file "${APP_DIR}/.env" --non-interactive)
if [[ -n "${PUBLIC_URL:-}" ]]; then
  SETUP_ARGS+=(--public-url "${PUBLIC_URL}")
fi
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/scripts/setup_mcp_oauth.py" "${SETUP_ARGS[@]}"
chmod 640 "${APP_DIR}/.env"

chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"
# Keep root able to update via git while the service user owns runtime files.
chown root:root "${APP_DIR}/install.sh" "${APP_DIR}/update.sh" "${APP_DIR}/install-cloudflare.sh" || true
chmod 755 "${APP_DIR}/install.sh" "${APP_DIR}/update.sh" "${APP_DIR}/install-cloudflare.sh"

MCP_PORT_VALUE="$(awk -F= '/^MCP_PORT=/{print substr($0, index($0,$2))}' "${APP_DIR}/.env" 2>/dev/null || true)"
MCP_PORT_VALUE="${MCP_PORT_VALUE:-8000}"
if [[ ! "${MCP_PORT_VALUE}" =~ ^[0-9]+$ ]]; then
  MCP_PORT_VALUE=8000
fi
if command -v ss >/dev/null 2>&1; then
  if ss -ltn | grep -Eq ":${MCP_PORT_VALUE}\\>"; then
    if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
      die "Port ${MCP_PORT_VALUE} is already in use. Pick a free MCP_PORT in ${APP_DIR}/.env (WB MCP uses 8788). Do not stop other MCP units."
    fi
  fi
fi

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

if curl -fsS "http://127.0.0.1:${MCP_PORT_VALUE}/health" >/dev/null; then
  log "Health check passed: http://127.0.0.1:${MCP_PORT_VALUE}/health"
else
  log "Service installed, but /health is not answering yet. Check: journalctl -u ${SERVICE_NAME} -n 100 --no-pager"
  log "If .env or tokens are incomplete, that is expected until OAuth setup."
fi

log
log "Managed systemd unit: ${SERVICE_NAME}. Other MCP units were not restarted."
log "MCP endpoint stays on 127.0.0.1:${MCP_PORT_VALUE} by default. Do not publish this port to the internet."
log "Next:"
log "  sudo ./install-cloudflare.sh"
log "That command downloads a private cloudflared into this project and prints the HTTPS URL."
log "Then fill amoCRM credentials in ${APP_DIR}/.env and run oauth_setup.py"
