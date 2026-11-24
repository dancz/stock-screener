param(
    [string]$RemoteHost = "pi5",
    [string]$RemoteDir = "/home/pi/src/stock-screener",
    [string]$DataDir = "/media/pi/Seagate8gb/tickers",
    [int]$Port = 8085,
    [string]$Domain = "akcie.dantm.info",
    [switch]$SkipBuild,
    [switch]$SyncDb
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$ArchiveLocal = Join-Path $env:TEMP ("stock-screener-deploy-" + [guid]::NewGuid().ToString("N") + ".tar.gz")
$ArchiveRemote = "/tmp/stock-screener-deploy.tar.gz"
$DbRemoteTmp = "/tmp/stock-screener-seed.db"
$SshOpts = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")

Push-Location $Root
try {
    if (-not $SkipBuild) {
        Write-Host "[1/6] Building frontend production bundle..." -ForegroundColor Cyan
        npm run build --prefix (Join-Path $Root "frontend")
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed with exit code $LASTEXITCODE" }
    } else {
        Write-Host "[1/6] Skipping frontend build (-SkipBuild specified)..." -ForegroundColor Yellow
        if (-not (Test-Path (Join-Path $Root "frontend\dist\index.html"))) {
            throw "frontend/dist/index.html not found. Run without -SkipBuild."
        }
    }

    Write-Host "[2/6] Packaging release archive..." -ForegroundColor Cyan
    & tar.exe -czf $ArchiveLocal `
        --exclude="__pycache__" `
        --exclude="*.pyc" `
        --exclude=".venv" `
        --exclude="backend/data" `
        --exclude="node_modules" `
        --exclude=".git" `
        backend/app backend/requirements.txt frontend/dist deploy/stock-screener.service scripts/setup-rpi.sh start.sh
    if ($LASTEXITCODE -ne 0) { throw "tar packaging failed with exit code $LASTEXITCODE" }

    Write-Host "[3/6] Uploading bundle to ${RemoteHost}:${RemoteDir}..." -ForegroundColor Cyan
    & scp @SshOpts $ArchiveLocal "${RemoteHost}:${ArchiveRemote}"
    if ($LASTEXITCODE -ne 0) { throw "scp bundle upload failed with exit code $LASTEXITCODE" }

    $NeedDbSeed = 0
    $LocalDb = Join-Path $Root "backend\data\screener.db"
    if (Test-Path $LocalDb) {
        if ($SyncDb) {
            $NeedDbSeed = 1
        } else {
            & ssh -n @SshOpts $RemoteHost "test -f '$DataDir/screener.db'" 2>$null
            if ($LASTEXITCODE -ne 0) {
                $NeedDbSeed = 1
            }
        }
    }

    if ($NeedDbSeed -eq 1) {
        Write-Host "[3b/6] Checkpointing & uploading SQLite snapshot DB to $RemoteHost ($DataDir)..." -ForegroundColor Cyan
        $LocalVenvPy = Join-Path $Root "backend\.venv\Scripts\python.exe"
        if (Test-Path $LocalVenvPy) {
            & $LocalVenvPy -c "import sqlite3; c=sqlite3.connect('backend/data/screener.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"
        }
        & scp @SshOpts $LocalDb "${RemoteHost}:${DbRemoteTmp}"
        if ($LASTEXITCODE -ne 0) { throw "scp DB upload failed with exit code $LASTEXITCODE" }
    }

    Write-Host "[4/6] Installing dependencies, configuring systemd (DB -> $DataDir) & restarting on $RemoteHost..." -ForegroundColor Cyan
    $RemoteScript = @"
set -euo pipefail
mkdir -p "$RemoteDir" "$DataDir" "$DataDir/cache"
tar -xzf "$ArchiveRemote" -C "$RemoteDir"
rm -f "$ArchiveRemote"
sed -i 's/\r$//' "$RemoteDir/deploy/stock-screener.service" "$RemoteDir/scripts/setup-rpi.sh" "$RemoteDir/start.sh"
chmod +x "$RemoteDir/scripts/setup-rpi.sh" "$RemoteDir/start.sh"

cd "$RemoteDir"
if [ ! -d "backend/.venv" ]; then
  echo "[Pi] Creating Python virtualenv..."
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install --upgrade pip
fi

REQ_HASH=`$(sha256sum backend/requirements.txt | awk '{print `$1}')
PREV_HASH=""
if [ -f "backend/.venv/.req.sha256" ]; then
  PREV_HASH=`$(cat backend/.venv/.req.sha256)
fi
if [ "`$REQ_HASH" != "`$PREV_HASH" ]; then
  echo "[Pi] Installing/updating ARM64 Python dependencies..."
  backend/.venv/bin/pip install -r backend/requirements.txt
  echo "`$REQ_HASH" > backend/.venv/.req.sha256
else
  echo "[Pi] Python dependencies unchanged (cached)."
fi

if [ "$NeedDbSeed" -eq 1 ] && [ -f "$DbRemoteTmp" ]; then
  echo "[Pi] Installing DB at $DataDir/screener.db and enabling production history backfill profile..."
  sudo systemctl stop stock-screener 2>/dev/null || true
  mv "$DbRemoteTmp" "$DataDir/screener.db"
  rm -f "$DataDir/screener.db-wal" "$DataDir/screener.db-shm"
  SCREENER_DB_PATH="$DataDir/screener.db" "$RemoteDir/backend/.venv/bin/python" -c '
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

test -w "$DataDir" || { echo "ERROR: $DataDir is not writable by `$USER!" >&2; exit 1; }

TMP_SVC="/tmp/stock-screener.service.rendered"
cp "$RemoteDir/deploy/stock-screener.service" "`$TMP_SVC"
sed -i "s|/home/pi/src/stock-screener|$RemoteDir|g" "`$TMP_SVC"
sed -i "s|/media/pi/Seagate8gb/tickers|$DataDir|g" "`$TMP_SVC"
sed -i "s|User=pi|User=`$USER|g" "`$TMP_SVC"
sed -i "s|--port 8085|--port $Port|g" "`$TMP_SVC"
sed -i "s|SCREENER_PORT=8085|SCREENER_PORT=$Port|g" "`$TMP_SVC"
sudo cp "`$TMP_SVC" /etc/systemd/system/stock-screener.service
rm -f "`$TMP_SVC"

sudo systemctl daemon-reload
sudo systemctl enable stock-screener
sudo systemctl restart stock-screener

echo "[5/6] Waiting for http://127.0.0.1:$Port/api/health on $RemoteHost..."
HEALTHY=0
for i in `$(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$Port/api/health" >/dev/null 2>&1; then
    HEALTHY=1
    break
  fi
  sleep 1
done

if [ "`$HEALTHY" -ne 1 ]; then
  sudo systemctl status stock-screener --no-pager || true
  sudo journalctl -u stock-screener -n 40 --no-pager || true
  exit 1
fi
echo "[Pi] Service is active and healthy on port $Port (DB: $DataDir/screener.db)!"
"@

    ($RemoteScript -replace "`r`n", "`n") | & ssh @SshOpts $RemoteHost "bash -s"
    if ($LASTEXITCODE -ne 0) {
        throw "Remote deployment failed on $RemoteHost. Inspect logs via: ssh $RemoteHost 'sudo journalctl -u stock-screener -n 50 --no-pager'"
    }

    Write-Host "[6/6] Checking public URL https://${Domain}/api/health ..." -ForegroundColor Cyan
    try {
        $resp = Invoke-RestMethod -Uri "https://${Domain}/api/health" -TimeoutSec 8
        if ($resp.status -eq "ok") {
            Write-Host "Deploy complete! Live at https://${Domain} (Pi port $Port, DB: $DataDir/screener.db)" -ForegroundColor Green
        } else {
            Write-Warning "Local Pi service healthy on port $Port, but https://${Domain}/api/health returned unexpected response."
        }
    } catch {
        Write-Warning "Local Pi service is running on port $Port, but https://${Domain}/api/health is not reachable yet: $($_.Exception.Message)"
        Write-Host "If this is a fresh setup, ensure Cloudflare Tunnel (cloudflared) routes ${Domain} -> http://localhost:${Port}." -ForegroundColor Yellow
    }
}
finally {
    Pop-Location
    if (Test-Path $ArchiveLocal) {
        Remove-Item $ArchiveLocal -Force -ErrorAction SilentlyContinue
    }
}
