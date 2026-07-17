"""Calendar API endpoint tests.

Authentication is already covered in Stage 4, so ``get_current_user`` is
overridden here; the calendar provider is faked via the factory dependency.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.v1.deps import get_calendar_provider_factory, get_current_user, get_db
from app.domain.ports.calendar_provider import CalendarInfo
from app.domain.value_objects.enums import CalendarAccessRole, CalendarProvider
from app.main import app
from app.persistence.models.account import GoogleAccount
from app.persistence.models.calendar import Calendar
from app.persistence.models.user import User
from tests.integration.test_calendar_service import FakeCalendarProvider, FakeFactory

CAL_SCOPES = [
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


@pytest_asyncio.fixture
async def api(
    engine: AsyncEngine,
) -> AsyncGenerator[tuple[AsyncClient, FakeCalendarProvider, Calendar]]:
    factory_ = async_sessionmaker(engine, expire_on_commit=False)

    async with factory_() as setup:
        user = User(email="api@example.com")
        account = GoogleAccount(
            user=user,
            provider=CalendarProvider.GOOGLE,
            provider_subject="s1",
            email="api@example.com",
            is_primary=True,
            scopes=CAL_SCOPES,
        )
        setup.add(account)
        await setup.flush()
        calendar = Calendar(
            google_account_id=account.id,
            provider=CalendarProvider.GOOGLE,
            external_calendar_id="c1",
            summary="Primary",
            access_role="owner",
        )
        setup.add(calendar)
        await setup.commit()

    provider = FakeCalendarProvider(
        [CalendarInfo("c1", "Primary", CalendarAccessRole.OWNER, is_primary=True)]
    )

    async def _get_db() -> AsyncGenerator:
        async with factory_() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_calendar_provider_factory] = lambda: FakeFactory(provider)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, provider, calendar

    app.dependency_overrides.clear()


async def test_list_calendars(api) -> None:
    client, _, _calendar = api
    r = await client.get("/api/v1/calendars")
    assert r.status_code == 200
    body = r.json()
    assert [c["external_calendar_id"] for c in body["calendars"]] == ["c1"]
    assert body["default_calendar_id"] is None


async def test_refresh_discovers_calendars(api) -> None:
    client, provider, _ = api
    provider.calendars.append(CalendarInfo("c2", "Sports", CalendarAccessRole.WRITER))
    r = await client.post("/api/v1/calendars/refresh")
    assert r.status_code == 200
    assert {c["external_calendar_id"] for c in r.json()["calendars"]} == {"c1", "c2"}


async def test_default_calendar_lifecycle(api) -> None:
    client, _, calendar = api

    # No default selected yet.
    assert (await client.get("/api/v1/calendars/default")).status_code == 404

    r = await client.put("/api/v1/calendars/default", json={"calendar_id": str(calendar.id)})
    assert r.status_code == 200
    assert r.json()["is_sync_target"] is True

    r = await client.get("/api/v1/calendars/default")
    assert r.status_code == 200
    assert r.json()["id"] == str(calendar.id)


async def test_set_default_rejects_readonly(api) -> None:
    client, provider, calendar = api
    provider.calendars = [CalendarInfo("c1", "Primary", CalendarAccessRole.READER)]
    r = await client.put("/api/v1/calendars/default", json={"calendar_id": str(calendar.id)})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "calendar_permission_denied"


async def test_status_endpoint(api) -> None:
    client, _, _ = api
    r = await client.get("/api/v1/calendars/status")
    body = r.json()
    assert body["connected"] is True
    assert body["has_calendar_scope"] is True
    assert body["needs_reauth"] is False
    assert body["calendar_count"] == 1


async def test_validate_endpoint(api) -> None:
    client, _, calendar = api
    r = await client.post("/api/v1/calendars/validate", json={"calendar_id": str(calendar.id)})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True and body["writable"] is True
    assert body["access_role"] == "owner"


async def test_validate_reports_vanished_calendar(api) -> None:
    client, provider, calendar = api
    provider.calendars = []
    r = await client.post("/api/v1/calendars/validate", json={"calendar_id": str(calendar.id)})
    assert r.status_code == 200
    assert r.json() == {
        "valid": False,
        "writable": False,
        "access_role": None,
        "reason": "Calendar no longer exists.",
    }
