"""WorkerService and JobService tests.

These pin the orchestration guarantees: at-most-one active sync per subscription,
duplicate deliveries are no-ops, crashes recover, retries back off, poison
messages dead-letter, and cancellation is honoured.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fakeredis import aioredis as fake_aioredis

from app.application.services.job_service import (
    JobNotCancellableError,
    JobNotFoundError,
    JobNotRetryableError,
    JobService,
)
from app.application.services.worker_service import WorkerService
from app.domain.orchestration.models import Job, JobPriority, JobState, JobType
from app.domain.orchestration.retry import RetryPolicy
from app.exceptions.base import RetryableError
from app.exceptions.calendar import CalendarReauthRequiredError
from app.exceptions.provider import RateLimitError
from app.infrastructure.jobs import JobStore
from app.infrastructure.locks import LockManager


class FakeDispatcher:
    """Records dispatches instead of touching a broker."""

    def __init__(self) -> None:
        self.dispatched: list[tuple[uuid.UUID, float]] = []
        self.revoked: list[str] = []

    def dispatch(self, job: Job, *, countdown: float = 0.0) -> str:
        self.dispatched.append((job.id, countdown))
        return f"celery-{job.id}"

    def revoke(self, celery_task_id: str) -> None:
        self.revoked.append(celery_task_id)


@pytest_asyncio.fixture
async def redis():
    client = fake_aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def ctx(redis):
    store = JobStore(redis, retention_seconds=3600)
    locks = LockManager(redis, ttl_seconds=5)
    dispatcher = FakeDispatcher()
    jobs = JobService(store, dispatcher)
    return store, locks, dispatcher, jobs


def worker_with(store, locks, handler, *, max_attempts: int = 3) -> WorkerService:
    return WorkerService(
        store,
        locks,
        {JobType.SYNC_SUBSCRIPTION: handler},
        RetryPolicy(max_attempts=max_attempts, base_delay_seconds=1, max_delay_seconds=4),
        rng=random.Random(42),
    )


async def _enqueue(jobs: JobService, **kw) -> Job:
    return await jobs.enqueue(
        JobType.SYNC_SUBSCRIPTION,
        payload={"subscription_id": str(uuid.uuid4())},
        user_id=uuid.uuid4(),
        **kw,
    )


# --- happy path ---------------------------------------------------------------
async def test_worker_runs_the_handler_and_succeeds(ctx) -> None:
    store, locks, _dispatcher, jobs = ctx
    calls: list[uuid.UUID] = []

    async def handler(job: Job) -> dict:
        calls.append(job.id)
        return {"created": 1}

    job = await _enqueue(jobs)
    outcome = await worker_with(store, locks, handler).run(job.id)

    assert outcome.state is JobState.SUCCEEDED
    assert calls == [job.id]
    assert outcome.result == {"created": 1}

    stored = await store.get(job.id)
    assert stored.state is JobState.SUCCEEDED and stored.attempts == 1
    assert stored.duration_seconds is not None


async def test_enqueue_dispatches_to_the_broker(ctx) -> None:
    _, _, dispatcher, jobs = ctx
    job = await _enqueue(jobs, priority=JobPriority.HIGH)
    assert dispatcher.dispatched[0][0] == job.id
    assert job.state is JobState.QUEUED
    assert job.celery_task_id == f"celery-{job.id}"


# --- THE orchestration guarantee ----------------------------------------------
async def test_at_most_one_worker_runs_a_subscription(ctx) -> None:
    """Two workers, same subscription, concurrently: exactly one executes."""
    store, locks, _, jobs = ctx
    running = asyncio.Event()
    concurrent = 0
    peak = 0

    async def slow_handler(job: Job) -> dict:
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        running.set()
        await asyncio.sleep(0.1)
        concurrent -= 1
        return {}

    subscription_id = str(uuid.uuid4())
    a = await jobs.enqueue(JobType.SYNC_SUBSCRIPTION, payload={"subscription_id": subscription_id})
    b = await jobs.enqueue(JobType.SYNC_SUBSCRIPTION, payload={"subscription_id": subscription_id})

    worker = worker_with(store, locks, slow_handler)
    outcomes = await asyncio.gather(worker.run(a.id), worker.run(b.id))

    states = sorted(o.state.value for o in outcomes)
    assert states == ["skipped", "succeeded"]
    assert peak == 1  # never two at once


async def test_skipped_job_is_terminal_and_not_retried(ctx) -> None:
    store, locks, _, jobs = ctx

    async def handler(job: Job) -> dict:  # pragma: no cover - must not run
        raise AssertionError("handler should not run while the lock is held")

    subscription_id = str(uuid.uuid4())
    job = await jobs.enqueue(
        JobType.SYNC_SUBSCRIPTION, payload={"subscription_id": subscription_id}
    )
    await locks.acquire(f"sync:subscription:{subscription_id}")  # someone else holds it

    outcome = await worker_with(store, locks, handler).run(job.id)
    assert outcome.state is JobState.SKIPPED
    assert outcome.should_retry is False
    assert (await store.get(job.id)).is_terminal


async def test_duplicate_delivery_of_a_finished_job_is_a_no_op(ctx) -> None:
    """acks_late can redeliver a message whose job already succeeded."""
    store, locks, _, jobs = ctx
    calls = 0

    async def handler(job: Job) -> dict:
        nonlocal calls
        calls += 1
        return {}

    job = await _enqueue(jobs)
    worker = worker_with(store, locks, handler)
    await worker.run(job.id)
    await worker.run(job.id)  # redelivery

    assert calls == 1


# --- retries and dead-lettering ------------------------------------------------
async def test_transient_failure_schedules_a_retry(ctx) -> None:
    store, locks, _, jobs = ctx

    async def failing(job: Job) -> dict:
        raise RetryableError("upstream blip")

    job = await _enqueue(jobs)
    outcome = await worker_with(store, locks, failing).run(job.id)

    assert outcome.state is JobState.RETRYING
    assert outcome.should_retry is True
    assert 0 < outcome.retry_delay <= 4
    stored = await store.get(job.id)
    assert stored.next_retry_at is not None
    assert stored.error_code == "retryable_error"


async def test_rate_limited_failure_waits_past_the_throttle_window(ctx) -> None:
    store, locks, _, jobs = ctx

    async def throttled(job: Job) -> dict:
        raise RateLimitError()

    worker = WorkerService(
        store,
        locks,
        {JobType.SYNC_SUBSCRIPTION: throttled},
        RetryPolicy(max_attempts=3, base_delay_seconds=1, rate_limit_floor_seconds=60),
    )
    job = await _enqueue(jobs)
    outcome = await worker.run(job.id)
    assert outcome.retry_delay >= 60


async def test_permanent_failure_dead_letters_immediately(ctx) -> None:
    """Reconnect-required cannot be fixed by retrying; do not burn quota."""
    store, locks, _, jobs = ctx

    async def revoked(job: Job) -> dict:
        raise CalendarReauthRequiredError()

    job = await _enqueue(jobs)
    outcome = await worker_with(store, locks, revoked).run(job.id)

    assert outcome.state is JobState.DEAD_LETTER
    assert outcome.should_retry is False
    assert (await store.get(job.id)).attempts == 1  # not retried even once
    assert [j.id for j in await store.list_dead_letter()] == [job.id]


async def test_retry_exhaustion_dead_letters_the_poison_message(ctx) -> None:
    store, locks, _, jobs = ctx

    async def always_failing(job: Job) -> dict:
        raise RetryableError("permanently broken upstream")

    job = await _enqueue(jobs)
    worker = worker_with(store, locks, always_failing, max_attempts=3)

    for _ in range(2):
        outcome = await worker.run(job.id)
        assert outcome.state is JobState.RETRYING
        await worker.requeue_after_retry(outcome.job)

    final = await worker.run(job.id)
    assert final.state is JobState.DEAD_LETTER
    assert (await store.get(job.id)).attempts == 3


async def test_missing_handler_fails_the_job_without_crashing_the_worker(ctx) -> None:
    store, locks, _, jobs = ctx
    worker = WorkerService(store, locks, {}, RetryPolicy())
    job = await _enqueue(jobs)
    outcome = await worker.run(job.id)
    assert outcome.state is JobState.FAILED
    assert (await store.get(job.id)).error_code == "no_handler"


# --- cancellation ---------------------------------------------------------------
async def test_cancelled_job_is_never_executed(ctx) -> None:
    store, locks, dispatcher, jobs = ctx
    calls = 0

    async def handler(job: Job) -> dict:
        nonlocal calls
        calls += 1
        return {}

    job = await _enqueue(jobs)
    await jobs.cancel(job.id, user_id=job.user_id)
    assert dispatcher.revoked == [f"celery-{job.id}"]

    # A revoked message may still be delivered; the worker re-reads the state.
    outcome = await worker_with(store, locks, handler).run(job.id)
    assert outcome.state is JobState.CANCELLED
    assert calls == 0


async def test_cannot_cancel_a_finished_job(ctx) -> None:
    store, locks, _, jobs = ctx

    async def handler(job: Job) -> dict:
        return {}

    job = await _enqueue(jobs)
    await worker_with(store, locks, handler).run(job.id)
    with pytest.raises(JobNotCancellableError):
        await jobs.cancel(job.id, user_id=job.user_id)


# --- retry / ownership -----------------------------------------------------------
async def test_manual_retry_requeues_and_restores_the_budget(ctx) -> None:
    store, locks, dispatcher, jobs = ctx

    async def failing(job: Job) -> dict:
        raise CalendarReauthRequiredError()

    job = await _enqueue(jobs)
    await worker_with(store, locks, failing).run(job.id)
    assert (await store.get(job.id)).state is JobState.DEAD_LETTER

    retried = await jobs.retry(job.id, user_id=job.user_id)
    assert retried.state is JobState.QUEUED
    assert retried.attempts == 0 and retried.error is None
    assert await store.list_dead_letter() == []  # removed from the poison queue
    assert len(dispatcher.dispatched) == 2


async def test_cannot_retry_a_successful_job(ctx) -> None:
    store, locks, _, jobs = ctx

    async def handler(job: Job) -> dict:
        return {}

    job = await _enqueue(jobs)
    await worker_with(store, locks, handler).run(job.id)
    with pytest.raises(JobNotRetryableError):
        await jobs.retry(job.id, user_id=job.user_id)


async def test_a_job_is_not_visible_to_another_user(ctx) -> None:
    _, _, _, jobs = ctx
    job = await _enqueue(jobs)
    with pytest.raises(JobNotFoundError):
        await jobs.get(job.id, user_id=uuid.uuid4())


async def test_unknown_job_raises(ctx) -> None:
    _, _, _, jobs = ctx
    with pytest.raises(JobNotFoundError):
        await jobs.get(uuid.uuid4())


# --- crash recovery ---------------------------------------------------------------
async def test_worker_crash_leaves_a_stuck_job_that_is_reaped(ctx) -> None:
    """A SIGKILLed worker never transitions its job. The reaper repairs the record."""
    store, _locks, _, jobs = ctx
    job = await _enqueue(jobs)
    job.transition(JobState.RUNNING)
    job.started_at = datetime.now(UTC) - timedelta(hours=2)
    await store.save(job)

    reaped = await jobs.mark_stuck_as_failed(older_than_seconds=1800)
    assert [j.id for j in reaped] == [job.id]

    stored = await store.get(job.id)
    assert stored.state is JobState.FAILED
    assert stored.error_code == "worker_lost"
    # And it is now retryable.
    assert (await jobs.retry(job.id, user_id=job.user_id)).state is JobState.QUEUED


async def test_a_crashed_worker_releases_its_lock_by_expiry(redis) -> None:
    """No permanent lock on a lost worker."""
    store = JobStore(redis)
    manager = LockManager(redis, ttl_seconds=1)
    handle = await manager.acquire("sync:subscription:x")
    assert handle is not None

    await asyncio.sleep(1.2)  # the process died; nothing released the lock

    async def handler(job: Job) -> dict:
        return {"ok": True}

    jobs = JobService(store, FakeDispatcher())
    job = await jobs.enqueue(JobType.SYNC_SUBSCRIPTION, payload={"subscription_id": "x"})
    outcome = await worker_with(store, manager, handler).run(job.id)
    assert outcome.state is JobState.SUCCEEDED  # the lock was reclaimed


async def test_large_queue_of_jobs_all_complete(ctx) -> None:
    store, locks, _, jobs = ctx
    done: list[uuid.UUID] = []

    async def handler(job: Job) -> dict:
        done.append(job.id)
        return {}

    created = [await _enqueue(jobs) for _ in range(50)]
    worker = worker_with(store, locks, handler)
    for job in created:
        await worker.run(job.id)

    assert len(done) == 50
    states = [(await store.get(j.id)).state for j in created]
    assert all(state is JobState.SUCCEEDED for state in states)
