# Developer Guide

## Prerequisites

- **Docker** + Docker Compose (recommended path)
- **Python 3.13** and **Node 20+** (for running services outside Docker)
- **npm** (frontend package manager)

## First-run checklist

Every command needed to go from clone to a verified running stack.

```bash
# 1. Clone
git clone <repo-url> matchsync && cd matchsync

# 2. Environment files
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local

# 3. Run the whole stack
docker compose up --build

# 4. Verify the API is live
curl http://localhost:8000/api/v1/health
# -> {"status":"ok","service":"MatchSync","version":"0.1.0","environment":"local"}

# 5. Verify the database connection (readiness probe)
curl -i http://localhost:8000/api/v1/ready
# -> 200 {"status":"ready","database":true}

# 6. Verify the frontend
open http://localhost:3000        # MatchSync landing page

# 7. Verify the worker is consuming the queue
docker compose logs worker | grep worker_heartbeat   # after up to 15 min, or:
docker compose exec worker python -c \
  "from app.tasks.sync_tasks import heartbeat; print(heartbeat.delay().get(timeout=10))"
# -> ok
```

## Running without Docker

Start only the infrastructure in containers, run app processes on the host:

```bash
docker compose up postgres redis -d

# Backend
cd backend
python3.13 -m venv .venv && source .venv/bin/activate   # Win: .venv\Scripts\activate
pip install -e ".[dev,test]"
alembic upgrade head            # no-op until models exist, but validates wiring
uvicorn app.main:app --reload

# Celery (two more terminals)
celery -A app.worker.celery_app worker --loglevel=INFO
celery -A app.worker.celery_app beat --loglevel=INFO

# Frontend
cd frontend && npm install && npm run dev
```

## Quality gates (run before pushing)

```bash
# Backend
cd backend && ruff check . && black --check . && mypy app && pytest

# Frontend
cd frontend && npm run lint && npm run format:check && npm run typecheck && npm run test
```

`pre-commit install` wires most of these to run automatically on commit.

## Migrations

```bash
cd backend
alembic revision --autogenerate -m "add users table"   # after adding models
alembic upgrade head
alembic downgrade -1
```

## Editor

VS Code users get recommended extensions and settings automatically
(`.vscode/`). Format-on-save and import-organize are pre-configured.
