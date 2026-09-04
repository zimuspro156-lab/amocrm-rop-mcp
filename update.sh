#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/amocrm-rop-mcp"
SERVICE_NAME="amocrm-rop-mcp"
BRANCH="${BRANCH:-main}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo ./update.sh" >&2
  exit 1
fi

cd "${APP_DIR}"

if [[ ! -d .git ]]; then
  echo "ERROR: ${APP_DIR} is not a git checkout" >&2
  exit 1
fi

git fetch origin
git pull --ff-only origin "${BRANCH}"

# shellcheck disable=SC1091
source .venv/bin/activate
pip install -r requirements.txt

if ! pytest -q; then
  echo
  echo "Tests failed. Production service was NOT restarted."
  echo "Fix the issue, then run sudo ./update.sh again."
  exit 1
fi

systemctl restart "${SERVICE_NAME}"
sleep 2
systemctl --no-pager --full status "${SERVICE_NAME}" || true

if curl -fsS "http://127.0.0.1:8000/health"; then
  echo
  echo "Update complete."
else
  echo "Service restarted, but /health failed. Check journalctl -u ${SERVICE_NAME} -n 100 --no-pager" >&2
  exit 1
fi
