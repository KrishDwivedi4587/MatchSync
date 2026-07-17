"""Job service: enqueue, inspect, retry, cancel.

Owns the job *lifecycle*, not the work. Dispatch to the broker goes through a
``TaskDispatcher`` port so the whole service is testable without a running
Celery or Redis broker.
"""

from __future__ import annotations

import builtins
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core.logging import get_logger
from app.domain.orchestration.models import (
    TERMINAL_STATES,
    Job,
    JobPriority,
    JobState,
    JobType,
)
from app.exceptions.base import AppError, ConflictError, NotFoundError
from app.infrastructure.jobs import JobStore

logger = get_logger(__name__)


class JobNotFoundError(NotFoundError):
    code = "job_not_found"
    message = "The job does not exist."


class JobNotRetryableError(ConflictError):
    code = "job_not_retryable"
    message = "Only failed, dead-lettered, or cancelled jobs can be retried."


class JobNotCancellableError(ConflictError):
    code = "job_not_cancellable"
    message = "This job has already finished."


class TaskDispatcher(Protocol):
    """Sends a job to the broker. The only Celery-aware seam."""

    def dispatch(self, job: Job, *, countdown: float = 0.0) -> str: ...
    def revoke(self, celery_task_id: str) -> None: ...


class JobService:
    def __init__(self, store: JobStore, dispatcher: TaskDispatcher) -> None:
        self._store = store
        self._dispatcher = dispatcher

    # --- creation ---------------------------------------------------------
    async def enqueue(
        self,
        job_type: JobType,
        *,
        payload: dict[str, Any] | None = None,
        user_id: uuid.UUID | None = None,
        priority: JobPriority = JobPriority.NORMAL,
        max_attempts: int = 5,
        countdown: float = 0.0,
    ) -> Job:
        """Create a job and hand it to the broker."""
        job = Job(
            type=job_type,
            payload=payload or {},
            user_id=user_id,
            priority=priority,
            max_attempts=max_attempts,
        )
        await self._store.save(job)

        job.transition(JobState.QUEUED)
        job.celery_task_id = self._dispatcher.dispatch(job, countdown=countdown)
        await self._store.save(job)

        logger.info(
            "job.created",
            job_id=str(job.id),
            type=job.type.value,
            queue=job.queue.value,
            priority=int(job.priority),
            countdown=countdown,
        )
        return job

    # --- reads ------------------------------------------------------------
    async def get(self, job_id: uuid.UUID, *, user_id: uuid.UUID | None = None) -> Job:
        job = await self._store.get(job_id)
        if job is None:
            raise JobNotFoundError()
        # Ownership: a user-scoped job is only visible to its owner.
        if user_id is not None and job.user_id is not None and job.user_id != user_id:
            raise JobNotFoundError()
        return job

    async def list(
        self,
        *,
        user_id: uuid.UUID | None = None,
        states: set[JobState] | None = None,
        types: set[JobType] | None = None,
        limit: int = 50,
    ) -> list[Job]:
        return await self._store.list(user_id=user_id, states=states, types=types, limit=limit)

    # ``builtins.list``: the ``list`` method above shadows the builtin in
    # this class body's later def-signature annotations.
    async def dead_letter_queue(self, *, limit: int = 50) -> builtins.list[Job]:
        return await self._store.list_dead_letter(limit=limit)

    # --- control ----------------------------------------------------------
    async def retry(self, job_id: uuid.UUID, *, user_id: uuid.UUID | None = None) -> Job:
        """Re-queue a finished-but-unsuccessful job.

        Safe by construction: the synchronization engine is idempotent, so a
        replay of an already-applied job produces an empty plan.
        """
        job = await self.get(job_id, user_id=user_id)
        if job.state not in (JobState.FAILED, JobState.DEAD_LETTER, JobState.CANCELLED):
            raise JobNotRetryableError()

        job.attempts = 0  # a manual retry restores the full budget
        job.error = job.error_code = None
        job.finished_at = None
        job.transition(JobState.QUEUED)
        job.celery_task_id = self._dispatcher.dispatch(job)
        await self._store.save(job)

        logger.info("job.retried", job_id=str(job.id), type=job.type.value)
        return job

    async def cancel(self, job_id: uuid.UUID, *, user_id: uuid.UUID | None = None) -> Job:
        """Cancel a job that has not finished.

        Cancellation is cooperative *and* forceful: we revoke the broker message
        and mark the job CANCELLED. A worker that already picked it up re-reads
        the state before executing and exits without touching the calendar.
        """
        job = await self.get(job_id, user_id=user_id)
        if job.is_terminal:
            raise JobNotCancellableError()

        if job.celery_task_id:
            self._dispatcher.revoke(job.celery_task_id)
        job.transition(JobState.CANCELLED)
        await self._store.save(job)

        logger.info("job.cancelled", job_id=str(job.id), type=job.type.value)
        return job

    # --- worker-side helpers ----------------------------------------------
    async def mark_stuck_as_failed(self, *, older_than_seconds: int) -> builtins.list[Job]:
        """Reap RUNNING jobs whose worker died mid-flight.

        The broker will redeliver the message (``task_reject_on_worker_lost``), so
        the work still happens; this only repairs the *record*.
        """
        reaped: list[Job] = []
        for job in await self._store.list_stuck(older_than_seconds=older_than_seconds):
            job.error = "worker lost while running"
            job.error_code = "worker_lost"
            job.transition(JobState.FAILED)
            await self._store.save(job)
            reaped.append(job)
            logger.warning("job.stuck_reaped", job_id=str(job.id), type=job.type.value)
        return reaped

    async def prune(self, *, older_than_seconds: int) -> int:
        return await self._store.prune(older_than_seconds=older_than_seconds)


def is_terminal(state: JobState) -> bool:
    return state in TERMINAL_STATES or state is JobState.DEAD_LETTER


__all__ = [
    "UTC",
    "AppError",
    "JobNotCancellableError",
    "JobNotFoundError",
    "JobNotRetryableError",
    "JobService",
    "TaskDispatcher",
    "datetime",
]
