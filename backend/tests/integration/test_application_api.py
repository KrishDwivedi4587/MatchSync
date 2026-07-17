"""Application-layer tests: subscriptions, onboarding, preferences, dashboard.

The whole point of Stage 10 is composition, so these are driven through the API
with the auth and calendar-status seams faked (both covered by earlier stages).
"""

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
)
from app.application.services.calendar_service import CalendarStatus
from app.domain.value_objects.enums import (
    CalendarProvider,
    CompetitionType,
    SportCategory,
)
from app.infrastructure.heartbeat import HeartbeatRegistry
from app.infrastructure.jobs import JobStore
from app.main import app
from app.persistence.models.account import GoogleAccount
from app.persistence.models.calendar import Calendar
from app.persistence.models.catalog import Competition, Sport, Team
from app.persistence.models.user import User


class StubCalendarStatus:
    """Overrides CalendarService.get_status so onboarding/dashboard need no Google."""

    def __init__(self, *, selected: bool) -> None:
        self._selected = selected

    async def get_status(self, user) -> CalendarStatus:
        return CalendarStatus(
            connected=True,
            account_email="fan@example.com",
            has_calendar_scope=True,
            needs_reauth=False,
            calendar_count=1,
            default_calendar_id=uuid.uuid4() if self._selected else None,
            default_calendar_summary="Sports" if self._selected else None,
        )


