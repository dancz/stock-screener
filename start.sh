#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [ ! -x "backend/.venv/bin/python" ]; then
  echo "[Setup] Creating Python virtual environment and installing dependencies..."
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install --upgrade pip
  backend/.venv/bin/pip install -r backend/requirements.txt
fi

if [ ! -f "frontend/dist/index.html" ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "[Build] Building frontend production bundle..."
    npm --prefix frontend install
    npm --prefix frontend run build
  else
    echo "ERROR: frontend/dist/index.html missing and npm is not installed."
    echo "Build frontend/dist on a dev machine with ./scripts/build-prod.ps1 and copy it here."
    exit 1
  fi
fi

mkdir -p backend/data
echo "Starting SwingPulse at http://0.0.0.0:8000 ..."
exec backend/.venv/bin/uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
