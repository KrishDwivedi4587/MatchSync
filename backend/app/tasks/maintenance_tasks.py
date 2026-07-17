"""Maintenance tasks: metadata refresh, fixture import, health, cleanup, reaping.

All run on the ``maintenance``/``ingest`` queues so a slow provider refresh can
never starve a user's manual sync.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import Task

from app.application.services.job_service import JobService
from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.orchestration.models import JobPriority, JobType
from app.tasks.base import CeleryDispatcher, heartbeats, job_store, run_async
from app.tasks.sync_tasks import _execute
from app.worker import celery_app

logger = get_logger(__name__)


def _self_enqueue(job_type: JobType, payload: dict[str, Any] | None = None) -> str:
    """Beat entries create a Job record, then let a worker execute it.

    Beat itself never does work — it only schedules. This keeps every unit of
    work observable in /jobs, retryable, and cancellable.
    """

    async def _create() -> str:
        service = JobService(job_store(), CeleryDispatcher())
        job = await service.enqueue(
            job_type,
            payload=payload or {},
            priority=JobPriority.LOW,
            max_attempts=get_settings().job_max_attempts,
        )
        return str(job.id)

    return run_async(_create())


@celery_app.task(bind=True, name="orchestration.metadata_refresh", acks_late=True)
def metadata_refresh(self: Task, job_id: str | None = None) -> dict[str, Any]:
    """Beat calls this with no argument; it enqueues a real job and returns."""
    if job_id is None:
        return {"enqueued": _self_enqueue(JobType.METADATA_REFRESH)}
    return _execute(self, job_id)


@celery_app.task(bind=True, name="orchestration.fixture_import", acks_late=True)
def fixture_import(self: Task, job_id: str | None = None) -> dict[str, Any]:
    if job_id is None:
        registry_sports = _all_sport_keys()
        return {
            "enqueued": [
                _self_enqueue(JobType.FIXTURE_IMPORT, {"sport": s}) for s in registry_sports
            ]
        }
    return _execute(self, job_id)


def _all_sport_keys() -> list[str]:
    from app.infrastructure.providers.registry import get_sports_registry

    return get_sports_registry(get_settings()).sport_keys()


@celery_app.task(bind=True, name="orchestration.health_check")
def health_check(self: Task, job_id: str | None = None) -> dict[str, Any]:
    """Scheduler liveness beacon. /scheduler/status reads the key it writes.

    Accepts an optional ``job_id`` because the dispatcher maps
    ``JobType.HEALTH_CHECK`` to this task and always sends one. Nothing enqueues
    such jobs today, but a dispatched one must be *recorded* as failed
    (no registered handler) rather than crash the task with a TypeError.
    """
    if job_id is not None:
        return _execute(self, job_id)

    async def _beat() -> dict[str, Any]:
        beats = heartbeats()
        await beats.scheduler_beat(last_task="health_check")
        return {"redis": await beats.redis_healthy(), "queues": await beats.queue_depths()}

    result = run_async(_beat())
    logger.info("scheduler.health_check", **{"redis": result["redis"]})
    return result


@celery_app.task(name="orchestration.detect_stuck_jobs")
def detect_stuck_jobs() -> dict[str, int]:
    """Reap RUNNING jobs whose worker died.

    The broker redelivers the message (``task_reject_on_worker_lost``), so the
    work still happens; this repairs the *record* so the dashboard is truthful and
    the job becomes retryable.
    """
    settings = get_settings()

    async def _reap() -> dict[str, int]:
        service = JobService(job_store(), CeleryDispatcher())
        reaped = await service.mark_stuck_as_failed(
            older_than_seconds=settings.stuck_job_threshold_seconds
        )
        return {"reaped": len(reaped)}

    result = run_async(_reap())
    if result["reaped"]:
        logger.warning("scheduler.stuck_jobs_reaped", **result)
    return result


@celery_app.task(bind=True, name="orchestration.cleanup")
def cleanup(self: Task, job_id: str | None = None) -> dict[str, Any]:
    """Drop job index entries whose documents have already expired.

    ``job_id`` tolerance mirrors ``health_check`` (see its docstring).
    """
    if job_id is not None:
        return _execute(self, job_id)
    settings = get_settings()

    async def _prune() -> dict[str, int]:
        service = JobService(job_store(), CeleryDispatcher())
        pruned = await service.prune(older_than_seconds=settings.job_retention_seconds)
        return {"pruned": pruned}

    result = run_async(_prune())
    logger.info("scheduler.cleanup", **result)
    return result


__all__ = [
    "cleanup",
    "detect_stuck_jobs",
    "fixture_import",
    "health_check",
    "metadata_refresh",
    "uuid",
]
