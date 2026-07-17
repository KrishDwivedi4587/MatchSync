"""Health/readiness response schemas.

These are the only API DTOs in the foundation stage. Real domain schemas arrive
with their features in later stages.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Liveness: the process is up and serving requests."""

    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    """Readiness: the process can reach its critical dependencies."""

    status: Literal["ready", "degraded"]
    database: bool
