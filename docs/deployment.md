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

Run `alembic upgrade head` as a release step before rolling out new app
instances. (Automated in the deployment stage.)
