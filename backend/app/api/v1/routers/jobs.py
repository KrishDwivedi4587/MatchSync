"""Orchestration endpoints.

    POST /jobs/sync             -> enqueue a manual (HIGH priority) sync job
    GET  /jobs                  -> list the caller's jobs
    GET  /jobs/{id}             -> one job
    POST /jobs/{id}/retry       -> re-queue a failed / dead-lettered job
    POST /jobs/{id}/cancel      -> revoke + mark cancelled
    GET  /jobs/dead-letter      -> the poison queue
    GET  /scheduler/status      -> Beat liveness + schedule definitions
    GET  /workers               -> live workers (heartbeat TTL)
    GET  /queue                 -> queue depths
    GET  /orchestration/metrics -> aggregate metrics
    GET  /orchestration/health  -> platform health

**No synchronization logic here.** These endpoints only schedule, inspect, and
control work. The engine is invoked by workers, never by the API.

Paths use `/jobs/{id}/retry` rather than `POST /jobs/retry` with a body: the job
is the resource being acted on (Stage 1, Section 11 REST conventions).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.v1.deps import CurrentUser, get_job_service, get_orchestration_service
from app.application.services.job_service import JobService
from app.application.services.orchestration_service import OrchestrationService
from app.domain.orchestration.models import Job, JobState, JobType
from app.schemas.jobs import (
    BacklogResponse,
    JobListResponse,
    JobOut,
    OrchestrationHealthResponse,
    OrchestrationMetricsResponse,
    QueueResponse,
    SchedulerStatusResponse,
    SyncJobRequest,
    WorkersResponse,
)

router = APIRouter(tags=["orchestration"])

Jobs = Annotated[JobService, Depends(get_job_service)]
Orchestration = Annotated[OrchestrationService, Depends(get_orchestration_service)]


def _job_out(job: Job) -> JobOut:
    return JobOut(
        id=job.id,
        type=job.type,
        state=job.state,
        priority=int(job.priority),
        queue=job.queue.value,
        payload=job.payload,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        error=job.error,
        error_code=job.error_code,
        created_at=job.created_at,
        queued_at=job.queued_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        next_retry_at=job.next_retry_at,
        queue_latency_seconds=job.queue_latency_seconds,
        duration_seconds=job.duration_seconds,
    )


# --- job control ------------------------------------------------------------
@router.post("/jobs/sync", response_model=JobOut, summary="Enqueue a synchronization job")
async def enqueue_sync(payload: SyncJobRequest, user: CurrentUser, jobs: Jobs) -> JobOut:
    """Queue work; do not perform it. Returns immediately with the job record."""
    if payload.subscription_id:
        job_type = JobType.SYNC_SUBSCRIPTION
        body = {
            "subscription_id": str(payload.subscription_id),
            "mode": payload.mode.value,
            "trigger": "manual",
        }
    else:
        job_type = JobType.SYNC_USER
        body = {"mode": payload.mode.value}

    job = await jobs.enqueue(
        job_type,
        payload=body,
        user_id=user.id,
        priority=payload.priority,
        countdown=payload.delay_seconds,
    )
    return _job_out(job)


@router.get("/jobs", response_model=JobListResponse, summary="List jobs")
async def list_jobs(
    user: CurrentUser,
    jobs: Jobs,
    state: Annotated[list[JobState] | None, Query()] = None,
    type: Annotated[list[JobType] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> JobListResponse:
    found = await jobs.list(
        user_id=user.id,
        states=set(state) if state else None,
        types=set(type) if type else None,
        limit=limit,
    )
    return JobListResponse(jobs=[_job_out(j) for j in found], total=len(found))


@router.get("/jobs/dead-letter", response_model=JobListResponse, summary="Dead-letter queue")
async def dead_letter(
    user: CurrentUser, jobs: Jobs, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> JobListResponse:
    found = [j for j in await jobs.dead_letter_queue(limit=limit) if j.user_id in (None, user.id)]
    return JobListResponse(jobs=[_job_out(j) for j in found], total=len(found))


@router.get("/jobs/{job_id}", response_model=JobOut, summary="Get one job")
async def get_job(job_id: uuid.UUID, user: CurrentUser, jobs: Jobs) -> JobOut:
    return _job_out(await jobs.get(job_id, user_id=user.id))


@router.post("/jobs/{job_id}/retry", response_model=JobOut, summary="Retry a job")
async def retry_job(job_id: uuid.UUID, user: CurrentUser, jobs: Jobs) -> JobOut:
    """Safe by construction: the engine is idempotent, so a replay is a no-op."""
    return _job_out(await jobs.retry(job_id, user_id=user.id))


@router.post("/jobs/{job_id}/cancel", response_model=JobOut, summary="Cancel a job")
async def cancel_job(job_id: uuid.UUID, user: CurrentUser, jobs: Jobs) -> JobOut:
    return _job_out(await jobs.cancel(job_id, user_id=user.id))


# --- platform observability ---------------------------------------------------
@router.get("/scheduler/status", response_model=SchedulerStatusResponse, summary="Scheduler status")
async def scheduler_status(
    user: CurrentUser, orchestration: Orchestration
) -> SchedulerStatusResponse:
    return SchedulerStatusResponse(**await orchestration.scheduler_status())


@router.get("/workers", response_model=WorkersResponse, summary="Live workers")
async def workers(user: CurrentUser, orchestration: Orchestration) -> WorkersResponse:
    found = await orchestration.workers()
    return WorkersResponse(workers=found, online=len(found))


@router.get("/queue", response_model=QueueResponse, summary="Queue depths")
async def queue(user: CurrentUser, orchestration: Orchestration) -> QueueResponse:
    return QueueResponse(**await orchestration.queues())


@router.get("/orchestration/backlog", response_model=BacklogResponse, summary="Sync backlog")
async def backlog(user: CurrentUser, orchestration: Orchestration) -> BacklogResponse:
    return BacklogResponse(**await orchestration.backlog())


@router.get(
    "/orchestration/metrics", response_model=OrchestrationMetricsResponse, summary="Metrics"
)
async def metrics(user: CurrentUser, orchestration: Orchestration) -> OrchestrationMetricsResponse:
    return OrchestrationMetricsResponse(metrics=await orchestration.metrics())


@router.get(
    "/orchestration/health", response_model=OrchestrationHealthResponse, summary="Platform health"
)
async def health(user: CurrentUser, orchestration: Orchestration) -> OrchestrationHealthResponse:
    return OrchestrationHealthResponse(**await orchestration.health())
