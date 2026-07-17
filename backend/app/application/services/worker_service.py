"""The worker.

    load job → check cancellation → acquire lock → invoke an engine → record → release

**Workers contain no business logic.** This module never decides what a calendar
event should look like, never talks to Google, never talks to a sports API. It
calls exactly one engine method per job type and records the outcome.

The retry *decision* is computed here (from the pure policy) but the retry is
*performed* by the Celery task, which is the only thing that knows how to
reschedule a message. That keeps this class free of Celery.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.domain.orchestration.models import Job, JobState, JobType
from app.domain.orchestration.retry import RetryDecision, RetryPolicy, decide
from app.infrastructure.jobs import JobStore
from app.infrastructure.locks import LockManager, LockNotAcquiredError

logger = get_logger(__name__)

# A handler runs one job and returns a small, log-safe summary.
JobHandler = Callable[[Job], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class JobOutcome:
    job: Job
    state: JobState
    result: dict[str, Any] | None = None
    retry: RetryDecision | None = None

    @property
    def should_retry(self) -> bool:
        return self.retry is not None and self.retry.retry

    @property
    def retry_delay(self) -> float:
        return self.retry.delay_seconds if self.retry else 0.0


class WorkerService:
    def __init__(
        self,
        store: JobStore,
        locks: LockManager,
        handlers: dict[JobType, JobHandler],
        policy: RetryPolicy,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self._store = store
        self._locks = locks
        self._handlers = handlers
        self._policy = policy
        self._rng = rng

    async def run(self, job_id: uuid.UUID) -> JobOutcome:
        """Execute one job. Never raises for job-level failures."""
        job = await self._store.get(job_id)
        if job is None:
            logger.warning("worker.job_missing", job_id=str(job_id))
            raise LookupError(f"job {job_id} not found")

        # Cooperative cancellation: a revoked message may still be delivered.
        if job.state is JobState.CANCELLED:
            logger.info("worker.job_cancelled_before_start", job_id=str(job.id))
            return JobOutcome(job, JobState.CANCELLED)

        # A redelivered message for an already-finished job must not re-run it.
        if job.is_terminal:
            logger.info("worker.job_already_terminal", job_id=str(job.id), state=job.state.value)
            return JobOutcome(job, job.state)

        lock_key = job.lock_key
        if lock_key is None:
            return await self._execute(job)

        try:
            async with self._locks.guard(lock_key):
                logger.info("worker.lock_acquired", job_id=str(job.id), lock=lock_key)
                return await self._execute(job)
        except LockNotAcquiredError:
            # Another worker owns this subscription. This delivery is a correct
            # no-op — not a failure, and never retried.
            job.transition(JobState.SKIPPED)
            await self._store.save(job)
            logger.info("worker.skipped_locked", job_id=str(job.id), lock=lock_key)
            return JobOutcome(job, JobState.SKIPPED)

    async def _execute(self, job: Job) -> JobOutcome:
        handler = self._handlers.get(job.type)
        if handler is None:
            job.error = f"no handler for {job.type.value}"
            job.error_code = "no_handler"
            job.transition(JobState.FAILED)
            await self._store.save(job)
            return JobOutcome(job, JobState.FAILED)

        if job.state is not JobState.RUNNING:
            job.transition(JobState.RUNNING)
        await self._store.save(job)

        try:
            result = await handler(job)
        except Exception as exc:
            return await self._handle_failure(job, exc)

        job.error = job.error_code = None
        job.transition(JobState.SUCCEEDED)
        await self._store.save(job)
        logger.info(
            "worker.job_succeeded",
            job_id=str(job.id),
            type=job.type.value,
            attempts=job.attempts,
            duration_seconds=job.duration_seconds,
            queue_latency_seconds=job.queue_latency_seconds,
        )
        return JobOutcome(job, JobState.SUCCEEDED, result=result)

    async def _handle_failure(self, job: Job, exc: BaseException) -> JobOutcome:
        decision = decide(exc, job.attempts, self._policy, rng=self._rng)
        job.error = str(exc)[:500]
        job.error_code = getattr(exc, "code", exc.__class__.__name__)

        if decision.retry:
            job.next_retry_at = decision.next_retry_at
            job.transition(JobState.RETRYING)
            await self._store.save(job)
            logger.warning(
                "worker.job_retrying",
                job_id=str(job.id),
                type=job.type.value,
                attempts=job.attempts,
                kind=decision.kind.value,
                delay_seconds=decision.delay_seconds,
                error_code=job.error_code,
            )
            return JobOutcome(job, JobState.RETRYING, retry=decision)

        # Permanent, or the retry budget is spent: dead-letter it. A poison
        # message can never loop forever.
        job.transition(JobState.FAILED)
        job.transition(JobState.DEAD_LETTER)
        await self._store.save(job)
        logger.error(
            "worker.job_dead_lettered",
            job_id=str(job.id),
            type=job.type.value,
            attempts=job.attempts,
            kind=decision.kind.value,
            error_code=job.error_code,
        )
        return JobOutcome(job, JobState.DEAD_LETTER, retry=decision)

    async def requeue_after_retry(self, job: Job, celery_task_id: str | None = None) -> Job:
        """Called by the task layer once the retry message is scheduled."""
        job.transition(JobState.QUEUED)
        if celery_task_id:
            job.celery_task_id = celery_task_id
        await self._store.save(job)
        return job
