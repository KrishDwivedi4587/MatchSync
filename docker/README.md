# docker/

Supporting Docker assets that don't belong to a single service.

- Service Dockerfiles live with their code (`backend/Dockerfile`,
  `frontend/Dockerfile`) so each build context is minimal.
- Compose files live at the repo root (`docker-compose.yml`,
  `docker-compose.prod.yml`) — the conventional location for `docker compose`.
- This directory holds shared/init assets added later (e.g. Postgres init SQL,
  entrypoint scripts, local TLS certs).

> Minor deviation from Stage 1's `/docker` note: Dockerfiles are colocated with
> their services for smaller build contexts and simpler caching. Compose (the
> orchestration) stays discoverable at the root. This is a devex refinement, not
> an architectural change.
