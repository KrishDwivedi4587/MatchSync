# Developer Guide

## Prerequisites

- **Docker** + Docker Compose v2.6+ (recommended path; the compose files use
  `service_completed_successfully` dependency conditions). On Windows install
  **Docker Desktop**, which requires the **WSL2** backend
  (`wsl --install`, reboot, then Docker Desktop).
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

# 3. Run the whole stack. The one-shot `migrate` service applies
#    `alembic upgrade head` automatically; the API and workers wait for it,
#    so no manual migration step is needed.
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

## Infrastructure troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `env file ./backend/.env not found` on `docker compose up` | Step 2 of the checklist was skipped — `cp backend/.env.example backend/.env`. |
| `migrate` exits non-zero and api/workers never start | Migration failure is a deliberate hard stop. Inspect with `docker compose logs migrate`; fix, then `docker compose up` again (Alembic re-applies only pending revisions). |
| API healthcheck failing / `curl` errors in `docker compose ps` | The API waits for Postgres, Redis, **and** a completed `migrate`. Check `docker compose logs api` and the migrate logs first. |
| Port already in use (3000/8000/5432/6379) | A host process owns the port — stop it or change the published port in `docker-compose.yml`. |
| Backend refuses to start with a `SECRET_KEY` validation error | You set `ENVIRONMENT=staging|production` — supply a unique 32+ character `SECRET_KEY` (this fail-fast is intentional). |
| Need a completely fresh database | `docker compose down -v` (deletes the named volumes), then `docker compose up --build`. Plain `down` preserves data. |

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
