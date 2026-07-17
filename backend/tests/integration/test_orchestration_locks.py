"""Distributed lock and job store tests, against an in-memory Redis."""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from fakeredis import aioredis as fake_aioredis

from app.domain.orchestration.models import Job, JobState, JobType
from app.infrastructure.jobs import JobStore
from app.infrastructure.locks import LockManager, LockNotAcquiredError


@pytest_asyncio.fixture
async def redis():
    client = fake_aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def locks(redis):
    return LockManager(redis, ttl_seconds=2)


@pytest_asyncio.fixture
async def store(redis):
    return JobStore(redis, retention_seconds=3600)


# --- mutual exclusion --------------------------------------------------------
async def test_second_acquire_of_the_same_lock_fails(locks) -> None:
    first = await locks.acquire("sync:subscription:1")
    assert first is not None
    assert await locks.acquire("sync:subscription:1") is None


async def test_different_locks_do_not_contend(locks) -> None:
    assert await locks.acquire("sync:subscription:1") is not None
    assert await locks.acquire("sync:subscription:2") is not None


async def test_release_frees_the_lock(locks) -> None:
    handle = await locks.acquire("k")
    assert await locks.release(handle) is True
    assert await locks.acquire("k") is not None


async def test_a_worker_cannot_release_a_lock_it_no_longer_owns(locks, redis) -> None:
    """The ownership token is the whole point: no stealing, no accidental unlock."""
    stale = await locks.acquire("k")
    await locks.release(stale)
    fresh = await locks.acquire("k")  # someone else now owns it

    # The old handle must not be able to delete the new owner's lock.
    assert await locks.release(stale) is False
    assert await locks.is_held("k") is True
    assert await locks.release(fresh) is True


async def test_renew_extends_only_for_the_owner(locks) -> None:
    handle = await locks.acquire("k")
    assert await locks.renew(handle) is True

    await locks.release(handle)
    assert await locks.renew(handle) is False  # expired/stolen -> cannot extend


async def test_lock_expires_so_a_dead_worker_never_deadlocks(redis) -> None:
    manager = LockManager(redis, ttl_seconds=1)
    handle = await manager.acquire("k", ttl_seconds=1)
    assert handle is not None

    # Simulate the holder being SIGKILLed: it never releases.
    await asyncio.sleep(1.2)
    assert await manager.is_held("k") is False
    assert await manager.acquire("k") is not None  # recovered automatically


# --- guard -------------------------------------------------------------------
async def test_guard_acquires_and_releases(locks) -> None:
    async with locks.guard("k"):
        assert await locks.is_held("k") is True
    assert await locks.is_held("k") is False


async def test_guard_raises_when_contended(locks) -> None:
    await locks.acquire("k")
    with pytest.raises(LockNotAcquiredError):
        async with locks.guard("k"):
            pass  # pragma: no cover


async def test_guard_releases_even_when_the_body_raises(locks) -> None:
    with pytest.raises(RuntimeError):
        async with locks.guard("k"):
            raise RuntimeError("boom")
    assert await locks.is_held("k") is False


async def test_guard_renews_the_lease_for_long_running_work(redis) -> None:
    """A job longer than the TTL must not lose its lock."""
    manager = LockManager(redis, ttl_seconds=1)  # renews every ~0.33s
    async with manager.guard("k"):
        await asyncio.sleep(1.5)  # longer than the TTL
        assert await manager.is_held("k") is True
    assert await manager.is_held("k") is False


async def test_two_concurrent_workers_only_one_enters(locks) -> None:
    entered: list[int] = []

    async def worker(n: int) -> None:
        try:
            async with locks.guard("sync:subscription:x"):
                entered.append(n)
                await asyncio.sleep(0.05)
        except LockNotAcquiredError:
            pass

    await asyncio.gather(*(worker(i) for i in range(5)))
    assert len(entered) == 1  # at-most-one active sync per subscription


# --- job store ---------------------------------------------------------------
def _job(**kw) -> Job:
    defaults = {"type": JobType.SYNC_SUBSCRIPTION, "payload": {"subscription_id": "s"}}
    defaults.update(kw)
    return Job(**defaults)


async def test_save_and_get_roundtrip(store) -> None:
    job = _job()
    await store.save(job)
    loaded = await store.get(job.id)
    assert loaded is not None and loaded.id == job.id and loaded.state is JobState.PENDING


async def test_get_unknown_job_is_none(store) -> None:
    assert await store.get(uuid.uuid4()) is None


async def test_list_is_newest_first_and_filterable(store) -> None:
    for i in range(3):
        j = _job()
        j.created_at = j.created_at.replace(microsecond=i * 1000)
        await store.save(j)

    succeeded = _job()
    succeeded.transition(JobState.QUEUED)
    succeeded.transition(JobState.RUNNING)
    succeeded.transition(JobState.SUCCEEDED)
    await store.save(succeeded)

    assert len(await store.list(limit=10)) == 4
    only_done = await store.list(states={JobState.SUCCEEDED}, limit=10)
    assert [j.id for j in only_done] == [succeeded.id]

    by_type = await store.list(types={JobType.CLEANUP}, limit=10)
    assert by_type == []


async def test_user_scoped_listing(store) -> None:
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    await store.save(_job(user_id=mine))
    await store.save(_job(user_id=theirs))
    assert len(await store.list(user_id=mine)) == 1


async def test_dead_letter_index(store) -> None:
    job = _job()
    job.transition(JobState.QUEUED)
    job.transition(JobState.RUNNING)
    job.transition(JobState.FAILED)
    job.transition(JobState.DEAD_LETTER)
    await store.save(job)

    dead = await store.list_dead_letter()
    assert [j.id for j in dead] == [job.id]

    # Re-queuing removes it from the poison queue.
    job.transition(JobState.QUEUED)
    await store.save(job)
    assert await store.list_dead_letter() == []


async def test_stuck_job_detection(store) -> None:
    from datetime import UTC, datetime, timedelta

    running = _job()
    running.transition(JobState.QUEUED)
    running.transition(JobState.RUNNING)
    running.started_at = datetime.now(UTC) - timedelta(hours=2)
    await store.save(running)

    fresh = _job()
    fresh.transition(JobState.QUEUED)
    fresh.transition(JobState.RUNNING)
    await store.save(fresh)

    stuck = await store.list_stuck(older_than_seconds=1800)
    assert [j.id for j in stuck] == [running.id]


async def test_count_by_state(store) -> None:
    await store.save(_job())
    done = _job()
    done.transition(JobState.QUEUED)
    done.transition(JobState.RUNNING)
    done.transition(JobState.SUCCEEDED)
    await store.save(done)

    counts = await store.count_by_state()
    assert counts["pending"] == 1 and counts["succeeded"] == 1
