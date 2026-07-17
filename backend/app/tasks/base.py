"""Task-layer plumbing: async bridge, Celery dispatcher, worker heartbeat.

Celery tasks are synchronous callables; every engine in this codebase is async.
``run_async`` bridges them with a fresh event loop per task, which is correct for
prefork workers (one task at a time per process).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.orchestration.models import Job, JobPriority
from app.infrastructure.heartbeat import HeartbeatRegistry
from app.infrastructure.jobs import JobStore
from app.infrastructure.locks import LockManager

logger = get_logger(__name__)

T = TypeVar("T")

# Task names, mapped from the job's queue by the dispatcher.
TASK_SYNC_SUBSCRIPTION = "orchestration.sync_subscription"
TASK_SYNC_SUBSCRIPTION_PRIORITY = "orchestration.sync_subscription_priority"
TASK_SYNC_USER = "orchestration.sync_user"
TASK_RECONCILE = "orchestration.reconcile"
TASK_FIXTURE_IMPORT = "orchestration.fixture_import"
TASK_METADATA_REFRESH = "orchestration.metadata_refresh"
TASK_CLEANUP = "orchestration.cleanup"
TASK_HEALTH_CHECK = "orchestration.health_check"


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine to completion from a synchronous Celery task.

    Every caller passes the result of calling an ``async def`` directly, which
    is always a coroutine — exactly what ``asyncio.run`` requires.
    """
    return asyncio.run(coro)


# --- Redis singletons for the worker process --------------------------------
_redis: aioredis.Redis | None = None


def worker_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        # redis-py 6.4 leaves ``from_url`` unannotated; it is the documented
        # constructor, so the untyped call is unavoidable here.
        _redis = aioredis.from_url(  # type: ignore[no-untyped-call]
            get_settings().redis_url, encoding="utf-8", decode_responses=True
        )
    return _redis


def job_store() -> JobStore:
    return JobStore(worker_redis(), retention_seconds=get_settings().job_retention_seconds)


def lock_manager() -> LockManager:
    return LockManager(worker_redis(), ttl_seconds=get_settings().lock_ttl_seconds)


def heartbeats() -> HeartbeatRegistry:
    return HeartbeatRegistry(worker_redis(), ttl_seconds=get_settings().heartbeat_ttl_seconds)


def register_worker_heartbeat(name: str, **info: Any) -> None:
    run_async(heartbeats().worker_beat(name, **info))


def deregister_worker_heartbeat(name: str) -> None:
    run_async(heartbeats().worker_gone(name))


# --- Dispatcher --------------------------------------------------------------
def _task_name_for(job: Job) -> str:
    from app.domain.orchestration.models import JobType

    if job.type is JobType.SYNC_SUBSCRIPTION:
        return (
            TASK_SYNC_SUBSCRIPTION_PRIORITY
            if job.priority >= JobPriority.HIGH
            else TASK_SYNC_SUBSCRIPTION
        )
    return {
        JobType.SYNC_USER: TASK_SYNC_USER,
        JobType.RECONCILE: TASK_RECONCILE,
        JobType.FIXTURE_IMPORT: TASK_FIXTURE_IMPORT,
        JobType.METADATA_REFRESH: TASK_METADATA_REFRESH,
        JobType.CLEANUP: TASK_CLEANUP,
        JobType.HEALTH_CHECK: TASK_HEALTH_CHECK,
    }[job.type]


class CeleryDispatcher:
    """The only Celery-aware seam in the application layer."""

    def dispatch(self, job: Job, *, countdown: float = 0.0) -> str:
        from app.worker import celery_app

        result = celery_app.send_task(
            _task_name_for(job),
            args=[str(job.id)],
            queue=job.queue.value,
            priority=int(job.priority),
            countdown=countdown or None,
        )
        return result.id

    def revoke(self, celery_task_id: str) -> None:
        from app.worker import celery_app

        celery_app.control.revoke(celery_task_id, terminate=False)
