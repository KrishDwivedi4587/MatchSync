# MatchSync — Backend

FastAPI + SQLAlchemy 2.0 + Celery backend. See [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)
for the authoritative design and [`../docs/developer-guide.md`](../docs/developer-guide.md)
for workflow details.

## Layout

```
app/
├── api/            # Presentation: routers, middleware, deps (thin)
├── application/    # Use-case services (transaction boundaries)
├── domain/         # Pure business rules + port interfaces (no I/O)
├── infrastructure/ # Adapters: providers, google, crypto, http
├── persistence/    # ORM models, repositories, session/engine
├── schemas/        # Pydantic DTOs (API contract)
├── tasks/          # Celery task entrypoints
├── core/           # config + logging
├── exceptions/     # typed error hierarchy + handlers
├── main.py         # FastAPI app factory
└── worker.py       # Celery app + Beat schedule
```

## Quick start (without Docker)

```bash
python3.13 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev,test]"
cp .env.example .env

uvicorn app.main:app --reload      # API at http://localhost:8000
```

Requires a reachable Postgres and Redis (start them with the root
`docker compose up postgres redis`).

## Common commands

```bash
uvicorn app.main:app --reload                          # run API
celery -A app.worker.celery_app worker --loglevel=INFO # run worker
celery -A app.worker.celery_app beat --loglevel=INFO   # run scheduler
alembic revision --autogenerate -m "message"           # new migration
alembic upgrade head                                   # apply migrations
pytest                                                 # tests + coverage
ruff check . && black --check . && mypy app            # lint/format/type
```
