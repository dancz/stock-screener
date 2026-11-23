param(
    [switch]$Prod,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

$BackendPython = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $BackendPython)) {
    Write-Host "[Setup] Creating Python 3.12 virtual environment and installing backend packages..." -ForegroundColor Cyan
    python -m venv (Join-Path $Root "backend\.venv")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $BackendPython -m pip install --upgrade pip
    & $BackendPython -m pip install -r (Join-Path $Root "backend\requirements.txt")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (-not (Test-Path (Join-Path $Root "frontend\node_modules"))) {
    Write-Host "[Setup] Installing frontend npm dependencies..." -ForegroundColor Cyan
    npm install --prefix (Join-Path $Root "frontend")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($Prod) {
    if (-not (Test-Path (Join-Path $Root "frontend\dist\index.html"))) {
        Write-Host "[Build] Building frontend production bundle..." -ForegroundColor Cyan
        npm run build --prefix (Join-Path $Root "frontend")
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    if (-not $NoBrowser) {
        Start-Job -ScriptBlock {
            for ($i = 0; $i -lt 30; $i++) {
                try {
                    $r = Invoke-RestMethod "http://127.0.0.1:8000/api/health" -TimeoutSec 1
                    if ($r.status -eq "ok") { Start-Process "http://localhost:8000"; break }
                } catch {}
                Start-Sleep -Milliseconds 500
            }
        } | Out-Null
    }

    Write-Host "Starting SwingPulse (Single-Process Production) at http://localhost:8000 ..." -ForegroundColor Green
    & $BackendPython -m uvicorn app.main:app --app-dir (Join-Path $Root "backend") --host 0.0.0.0 --port 8000
    exit $LASTEXITCODE
}

Write-Host "Starting FastAPI backend on http://127.0.0.1:8000 ..." -ForegroundColor Green
$BackendJob = Start-Process -FilePath $BackendPython `
    -ArgumentList "-m uvicorn app.main:app --app-dir `"$Root\backend`" --reload --port 8000" `
    -PassThru -NoNewWindow

if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        for ($i = 0; $i -lt 40; $i++) {
            try {
                $resp = Invoke-WebRequest "http://localhost:5173" -UseBasicParsing -TimeoutSec 1
                if ($resp.StatusCode -eq 200) { Start-Process "http://localhost:5173"; break }
            } catch {}
            Start-Sleep -Milliseconds 500
        }
    } | Out-Null
}

try {
    Write-Host "Starting Vite dev UI on http://localhost:5173 ..." -ForegroundColor Green
    npm run dev --prefix (Join-Path $Root "frontend")
}
finally {
    if ($BackendJob -and -not $BackendJob.HasExited) {
        Stop-Process -Id $BackendJob.Id -Force -ErrorAction SilentlyContinue
    }
}
