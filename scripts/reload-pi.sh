#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${SCREENER_PORT:-8085}"
SKIP_BUILD=0
SKIP_TESTS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1; shift ;;
    --skip-tests) SKIP_TESTS=1; shift ;;
    -p|--port) PORT="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: bash scripts/reload-pi.sh [--skip-build] [--skip-tests] [--port 8085]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

cd "$ROOT_DIR"

echo "[1/4] Checking Python virtualenv dependencies..."
if [ ! -d "backend/.venv" ]; then
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install --upgrade pip
fi
REQ_HASH="$(sha256sum backend/requirements.txt | awk '{print $1}')"
PREV_HASH=""
if [ -f "backend/.venv/.req.sha256" ]; then
  PREV_HASH="$(cat backend/.venv/.req.sha256)"
fi
if [ "$REQ_HASH" != "$PREV_HASH" ]; then
  backend/.venv/bin/pip install -r backend/requirements.txt
  echo "$REQ_HASH" > backend/.venv/.req.sha256
else
  echo "  Python requirements unchanged (cached)."
fi

if [[ "$SKIP_TESTS" -eq 0 ]]; then
  echo "[2/4] Running pytest..."
  backend/.venv/bin/pytest -q
else
  echo "[2/4] Skipping pytest (--skip-tests)."
fi

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "[3/4] Building frontend bundle..."
  if [ ! -d "frontend/node_modules" ]; then
    npm --prefix frontend ci
  fi
  npm --prefix frontend run build
else
  echo "[3/4] Skipping frontend build (--skip-build)."
fi

echo "[4/4] Restarting stock-screener systemd service & verifying health..."
sudo systemctl restart stock-screener
for i in $(seq 1 25); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    echo "Service healthy on http://127.0.0.1:${PORT}/api/health"
    exit 0
  fi
  sleep 1
done

echo "ERROR: Service failed to pass healthcheck on port ${PORT}." >&2
sudo systemctl status stock-screener --no-pager || true
sudo journalctl -u stock-screener -n 40 --no-pager || true
exit 1
