# MatchSync

Automatically synchronize sports fixtures into your Google Calendar.

> **Status:** Stage 2 — project foundation. The app boots and serves a health
> endpoint and a landing page. No business features yet (auth, sync, providers
> arrive in later stages). Architecture is fixed in
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Stack

| Layer | Tech |
|---|---|
| Frontend | Next.js 15 (App Router), TypeScript, TailwindCSS, shadcn/ui, TanStack Query, Zustand, React Hook Form, Zod |
| Backend | FastAPI, Python 3.13, SQLAlchemy 2.0, Alembic, Pydantic v2 |
| Data | PostgreSQL, Redis |
| Background jobs | **Celery + Beat** (chosen in Stage 1 over APScheduler/Temporal) |
| Infra | Docker, Docker Compose, GitHub Actions, pre-commit |

## Repository layout

```
matchsync/
├── frontend/          # Next.js app
├── backend/           # FastAPI + Celery (API, worker, beat share one image)
├── docs/              # Architecture (source of truth) + guides
├── docker/            # Shared docker assets
├── scripts/           # Developer bootstrap scripts
├── .github/           # CI workflows, PR template, labels
├── docker-compose.yml         # dev orchestration
└── docker-compose.prod.yml    # prod overrides
```

## Quick start (Docker — recommended)

Prerequisites: Docker with Compose v2.6+ (on Windows: Docker Desktop with the
WSL2 backend). Schema migrations run automatically on startup.

```bash
git clone <repo-url> matchsync && cd matchsync
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
docker compose up --build
```

- Frontend → http://localhost:3000
- API health → http://localhost:8000/api/v1/health
- API docs (dev) → http://localhost:8000/docs

See [`docs/developer-guide.md`](docs/developer-guide.md) for running without Docker
and the full [first-run checklist](docs/developer-guide.md#first-run-checklist).

## Documentation

- [Architecture (SAD)](docs/ARCHITECTURE.md) — the single source of truth
- [Developer guide](docs/developer-guide.md)
- [Deployment guide](docs/deployment.md)
- [API docs](docs/api.md)
- [Database docs](docs/database.md)
- [Contributing](CONTRIBUTING.md)
