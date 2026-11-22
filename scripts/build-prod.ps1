$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

Write-Host "Building frontend production bundle into frontend/dist ..." -ForegroundColor Cyan
npm run build --prefix (Join-Path $Root "frontend")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Build complete! You can now run single-process production server:" -ForegroundColor Green
Write-Host "  backend\.venv\Scripts\uvicorn.exe app.main:app --app-dir backend --host 0.0.0.0 --port 8000"
