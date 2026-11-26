#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-pi5}"
REMOTE_DIR="${REMOTE_DIR:-/home/pi/src/stock-screener}"
DATA_DIR="${DATA_DIR:-/media/pi/Seagate8gb/tickers}"
PORT="${PORT:-8085}"
DOMAIN="${DOMAIN:-akcie.dantm.info}"
SKIP_BUILD=0
SYNC_DB=0
FORCE=0
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) REMOTE_HOST="$2"; shift 2 ;;
    --dir) REMOTE_DIR="$2"; shift 2 ;;
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --sync-db) SYNC_DB=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help)
      echo "Usage: bash deploy.sh [--host pi5] [--dir /home/pi/src/stock-screener] [--data-dir /media/pi/Seagate8gb/tickers] [--port 8085] [--domain akcie.dantm.info] [--skip-build] [--sync-db] [--force]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ "$FORCE" -eq 0 ]]; then
  echo "[0/6] Checking $REMOTE_HOST for uncommitted or unpushed Git work..."
  ssh "${SSH_OPTS[@]}" "$REMOTE_HOST" bash -s <<EOF
set -euo pipefail
if [ -d "$REMOTE_DIR/.git" ]; then
  if [ -n "\$(git -C "$REMOTE_DIR" status --porcelain)" ]; then
    echo "ABORT: $REMOTE_HOST:$REMOTE_DIR has uncommitted Git changes! Commit/push on $REMOTE_HOST first, or pass --force." >&2
    git -C "$REMOTE_DIR" status --short >&2
    exit 1
  fi
  git -C "$REMOTE_DIR" fetch origin main --quiet || true
  if git -C "$REMOTE_DIR" rev-parse --verify @{u} >/dev/null 2>&1; then
    UNPUSHED=\$(git -C "$REMOTE_DIR" rev-list "@{u}..HEAD")
  else
    UNPUSHED=\$(git -C "$REMOTE_DIR" rev-list "origin/main..HEAD" 2>/dev/null || true)
  fi
  if [ -n "\$UNPUSHED" ]; then
    echo "ABORT: $REMOTE_HOST:$REMOTE_DIR has unpushed commits! Push from $REMOTE_HOST and git pull locally first, or pass --force." >&2
    exit 1
  fi
fi
EOF
fi

ARCHIVE_LOCAL="$(mktemp /tmp/stock-screener-deploy-XXXXXX.tar.gz)"
ARCHIVE_REMOTE="/tmp/stock-screener-deploy.tar.gz"
DB_REMOTE_TMP="/tmp/stock-screener-seed.db"
cleanup() { rm -f "$ARCHIVE_LOCAL"; }
trap cleanup EXIT

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "[1/6] Building frontend production bundle..."
  npm run build --prefix frontend
else
  echo "[1/6] Skipping frontend build (--skip-build)..."
  [[ -f "frontend/dist/index.html" ]] || { echo "Missing frontend/dist/index.html" >&2; exit 1; }
fi

echo "[2/6] Packaging release archive..."
tar -czf "$ARCHIVE_LOCAL" \
  --exclude="__pycache__" \
  --exclude="*.pyc" \
  --exclude=".venv" \
  --exclude="backend/data" \
  --exclude="node_modules" \
  --exclude=".git" \
  backend/app backend/requirements.txt frontend/dist deploy/stock-screener.service scripts/setup-rpi.sh start.sh

echo "[3/6] Uploading bundle to ${REMOTE_HOST}:${REMOTE_DIR}..."
scp "${SSH_OPTS[@]}" "$ARCHIVE_LOCAL" "${REMOTE_HOST}:${ARCHIVE_REMOTE}"

NEED_DB_SEED=0
if [[ -f "backend/data/screener.db" ]]; then
  if [[ "$SYNC_DB" -eq 1 ]]; then
    NEED_DB_SEED=1
  elif ! ssh -n "${SSH_OPTS[@]}" "$REMOTE_HOST" "test -f '$DATA_DIR/screener.db'" 2>/dev/null; then
    NEED_DB_SEED=1
  fi
fi

