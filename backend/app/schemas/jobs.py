"""Orchestration API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.orchestration.models import JobPriority, JobState, JobType
from app.domain.sync.models import SyncMode


class SyncJobRequest(BaseModel):
    """Enqueue a manual synchronization. Manual work is HIGH priority."""

    subscription_id: uuid.UUID | None = None
    mode: SyncMode = SyncMode.INCREMENTAL
    priority: JobPriority = JobPriority.HIGH
    delay_seconds: float = Field(default=0.0, ge=0, le=86_400)


class JobOut(BaseModel):
    id: uuid.UUID
    type: JobType
    state: JobState
    priority: int
    queue: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    error: str | None = None
    error_code: str | None = None
    created_at: datetime
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    next_retry_at: datetime | None = None
    queue_latency_seconds: float | None = None
    duration_seconds: float | None = None


class JobListResponse(BaseModel):
    jobs: list[JobOut]
    total: int


class WorkerOut(BaseModel):
    name: str
    seen_at: str
    state: str | None = None


class WorkersResponse(BaseModel):
    workers: list[dict[str, Any]]
    online: int


class QueueResponse(BaseModel):
    depths: dict[str, int]
    total: int


class SchedulerJobOut(BaseModel):
    key: str
    name: str
    schedule: str
    status: str
    last_run_at: str | None = None
    next_run_at: str | None = None


class SchedulerStatusResponse(BaseModel):
    alive: bool
    last_seen_at: str | None = None
    jobs: list[SchedulerJobOut]


class BacklogResponse(BaseModel):
    due_subscriptions: int
    oldest_due_at: str | None = None
    max_scheduling_delay_seconds: float


class OrchestrationMetricsResponse(BaseModel):
    metrics: dict[str, Any]


class OrchestrationHealthResponse(BaseModel):
    healthy: bool
    redis: bool
    workers_online: int
    scheduler_alive: bool
    stuck_jobs: int
