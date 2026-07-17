# One-time developer bootstrap for Windows / PowerShell. Idempotent.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "==> Creating env files (if missing)"
if (-not (Test-Path backend/.env))        { Copy-Item backend/.env.example backend/.env }
if (-not (Test-Path frontend/.env.local))  { Copy-Item frontend/.env.example frontend/.env.local }

Write-Host "==> Backend: virtualenv + dependencies"
Set-Location backend
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev,test]"
deactivate
Set-Location $Root

Write-Host "==> Frontend: dependencies"
Set-Location frontend
npm install
Set-Location $Root

Write-Host "==> Installing pre-commit hooks"
pip install --user pre-commit
pre-commit install

Write-Host "==> Done. Start the stack with: docker compose up --build"