if [[ "$NEED_DB_SEED" -eq 1 ]]; then
  echo "[3b/6] Checkpointing & uploading SQLite snapshot DB to $REMOTE_HOST ($DATA_DIR)..."
  if [[ -x "backend/.venv/bin/python" ]]; then
    backend/.venv/bin/python -c "import sqlite3; c=sqlite3.connect('backend/data/screener.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c "import sqlite3; c=sqlite3.connect('backend/data/screener.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"
  fi
  scp "${SSH_OPTS[@]}" "backend/data/screener.db" "${REMOTE_HOST}:${DB_REMOTE_TMP}"
fi

echo "[4/6] Installing dependencies, configuring systemd (DB -> $DATA_DIR) & restarting service on $REMOTE_HOST..."
ssh "${SSH_OPTS[@]}" "$REMOTE_HOST" bash -s <<EOF
set -euo pipefail
mkdir -p "$REMOTE_DIR" "$DATA_DIR" "$DATA_DIR/cache"
if [ -d "$REMOTE_DIR/.git" ]; then
  git -C "$REMOTE_DIR" fetch origin main --quiet || true
  git -C "$REMOTE_DIR" merge --ff-only origin/main --quiet || true
fi
tar -xzf "$ARCHIVE_REMOTE" -C "$REMOTE_DIR"
rm -f "$ARCHIVE_REMOTE"
sed -i 's/\r$//' "$REMOTE_DIR/deploy/stock-screener.service" "$REMOTE_DIR/scripts/setup-rpi.sh" "$REMOTE_DIR/start.sh"
chmod +x "$REMOTE_DIR/scripts/setup-rpi.sh" "$REMOTE_DIR/start.sh"

cd "$REMOTE_DIR"
if [ ! -d "backend/.venv" ]; then
  echo "[Pi] Creating Python virtualenv..."
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install --upgrade pip
fi

REQ_HASH=\$(sha256sum backend/requirements.txt | awk '{print \$1}')
PREV_HASH=""
if [ -f "backend/.venv/.req.sha256" ]; then
  PREV_HASH=\$(cat backend/.venv/.req.sha256)
fi
if [ "\$REQ_HASH" != "\$PREV_HASH" ]; then
  echo "[Pi] Installing/updating ARM64 Python dependencies..."
  backend/.venv/bin/pip install -r backend/requirements.txt
  echo "\$REQ_HASH" > backend/.venv/.req.sha256
else
  echo "[Pi] Python dependencies unchanged (cached)."
fi

if [ "$NEED_DB_SEED" -eq 1 ] && [ -f "$DB_REMOTE_TMP" ]; then
  echo "[Pi] Installing DB at $DATA_DIR/screener.db and enabling production history backfill profile..."
  sudo systemctl stop stock-screener 2>/dev/null || true
  mv "$DB_REMOTE_TMP" "$DATA_DIR/screener.db"
  rm -f "$DATA_DIR/screener.db-wal" "$DATA_DIR/screener.db-shm"
  SCREENER_DB_PATH="$DATA_DIR/screener.db" "$REMOTE_DIR/backend/.venv/bin/python" -c '
import sys; sys.path.insert(0, "backend")
from app.db.database import session_scope
from app.db.models import init_db, load_runtime_settings, save_runtime_settings
init_db()
with session_scope() as s:
    cfg = load_runtime_settings(s)
    cfg.setdefault("history", {})
    cfg["history"]["profile"] = "production"
    cfg["history"]["backfill_enabled"] = True
    save_runtime_settings(s, cfg)
'
fi

test -w "$DATA_DIR" || { echo "ERROR: $DATA_DIR is not writable by \$USER!" >&2; exit 1; }

TMP_SVC="/tmp/stock-screener.service.rendered"
cp "$REMOTE_DIR/deploy/stock-screener.service" "\$TMP_SVC"
sed -i "s|/home/pi/src/stock-screener|$REMOTE_DIR|g" "\$TMP_SVC"
sed -i "s|/media/pi/Seagate8gb/tickers|$DATA_DIR|g" "\$TMP_SVC"
sed -i "s|User=pi|User=\$USER|g" "\$TMP_SVC"
sed -i "s|--port 8085|--port $PORT|g" "\$TMP_SVC"
sed -i "s|SCREENER_PORT=8085|SCREENER_PORT=$PORT|g" "\$TMP_SVC"
sudo cp "\$TMP_SVC" /etc/systemd/system/stock-screener.service
rm -f "\$TMP_SVC"

sudo systemctl daemon-reload
sudo systemctl enable stock-screener
sudo systemctl restart stock-screener

echo "[5/6] Waiting for http://127.0.0.1:$PORT/api/health on $REMOTE_HOST..."
HEALTHY=0
for i in \$(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    HEALTHY=1
    break
  fi
  sleep 1
done

if [ "\$HEALTHY" -ne 1 ]; then
  sudo systemctl status stock-screener --no-pager || true
  sudo journalctl -u stock-screener -n 40 --no-pager || true
  exit 1
fi
echo "[Pi] Service is active and healthy on port $PORT (DB: $DATA_DIR/screener.db)!"
EOF

echo "[6/6] Checking public URL https://${DOMAIN}/api/health ..."
if curl -fsS --max-time 8 "https://${DOMAIN}/api/health" >/dev/null 2>&1; then
  echo "Deploy complete! Live at https://${DOMAIN} (Pi port $PORT, DB: $DATA_DIR/screener.db)"
else
  echo "WARNING: Local Pi service is running on port $PORT, but https://${DOMAIN}/api/health is not reachable yet."
fi
