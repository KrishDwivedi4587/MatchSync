# Deployment Guide

> Foundation-stage guidance. A full CD pipeline (registry push, environment
> promotion, migrations-on-deploy) lands in the deployment stage.

## Images

Both services build to small, non-root, multi-stage images:

- `backend/Dockerfile` — one image runs the **API**, **worker**, and **beat**
  (command supplied by the orchestrator).
- `frontend/Dockerfile` — Next.js `standalone` output.

## Production compose

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Production overrides (see `docker-compose.prod.yml`):

- No source bind-mounts; images are immutable.
- API runs multiple uvicorn workers; Celery worker uses higher concurrency.
- `ENVIRONMENT=production` → interactive docs disabled, JSON logs.
- `restart: unless-stopped` on all services.

## Configuration & secrets

- **Never** ship a real `.env`. Inject configuration as environment variables
  from your platform / secrets manager.
- Required in production: `SECRET_KEY`, Postgres credentials, Redis host, and
  (from the Auth stage) `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
- Set `ENVIRONMENT=production` and `LOG_FORMAT=json`.

## Health & readiness

- Liveness: `GET /api/v1/health`
- Readiness: `GET /api/v1/ready` (503 until Postgres is reachable)

Wire these to your platform's liveness/readiness probes and the load balancer.

## Migrations

Migrations are automated: the one-shot `migrate` service in the compose files
runs `alembic upgrade head` against the healthy database and exits; the API and
workers gate on its successful completion (`service_completed_successfully`),
so app processes can never race an unmigrated schema. Re-running it is
idempotent — Alembic applies only pending revisions.

Outside compose (e.g. a managed platform), keep the equivalent contract: run
`alembic upgrade head` as a release step before starting new app instances.

## Operational notes

- **Worker queues**: the base compose file assigns queues explicitly
  (`worker` → `sync.high,sync.default`; `worker-maintenance` →
  `ingest,maintenance`). The production override deliberately does **not**
  restate these commands — it inherits them, so the queue topology cannot
  drift between environments.
- **Frontend build targets**: dev builds the `deps` stage (full toolchain for
  `npm run dev` over the bind mount); production overrides `build.target` to
  `runner` (the standalone image that `node server.js` expects).
- **Fail-fast configuration**: with `ENVIRONMENT=staging|production` the
  backend refuses to boot when `SECRET_KEY` is the committed example value or
  shorter than 32 characters, exiting with a clear validation error.
- **State & recovery**: `docker compose down` preserves the named
  `postgres_data`/`redis_data` volumes — the stack restarts cleanly with data
  intact. `docker compose down -v` is the deliberate full reset. Job records
  and locks live in Redis by design (Stage 1): losing Redis loses in-flight
  job state only, never calendar events or fixtures; the scheduler re-enqueues
  from `subscriptions.next_sync_at`.
