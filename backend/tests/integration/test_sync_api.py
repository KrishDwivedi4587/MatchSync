"""Synchronization API endpoint tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.v1.deps import get_current_user, get_db, get_sync_service
from app.application.services.sync_service import SyncService
from app.core.config import get_settings
from app.domain.value_objects.enums import (
    CalendarProvider,
    CompetitionType,
    SportCategory,
    SubscriptionStatus,
    SubscriptionType,
)
from app.main import app
from app.persistence.models.account import GoogleAccount
from app.persistence.models.calendar import Calendar
from app.persistence.models.catalog import Competition, Sport, Team
from app.persistence.models.subscription import Subscription
from app.persistence.models.user import User
from app.persistence.repositories.sync_engine import (
    SyncFixtureRepository,
    SyncMappingRepository,
    SyncRunRepository,
    SyncSubscriptionRepository,
)
from tests.integration.test_sync_engine import FakeCalendarService, add_fixture


@pytest_asyncio.fixture
async def api(
    engine: AsyncEngine,
) -> AsyncGenerator[tuple[AsyncClient, FakeCalendarService, uuid.UUID]]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    calendar_service = FakeCalendarService()

    async with factory() as setup:
        user = User(email="api-sync@example.com")
        account = GoogleAccount(
            user=user,
            provider=CalendarProvider.GOOGLE,
            provider_subject="s1",
            email="api-sync@example.com",
            is_primary=True,
        )
        calendar = Calendar(
            google_account=account,
            provider=CalendarProvider.GOOGLE,
            external_calendar_id="cal-1",
            summary="Sports",
            access_role="owner",
        )
        sport = Sport(
            key="football", name="Football", category=SportCategory.TEAM, provider_key="f"
        )
        competition = Competition(
            sport=sport,
            provider_competition_id="PL",
            name="Premier League",
            type=CompetitionType.LEAGUE,
        )
        home = Team(sport=sport, provider_team_id="57", name="Arsenal")
        away = Team(sport=sport, provider_team_id="61", name="Chelsea")
        subscription = Subscription(
            user=user,
            target_calendar=calendar,
            sport=sport,
            scope_type=SubscriptionType.COMPETITION,
            competition=competition,
            status=SubscriptionStatus.ACTIVE,
        )
        setup.add_all([user, account, calendar, sport, competition, home, away, subscription])
        await setup.commit()
        await add_fixture(setup, competition, home, away)
        subscription_id = subscription.id
        calendar_id = calendar.id

    async def _get_db() -> AsyncGenerator:
        async with factory() as session:
            yield session

    async def _get_engine() -> AsyncGenerator:
        async with factory() as session:
            yield SyncService(
                session,
                calendar_service,
                SyncSubscriptionRepository(session),
                SyncFixtureRepository(session),
                SyncMappingRepository(session),
                SyncRunRepository(session),
                get_settings(),
            )

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_sync_service] = _get_engine

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, calendar_service, subscription_id, calendar_id

    app.dependency_overrides.clear()


async def test_plan_endpoint_previews_without_writing(api) -> None:
    client, cal, subscription_id, _ = api
    r = await client.get(f"/api/v1/sync/plan?subscription_id={subscription_id}&mode=full")
    assert r.status_code == 200
    body = r.json()
    assert body["stats"]["create"] == 1
    assert body["is_empty"] is False
    assert body["actions"][0]["type"] == "create"
    assert cal.calls == []  # preview performs no calendar calls


async def test_sync_endpoint_runs_and_reports(api) -> None:
    client, _cal, subscription_id, _ = api
    r = await client.post("/api/v1/sync", json={"subscription_id": str(subscription_id)})
    assert r.status_code == 200
    report = r.json()["reports"][0]
    assert report["status"] == "success"
    assert report["created"] == 1
    assert report["api_calls"] == 1
    assert report["run_id"]


async def test_sync_is_idempotent_through_the_api(api) -> None:
    client, cal, subscription_id, _ = api
    await client.post("/api/v1/sync", json={"subscription_id": str(subscription_id)})
    cal.calls.clear()

    r = await client.post("/api/v1/sync", json={"subscription_id": str(subscription_id)})
    report = r.json()["reports"][0]
    assert report["api_calls"] == 0
    assert report["plan"]["mutations"] == 0
    assert cal.calls == []


async def test_sync_user_endpoint(api) -> None:
    client, _, _, _ = api
    r = await client.post("/api/v1/sync/user")
    assert r.status_code == 200
    assert r.json()["reports"][0]["created"] == 1


async def test_sync_calendar_endpoint(api) -> None:
    client, _, _, calendar_id = api
    r = await client.post("/api/v1/sync/calendar", json={"calendar_id": str(calendar_id)})
    assert r.status_code == 200
    assert r.json()["reports"][0]["created"] == 1


async def test_status_history_and_report_endpoints(api) -> None:
    client, _, subscription_id, _ = api
    report = (
        await client.post("/api/v1/sync", json={"subscription_id": str(subscription_id)})
    ).json()
    run_id = report["reports"][0]["run_id"]

    status = await client.get("/api/v1/sync/status")
    assert status.status_code == 200
    entry = status.json()["subscriptions"][0]
    assert entry["last_run"]["created_count"] == 1
    assert entry["next_sync_at"] is not None

    history = await client.get("/api/v1/sync/history")
    assert history.json()["runs"][0]["id"] == run_id

    detail = await client.get(f"/api/v1/sync/report/{run_id}")
    assert detail.status_code == 200
    # Every calendar mutation is traceable through sync history (invariant I7).
    assert len(detail.json()["operations"]) == 1
    assert detail.json()["operations"][0]["operation_type"] == "create"


async def test_report_of_unknown_run_is_404(api) -> None:
    client, _, _, _ = api
    r = await client.get(f"/api/v1/sync/report/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_metrics_endpoint(api) -> None:
    client, _, subscription_id, _ = api
    await client.post("/api/v1/sync", json={"subscription_id": str(subscription_id)})
    r = await client.get("/api/v1/sync/metrics")
    assert r.status_code == 200
    metrics = r.json()["metrics"]
    assert metrics["runs"] == 1
    assert metrics["calendar_writes"] == 1


async def test_unknown_subscription_is_404(api) -> None:
    client, _, _, _ = api
    r = await client.post("/api/v1/sync", json={"subscription_id": str(uuid.uuid4())})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "subscription_not_found"