@pytest_asyncio.fixture
async def api(engine: AsyncEngine) -> AsyncGenerator[tuple[AsyncClient, uuid.UUID, dict]]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids: dict[str, uuid.UUID] = {}

    async with factory() as setup:
        user = User(email="fan@example.com", display_name="Fan", timezone="UTC")
        account = GoogleAccount(
            user=user,
            provider=CalendarProvider.GOOGLE,
            provider_subject="s1",
            email="fan@example.com",
            is_primary=True,
        )
        calendar = Calendar(
            google_account=account,
            provider=CalendarProvider.GOOGLE,
            external_calendar_id="cal-1",
            summary="Sports",
            access_role="owner",
            is_sync_target=True,
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
        team = Team(sport=sport, provider_team_id="57", name="Arsenal")
        setup.add_all([user, account, calendar, sport, competition, team])
        await setup.commit()
        ids = {"user": user.id, "calendar": calendar.id}

    async def _get_db() -> AsyncGenerator:
        async with factory() as session:
            yield session

    redis = fake_aioredis.FakeRedis(decode_responses=True)

    # A detached user for the auth dependency (identity only; services reload it).
    async with factory() as s:
        current = await s.get(User, ids["user"])

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: current
    app.dependency_overrides[get_job_store] = lambda: JobStore(redis)
    app.dependency_overrides[get_heartbeats] = lambda: HeartbeatRegistry(redis)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, ids["calendar"], ids

    app.dependency_overrides.clear()
    await redis.aclose()


def _create_body(calendar_id: uuid.UUID, **over) -> dict:
    body = {
        "calendar_id": str(calendar_id),
        "sport": "football",
        "scope": "competition",
        "competition_id": "PL",
    }
    body.update(over)
    return body


# --- subscription CRUD -------------------------------------------------------
async def test_create_and_list_subscription(api) -> None:
    client, calendar_id, _ = api
    r = await client.post("/api/v1/subscriptions", json=_create_body(calendar_id))
    assert r.status_code == 201
    body = r.json()
    assert body["label"] == "Premier League"
    assert body["status"] == "active"
    assert body["next_sync_at"] is not None  # due immediately for the scheduler

    listing = await client.get("/api/v1/subscriptions")
    assert listing.json()["total"] == 1


async def test_duplicate_subscription_is_rejected(api) -> None:
    client, calendar_id, _ = api
    await client.post("/api/v1/subscriptions", json=_create_body(calendar_id))
    r = await client.post("/api/v1/subscriptions", json=_create_body(calendar_id))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "subscription_exists"


async def test_team_scope_requires_a_team(api) -> None:
    client, calendar_id, _ = api
    r = await client.post(
        "/api/v1/subscriptions",
        json={"calendar_id": str(calendar_id), "sport": "football", "scope": "team"},
    )
    assert r.status_code == 422 or r.json()["error"]["code"] == "subscription_invalid"


async def test_create_team_subscription(api) -> None:
    client, calendar_id, _ = api
    r = await client.post(
        "/api/v1/subscriptions",
        json={
            "calendar_id": str(calendar_id),
            "sport": "football",
            "scope": "team",
            "team_id": "57",
        },
    )
    assert r.status_code == 201
    assert r.json()["label"] == "Arsenal"


async def test_unknown_competition_is_rejected(api) -> None:
    client, calendar_id, _ = api
    r = await client.post(
        "/api/v1/subscriptions", json=_create_body(calendar_id, competition_id="XX")
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "subscription_invalid"


async def test_calendar_must_belong_to_user(api) -> None:
    client, _, _ = api
    r = await client.post("/api/v1/subscriptions", json=_create_body(uuid.uuid4()))
    assert r.status_code == 422


async def test_edit_pause_resume_delete_lifecycle(api) -> None:
    client, calendar_id, _ = api
    created = (await client.post("/api/v1/subscriptions", json=_create_body(calendar_id))).json()
    sid = created["id"]

    edited = await client.patch(
        f"/api/v1/subscriptions/{sid}", json={"sync_frequency_minutes": 720, "event_prefix": "[PL]"}
    )
    assert edited.json()["sync_frequency_minutes"] == 720
    assert edited.json()["event_prefix"] == "[PL]"

    paused = await client.post(f"/api/v1/subscriptions/{sid}/pause")
    assert paused.json()["status"] == "paused"

    resumed = await client.post(f"/api/v1/subscriptions/{sid}/resume")
    assert resumed.json()["status"] == "active"

    assert (await client.delete(f"/api/v1/subscriptions/{sid}")).status_code == 204
    assert (await client.get("/api/v1/subscriptions")).json()["total"] == 0


async def test_bulk_subscribe_skips_duplicates(api) -> None:
    client, calendar_id, _ = api
    body = {
        "items": [
            _create_body(calendar_id),
            _create_body(calendar_id),
            {
                "calendar_id": str(calendar_id),
                "sport": "football",
                "scope": "team",
                "team_id": "57",
            },
        ]
    }
    r = await client.post("/api/v1/subscriptions/bulk", json=body)
    # The two identical competition subs collapse to one; the team sub is distinct.
    assert r.json()["total"] == 2


async def test_bulk_delete(api) -> None:
    client, calendar_id, _ = api
    created = (await client.post("/api/v1/subscriptions", json=_create_body(calendar_id))).json()
    r = await client.post("/api/v1/subscriptions/bulk-delete", json={"ids": [created["id"]]})
    assert r.json()["deleted"] == 1


async def test_unknown_subscription_is_404(api) -> None:
    client, _, _ = api
    r = await client.get(f"/api/v1/subscriptions/{uuid.uuid4()}")
    assert r.status_code == 404


# --- onboarding --------------------------------------------------------------
async def test_onboarding_progresses_as_the_user_acts(api, engine) -> None:
    client, calendar_id, _ids = api
    from app.api.v1.deps import get_calendar_service

    app.dependency_overrides[get_calendar_service] = lambda: StubCalendarStatus(selected=True)

    before = (await client.get("/api/v1/onboarding/status")).json()
    assert before["complete"] is False  # no subscription yet
    assert before["current_step"] == "add_subscription"

    await client.post("/api/v1/subscriptions", json=_create_body(calendar_id))
    after = (await client.get("/api/v1/onboarding/status")).json()
    assert after["complete"] is True

    app.dependency_overrides.pop(get_calendar_service, None)


async def test_onboarding_first_step_when_calendar_not_selected(api) -> None:
    client, _, _ = api
    from app.api.v1.deps import get_calendar_service

    app.dependency_overrides[get_calendar_service] = lambda: StubCalendarStatus(selected=False)
    state = (await client.get("/api/v1/onboarding/status")).json()
    assert state["complete"] is False
    assert state["current_step"] == "select_calendar"
    app.dependency_overrides.pop(get_calendar_service, None)


# --- profile + preferences ---------------------------------------------------
async def test_update_profile(api) -> None:
    client, _, _ = api
    r = await client.patch(
        "/api/v1/me", json={"display_name": "Alice", "timezone": "Europe/London"}
    )
    assert r.status_code == 200
    assert r.json()["display_name"] == "Alice"
    assert r.json()["timezone"] == "Europe/London"


async def test_preferences_default_and_update(api) -> None:
    client, _, _ = api
    defaults = (await client.get("/api/v1/me/preferences")).json()["preferences"]
    assert defaults["notifications"]["email"]["enabled"] is False
    assert defaults["display"]["theme"] == "system"

    updated = await client.put(
        "/api/v1/me/preferences",
        json={
            "notifications": {
                "email": {"enabled": True, "target": "me@example.com"},
                "reminders_minutes": [60, 1440],
            },
            "display": {"theme": "dark"},
        },
    )
    prefs = updated.json()["preferences"]
    assert prefs["notifications"]["email"]["enabled"] is True
    assert prefs["notifications"]["reminders_minutes"] == [60, 1440]
    assert prefs["display"]["theme"] == "dark"

    # Persisted across requests.
    reread = (await client.get("/api/v1/me/preferences")).json()["preferences"]
    assert reread["display"]["theme"] == "dark"


# --- dashboard ---------------------------------------------------------------
async def test_dashboard_composes_the_home_screen(api) -> None:
    client, calendar_id, _ = api
    await client.post("/api/v1/subscriptions", json=_create_body(calendar_id))

    # get_status reads the DB (account scopes + calendars); no Google call needed.
    r = await client.get("/api/v1/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert body["subscriptions"]["total"] == 1
    assert body["subscriptions"]["items"][0]["label"] == "Premier League"
    assert "orchestration" in body and "providers" in body
    assert "sync" in body
