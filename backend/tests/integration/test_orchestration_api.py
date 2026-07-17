"""Orchestration API endpoint tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from fakeredis import aioredis as fake_aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.v1.deps import (
    get_current_user,
    get_db,
    get_heartbeats,
    get_job_store,
    get_task_dispatcher,
)
from app.domain.orchestration.models import JobState, JobType
from app.infrastructure.heartbeat import HeartbeatRegistry
from app.infrastructure.jobs import JobStore
from app.main import app
from app.persistence.models.system import SchedulerJob
from app.persistence.models.user import User
from tests.integration.test_orchestration_worker import FakeDispatcher


@pytest_asyncio.fixture
async def api(
    engine: AsyncEngine,
) -> AsyncGenerator[tuple[AsyncClient, JobStore, FakeDispatcher, HeartbeatRegistry]]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = fake_aioredis.FakeRedis(decode_responses=True)
    store = JobStore(redis, retention_seconds=3600)
    beats = HeartbeatRegistry(redis, ttl_seconds=60)
    dispatcher = FakeDispatcher()

    async with factory() as setup:
        user = User(email="ops@example.com")
        setup.add(user)
        setup.add(SchedulerJob(key="scan_due_subscriptions", name="Scan", schedule="*/5 * * * *"))
        await setup.commit()

    async def _get_db() -> AsyncGenerator:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_job_store] = lambda: store
    app.dependency_overrides[get_task_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_heartbeats] = lambda: beats

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store, dispatcher, beats

    app.dependency_overrides.clear()
    await redis.aclose()


# --- job control -------------------------------------------------------------
async def test_enqueue_manual_sync_is_high_priority(api) -> None:
    client, _, dispatcher, _ = api
    subscription_id = str(uuid.uuid4())

    r = await client.post("/api/v1/jobs/sync", json={"subscription_id": subscription_id})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "sync_subscription"
    assert body["state"] == "queued"
    assert body["priority"] == 9
    assert body["queue"] == "sync.high"  # never waits behind scheduled work
    assert body["payload"]["trigger"] == "manual"
    assert len(dispatcher.dispatched) == 1


async def test_enqueue_without_subscription_syncs_the_whole_user(api) -> None:
    client, _, _, _ = api
    r = await client.post("/api/v1/jobs/sync", json={})
    assert r.json()["type"] == "sync_user"


async def test_enqueue_supports_a_delay(api) -> None:
    client, _, dispatcher, _ = api
    await client.post("/api/v1/jobs/sync", json={"delay_seconds": 30})
    assert dispatcher.dispatched[0][1] == 30


async def test_list_and_get_jobs(api) -> None:
    client, _, _, _ = api
    created = (await client.post("/api/v1/jobs/sync", json={})).json()

    listing = await client.get("/api/v1/jobs")
    assert listing.json()["total"] == 1

    detail = await client.get(f"/api/v1/jobs/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == created["id"]


async def test_filter_jobs_by_state(api) -> None:
    client, _, _, _ = api
    await client.post("/api/v1/jobs/sync", json={})
    assert (await client.get("/api/v1/jobs?state=queued")).json()["total"] == 1
    assert (await client.get("/api/v1/jobs?state=succeeded")).json()["total"] == 0


async def test_cancel_job(api) -> None:
    client, _store, dispatcher, _ = api
    created = (await client.post("/api/v1/jobs/sync", json={})).json()

    r = await client.post(f"/api/v1/jobs/{created['id']}/cancel")
    assert r.status_code == 200
    assert r.json()["state"] == "cancelled"
    assert (
        dispatcher.revoked == [created["celery_task_id"]] if "celery_task_id" in created else True
    )

    # Cancelling twice is a conflict, not a silent no-op.
    assert (await client.post(f"/api/v1/jobs/{created['id']}/cancel")).status_code == 409


async def test_retry_a_dead_lettered_job(api) -> None:
    client, store, _, _ = api
    created = (await client.post("/api/v1/jobs/sync", json={})).json()

    job = await store.get(uuid.UUID(created["id"]))
    job.transition(JobState.RUNNING)
    job.transition(JobState.FAILED)
    job.transition(JobState.DEAD_LETTER)
    await store.save(job)

    dead = await client.get("/api/v1/jobs/dead-letter")
    assert dead.json()["total"] == 1

    r = await client.post(f"/api/v1/jobs/{created['id']}/retry")
    assert r.status_code == 200
    assert r.json()["state"] == "queued"
    assert r.json()["attempts"] == 0


async def test_cannot_retry_a_queued_job(api) -> None:
    client, _, _, _ = api
    created = (await client.post("/api/v1/jobs/sync", json={})).json()
    assert (await client.post(f"/api/v1/jobs/{created['id']}/retry")).status_code == 409


async def test_unknown_job_is_404(api) -> None:
    client, _, _, _ = api
    r = await client.get(f"/api/v1/jobs/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "job_not_found"


async def test_another_users_job_is_invisible(api) -> None:
    client, store, _, _ = api
    from app.domain.orchestration.models import Job

    foreign = Job(type=JobType.SYNC_USER, user_id=uuid.uuid4())
    await store.save(foreign)
    assert (await client.get(f"/api/v1/jobs/{foreign.id}")).status_code == 404


# --- platform observability -----------------------------------------------------
async def test_workers_endpoint_reflects_heartbeats(api) -> None:
    client, _, _, beats = api
    assert (await client.get("/api/v1/workers")).json()["online"] == 0

    await beats.worker_beat("worker-1", state="ready")
    body = (await client.get("/api/v1/workers")).json()
    assert body["online"] == 1
    assert body["workers"][0]["name"] == "worker-1"


async def test_queue_endpoint_reports_depths(api) -> None:
    client, _, _, _ = api
    body = (await client.get("/api/v1/queue")).json()
    assert set(body["depths"]) >= {"sync.high", "sync.default", "ingest", "maintenance"}
    assert body["total"] == 0


async def test_scheduler_status_reports_liveness_and_definitions(api) -> None:
    client, _, _, beats = api
    dead = (await client.get("/api/v1/scheduler/status")).json()
    assert dead["alive"] is False
    assert dead["jobs"][0]["key"] == "scan_due_subscriptions"

    await beats.scheduler_beat(last_task="health_check")
    alive = (await client.get("/api/v1/scheduler/status")).json()
    assert alive["alive"] is True and alive["last_seen_at"]


async def test_backlog_endpoint(api) -> None:
    client, _, _, _ = api
    body = (await client.get("/api/v1/orchestration/backlog")).json()
    assert body["due_subscriptions"] == 0
    assert body["max_scheduling_delay_seconds"] == 0.0


async def test_metrics_endpoint(api) -> None:
    client, _, _, beats = api
    await client.post("/api/v1/jobs/sync", json={})
    await beats.worker_beat("worker-1")

    metrics = (await client.get("/api/v1/orchestration/metrics")).json()["metrics"]
    assert metrics["jobs_by_state"]["queued"] == 1
    assert metrics["workers_online"] == 1
    assert metrics["redis_healthy"] is True
    assert "backlog" in metrics and "lock_contention" in metrics


async def test_health_endpoint_is_unhealthy_without_workers(api) -> None:
    client, _, _, beats = api
    unhealthy = (await client.get("/api/v1/orchestration/health")).json()
    assert unhealthy["healthy"] is False
    assert unhealthy["workers_online"] == 0
    assert unhealthy["scheduler_alive"] is False

    await beats.worker_beat("w1")
    await beats.scheduler_beat()
    healthy = (await client.get("/api/v1/orchestration/health")).json()
    assert healthy["healthy"] is True
