#!/usr/bin/env bash
# One-time developer bootstrap: env files, Python venv, backend + frontend deps,
# and pre-commit hooks. Idempotent — safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Creating env files (if missing)"
[ -f backend/.env ]      || cp backend/.env.example backend/.env
[ -f frontend/.env.local ] || cp frontend/.env.example frontend/.env.local

echo "==> Backend: virtualenv + dependencies"
cd backend
python3.13 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev,test]"
deactivate
cd "$ROOT"

echo "==> Frontend: dependencies"
cd frontend && npm install && cd "$ROOT"

echo "==> Installing pre-commit hooks"
pip install --user pre-commit >/dev/null 2>&1 || true
pre-commit install || echo "pre-commit not on PATH; run 'pre-commit install' manually"

echo "==> Done. Start the stack with: docker compose up --build"
