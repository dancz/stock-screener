#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[1/4] Checking Python 3..."
python3 --version

echo "[2/4] Creating virtual environment and installing pure ARM64 Python wheels..."
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install --upgrade pip
pip install -r backend/requirements.txt

echo "[3/4] Checking frontend/dist..."
if [ ! -f "frontend/dist/index.html" ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "Building frontend production bundle..."
    npm --prefix frontend ci
    npm --prefix frontend run build
  else
    echo "WARNING: frontend/dist/index.html not found and npm is not installed."
    echo "Run ./scripts/build-prod.ps1 on your dev machine and copy frontend/dist/ to the Raspberry Pi."
  fi
fi

mkdir -p backend/data

echo "[4/4] Setup complete!"
echo ""
echo "To run manually:"
echo "  $ROOT_DIR/backend/.venv/bin/uvicorn app.main:app --app-dir $ROOT_DIR/backend --host 0.0.0.0 --port 8000"
echo ""
echo "To install as a systemd service:"
echo "  sudo cp $ROOT_DIR/deploy/stock-screener.service /etc/systemd/system/"
echo "  sudo sed -i \"s|/home/pi/stock-screener|$ROOT_DIR|g\" /etc/systemd/system/stock-screener.service"
echo "  sudo sed -i \"s|User=pi|User=$USER|g\" /etc/systemd/system/stock-screener.service"
echo "  sudo systemctl daemon-reload && sudo systemctl enable --now stock-screener"
