$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

$BackendPython = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $BackendPython)) {
    Write-Host "Creating Python venv and installing backend requirements..." -ForegroundColor Cyan
    python -m venv (Join-Path $Root "backend\.venv")
    & $BackendPython -m pip install --upgrade pip
    & $BackendPython -m pip install -r (Join-Path $Root "backend\requirements.txt")
}

Write-Host "Starting FastAPI backend on http://127.0.0.1:8000 ..." -ForegroundColor Green
$BackendJob = Start-Process -FilePath $BackendPython -ArgumentList "-m uvicorn app.main:app --app-dir `"$Root\backend`" --reload --port 8000" -PassThru -NoNewWindow

try {
    Write-Host "Starting Vite dev server on http://localhost:5173 ..." -ForegroundColor Green
    npm run dev --prefix (Join-Path $Root "frontend")
}
finally {
    if ($BackendJob -and -not $BackendJob.HasExited) {
        Stop-Process -Id $BackendJob.Id -Force -ErrorAction SilentlyContinue
    }
}
