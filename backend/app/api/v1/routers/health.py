"""Health and readiness endpoints.

The only endpoints permitted in the foundation stage. They power container
health checks and load-balancer probes (Stage 1, Section 15):

- ``/health``  liveness — is the process up?
- ``/ready``   readiness — can it reach Postgres? Returns 503 when degraded.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.core.config import get_settings
from app.persistence.session import check_database_connection
from app.schemas.health import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        service=settings.app_name,
        version="0.1.0",
        environment=settings.environment,
    )


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def ready(response: Response) -> ReadinessResponse:
    db_ok = await check_database_connection()
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="degraded", database=False)
    return ReadinessResponse(status="ready", database=True)
