"""Synchronization tasks.

Celery tasks are deliberately thin. Each one:
  1. builds the ``WorkerService``,
  2. asks it to run the job,
  3. translates a retry *decision* into a Celery ``retry()`` call.

The retry decision is computed by the pure policy in ``domain/orchestration``;
the task only knows how to reschedule a message. No synchronization logic here.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from celery import Task
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.job_service import JobService
from app.application.services.worker_service import JobOutcome, WorkerService
from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.orchestration.models import Job, JobPriority, JobState, JobType
from app.domain.orchestration.retry import RetryPolicy
from app.domain.value_objects.enums import SyncTrigger
from app.persistence.repositories.subscription import SubscriptionRepository
from app.persistence.session import async_session_factory
from app.tasks import handlers
from app.tasks.base import (
    CeleryDispatcher,
    heartbeats,
    job_store,
    lock_manager,
    run_async,
    worker_redis,
)
from app.worker import celery_app

logger = get_logger(__name__)


def _policy() -> RetryPolicy:
    settings = get_settings()
    return RetryPolicy(
        max_attempts=settings.job_max_attempts,
        base_delay_seconds=settings.job_retry_base_delay_seconds,
        max_delay_seconds=settings.job_retry_max_delay_seconds,
        rate_limit_floor_seconds=settings.job_rate_limit_floor_seconds,
    )


def build_worker_service() -> WorkerService:
    """Wire the job-type -> engine-call handler table."""
    store = job_store()
    redis = worker_redis()

    async def _with_session(
        fn: Callable[[AsyncSession, Job], Awaitable[dict[str, Any]]], job: Job
    ) -> dict[str, Any]:
        async with async_session_factory() as session:
            return await fn(session, job)

    async def _sync_subscription(job: Job) -> dict[str, Any]:
        return await _with_session(handlers.handle_sync_subscription, job)

    async def _sync_user(job: Job) -> dict[str, Any]:
        return await _with_session(handlers.handle_sync_user, job)

    async def _reconcile(job: Job) -> dict[str, Any]:
        return await _with_session(handlers.handle_reconcile, job)

    async def _metadata(job: Job) -> dict[str, Any]:
        return await _with_session(handlers.handle_metadata_refresh, job)

    async def _fixture_import(job: Job) -> dict[str, Any]:
        async with async_session_factory() as session:
            return await handlers.handle_fixture_import(session, job, redis)

    return WorkerService(
        store,
        lock_manager(),
        {
            JobType.SYNC_SUBSCRIPTION: _sync_subscription,
            JobType.SYNC_USER: _sync_user,
            JobType.RECONCILE: _reconcile,
            JobType.METADATA_REFRESH: _metadata,
            JobType.FIXTURE_IMPORT: _fixture_import,
        },
        _policy(),
    )


def _execute(task: Task[..., dict[str, Any]], job_id: str) -> dict[str, Any]:
    """Shared body: run the job, then honour its retry decision."""
    worker = build_worker_service()
    outcome: JobOutcome = run_async(worker.run(uuid.UUID(job_id)))

    if outcome.state is JobState.RETRYING and outcome.should_retry:
        # Re-queue through Celery. `requeue_after_retry` flips the job back to
        # QUEUED so the dashboard shows it waiting, not stuck in RETRYING.
        run_async(worker.requeue_after_retry(outcome.job, task.request.id))
        raise task.retry(countdown=outcome.retry_delay, max_retries=None)

    return {
        "job_id": job_id,
        "state": outcome.state.value,
        "result": outcome.result,
    }


@celery_app.task(bind=True, name="orchestration.sync_subscription", acks_late=True)
def sync_subscription(self: Task[[str], dict[str, Any]], job_id: str) -> dict[str, Any]:
    return _execute(self, job_id)


@celery_app.task(bind=True, name="orchestration.sync_subscription_priority", acks_late=True)
def sync_subscription_priority(self: Task[[str], dict[str, Any]], job_id: str) -> dict[str, Any]:
    """Same body, separate queue: manual syncs never wait behind scheduled ones."""
    return _execute(self, job_id)


@celery_app.task(bind=True, name="orchestration.sync_user", acks_late=True)
def sync_user(self: Task[[str], dict[str, Any]], job_id: str) -> dict[str, Any]:
    return _execute(self, job_id)


@celery_app.task(bind=True, name="orchestration.reconcile", acks_late=True)
def reconcile(self: Task[[str], dict[str, Any]], job_id: str) -> dict[str, Any]:
    return _execute(self, job_id)


# --- the scheduler tick -------------------------------------------------------
@celery_app.task(name="orchestration.scan_due_subscriptions")
def scan_due_subscriptions() -> dict[str, int]:
    """Beat's core tick: enqueue one job per due subscription.

    It performs **no synchronization**. It reads ``subscriptions.next_sync_at``
    (maintained by the Stage 8 engine) and hands each id to the queue.

    A single scheduler entry serves 100k subscriptions; per-subscription Beat
    entries would not scale (Stage 1, Section 10).
    """
    settings = get_settings()

    async def _scan() -> dict[str, int]:
        from datetime import UTC, datetime

        service = JobService(job_store(), CeleryDispatcher())
        enqueued = 0
        async with async_session_factory() as session:
            due = await SubscriptionRepository(session).list_due(
                datetime.now(UTC), limit=settings.scheduler_scan_batch_size
            )
            for subscription in due:
                await service.enqueue(
                    JobType.SYNC_SUBSCRIPTION,
                    payload={
                        "subscription_id": str(subscription.id),
                        "trigger": SyncTrigger.SCHEDULED.value,
                    },
                    user_id=subscription.user_id,
                    priority=JobPriority.NORMAL,
                    max_attempts=settings.job_max_attempts,
                )
                enqueued += 1

        await heartbeats().scheduler_beat(last_scan="scan_due_subscriptions", enqueued=enqueued)
        return {"due": len(due), "enqueued": enqueued}

    result = run_async(_scan())
    logger.info("scheduler.scan_due_subscriptions", **result)
    return result


@celery_app.task(name="orchestration.heartbeat")
def heartbeat() -> str:
    """Kept from Stage 2: proves scheduler -> broker -> worker end to end."""
    logger.info("worker_heartbeat")
    return "ok"
