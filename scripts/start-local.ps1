$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$python = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw 'Backend virtual environment not found. Run: cd backend; py -3.13 -m venv .venv; pip install -e ".[dev]"'
}

Write-Host "Starting RecoverAI backend on http://localhost:8000 ..."
Start-Process -FilePath $python `
    -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8000" `
    -WorkingDirectory $backend

Write-Host "Starting RecoverAI dashboard on http://localhost:8080 ..."
Start-Process -FilePath "npm.cmd" `
    -ArgumentList "run dev -- --host 127.0.0.1 --port 8080" `
    -WorkingDirectory $frontend

Write-Host "Dashboard: http://localhost:8080"
Write-Host "API health: http://localhost:8000/health"
