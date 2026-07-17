"""CalendarService tests: discovery, selection, validation, ownership, event CRUD.

Uses a FakeCalendarProvider — the service must work against any implementation
of the port, which is the whole point of the abstraction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.calendar_service import CalendarService
from app.application.services.calendar_validator import CalendarValidator
from app.domain.ports.calendar_provider import (
    BatchResult,
    CalendarEventInput,
    CalendarEventRecord,
    CalendarInfo,
    EventQuery,
    EventTime,
)
from app.domain.value_objects.enums import CalendarAccessRole, CalendarProvider
from app.exceptions.calendar import (
    CalendarNotFoundError,
    CalendarPermissionError,
    CalendarReauthRequiredError,
)
from app.persistence.models.account import GoogleAccount
from app.persistence.models.calendar import Calendar
from app.persistence.models.user import User
from app.persistence.repositories.user import CalendarRepository, GoogleAccountRepository

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


class FakeCalendarProvider:
    key = "fake"
    required_scopes = (
        "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    )

    def __init__(self, calendars: list[CalendarInfo] | None = None) -> None:
        self.calendars = calendars or []
        self.created: list[CalendarEventInput] = []
        self.deleted: list[str] = []

    async def list_calendars(self) -> list[CalendarInfo]:
        return self.calendars

    async def get_calendar(self, external_id: str) -> CalendarInfo:
        for c in self.calendars:
            if c.external_id == external_id:
                return c
        raise CalendarNotFoundError()

    async def create_event(self, calendar_id, event):
        self.created.append(event)
        return CalendarEventRecord(
            id="e1", calendar_id=calendar_id, title=event.title, when=event.when
        )

    async def update_event(self, calendar_id, event_id, event):
        return CalendarEventRecord(
            id=event_id, calendar_id=calendar_id, title=event.title, when=event.when
        )

    async def delete_event(self, calendar_id, event_id) -> None:
        self.deleted.append(event_id)

    async def get_event(self, calendar_id, event_id):
        return None

    async def list_events(self, calendar_id, query: EventQuery):
        return []

    async def search_events(self, calendar_id, query: EventQuery):
        return []

    async def batch_create(self, calendar_id, events):
        return [BatchResult(index=i, success=True) for i in range(len(events))]

    async def batch_update(self, calendar_id, items):
        return [BatchResult(index=i, success=True) for i in range(len(items))]

    async def batch_delete(self, calendar_id, event_ids):
        return [BatchResult(index=i, success=True) for i in range(len(event_ids))]


class FakeFactory:
    def __init__(self, provider: FakeCalendarProvider) -> None:
        self.provider = provider

    def for_account(self, provider_key, account_id):
        return self.provider


@pytest_asyncio.fixture
async def ctx(db_session: AsyncSession):
    user = User(email="cal@example.com")
    account = GoogleAccount(
        user=user,
        provider=CalendarProvider.GOOGLE,
        provider_subject="s1",
        email="cal@example.com",
        is_primary=True,
        scopes=[
            "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
            "https://www.googleapis.com/auth/calendar.events",
        ],
    )
    db_session.add(account)
    await db_session.commit()

    provider = FakeCalendarProvider()
    service = CalendarService(
        db_session,
        CalendarRepository(db_session),
        GoogleAccountRepository(db_session),
        FakeFactory(provider),
        CalendarValidator(),
    )
    return service, provider, user, account


async def _add_calendar(
    db: AsyncSession, account: GoogleAccount, external_id: str, role: str = "owner"
) -> Calendar:
    cal = Calendar(
        google_account_id=account.id,
        provider=CalendarProvider.GOOGLE,
        external_calendar_id=external_id,
        summary=external_id,
        access_role=role,
    )
    db.add(cal)
    await db.commit()
    return cal


# --- discovery -------------------------------------------------------------
async def test_discovery_creates_updates_and_soft_deletes(ctx, db_session) -> None:
    service, provider, user, account = ctx
    stale = await _add_calendar(db_session, account, "gone")

    provider.calendars = [
        CalendarInfo("c1", "Primary", CalendarAccessRole.OWNER, is_primary=True, time_zone="UTC"),
        CalendarInfo("c2", "Sports", CalendarAccessRole.WRITER),
    ]
    await service.discover_calendars(user)

    calendars = await service.list_calendars(user)
    by_ext = {c.external_calendar_id: c for c in calendars}
    assert set(by_ext) == {"c1", "c2"}  # "gone" was soft-deleted, so excluded
    assert by_ext["c1"].is_primary is True
    assert by_ext["c1"].access_role == "owner"

    await db_session.refresh(stale)
    assert stale.deleted_at is not None  # retained, not destroyed


async def test_discovery_is_idempotent(ctx, db_session) -> None:
    service, provider, user, _ = ctx
    provider.calendars = [CalendarInfo("c1", "Primary", CalendarAccessRole.OWNER)]
    await service.discover_calendars(user)
    await service.discover_calendars(user)
    assert len(await service.list_calendars(user)) == 1


async def test_discovery_without_account_requires_reauth(db_session) -> None:
    user = User(email="noaccount@example.com")
    db_session.add(user)
    await db_session.commit()
    service = CalendarService(
        db_session,
        CalendarRepository(db_session),
        GoogleAccountRepository(db_session),
        FakeFactory(FakeCalendarProvider()),
        CalendarValidator(),
    )
    with pytest.raises(CalendarReauthRequiredError):
        await service.discover_calendars(user)


# --- selection -------------------------------------------------------------
async def test_set_default_calendar_selects_exactly_one(ctx, db_session) -> None:
    service, provider, user, account = ctx
    c1 = await _add_calendar(db_session, account, "c1")
    c2 = await _add_calendar(db_session, account, "c2")
    provider.calendars = [
        CalendarInfo("c1", "One", CalendarAccessRole.OWNER),
        CalendarInfo("c2", "Two", CalendarAccessRole.WRITER),
    ]

    await service.set_default_calendar(user, c1.id)
    assert (await service.get_default_calendar(user)).id == c1.id

    # Switching moves the flag; never two targets.
    await service.set_default_calendar(user, c2.id)
    default = await service.get_default_calendar(user)
    assert default.id == c2.id
    targets = [c for c in await service.list_calendars(user) if c.is_sync_target]
    assert len(targets) == 1


async def test_cannot_select_readonly_calendar(ctx, db_session) -> None:
    service, provider, user, account = ctx
    cal = await _add_calendar(db_session, account, "ro", role="reader")
    provider.calendars = [CalendarInfo("ro", "Read only", CalendarAccessRole.READER)]

    with pytest.raises(CalendarPermissionError):
        await service.set_default_calendar(user, cal.id)
    assert await service.get_default_calendar(user) is None


async def test_cannot_select_inaccessible_calendar(ctx, db_session) -> None:
    service, provider, user, account = ctx
    cal = await _add_calendar(db_session, account, "vanished")
    provider.calendars = []  # remote no longer has it

    with pytest.raises(CalendarNotFoundError):
        await service.set_default_calendar(user, cal.id)


async def test_cannot_select_another_users_calendar(ctx, db_session) -> None:
    service, _provider, user, _ = ctx
    other_user = User(email="other@example.com")
    other_account = GoogleAccount(
        user=other_user,
        provider=CalendarProvider.GOOGLE,
        provider_subject="s2",
        email="other@example.com",
    )
    db_session.add(other_account)
    await db_session.commit()
    foreign = await _add_calendar(db_session, other_account, "theirs")

    # Ownership failure is reported as "not found" (no existence disclosure).
    with pytest.raises(CalendarNotFoundError):
        await service.set_default_calendar(user, foreign.id)


async def test_unknown_calendar_id_raises_not_found(ctx) -> None:
    service, _, user, _ = ctx
    with pytest.raises(CalendarNotFoundError):
        await service.get_calendar(user, uuid.uuid4())


# --- status / validation ---------------------------------------------------
async def test_status_reports_scope_and_default(ctx, db_session) -> None:
    service, provider, user, account = ctx
    cal = await _add_calendar(db_session, account, "c1")
    provider.calendars = [CalendarInfo("c1", "One", CalendarAccessRole.OWNER)]
    await service.set_default_calendar(user, cal.id)

    status = await service.get_status(user)
    assert status.connected is True
    assert status.has_calendar_scope is True
    assert status.needs_reauth is False
    assert status.default_calendar_id == cal.id


async def test_status_flags_missing_calendar_scope(ctx, db_session) -> None:
    service, _, user, account = ctx
    account.scopes = ["openid", "email"]  # pre-Stage-5 token
    await db_session.commit()

    status = await service.get_status(user)
    assert status.has_calendar_scope is False
    assert status.needs_reauth is True


async def test_validate_calendar_reports_readonly(ctx, db_session) -> None:
    service, provider, user, account = ctx
    cal = await _add_calendar(db_session, account, "ro", role="reader")
    provider.calendars = [CalendarInfo("ro", "RO", CalendarAccessRole.READER)]

    result = await service.validate_calendar(user, cal.id)
    assert result.valid is True
    assert result.writable is False
    assert result.access_role is CalendarAccessRole.READER


# --- event platform --------------------------------------------------------
async def test_event_crud_delegates_to_provider(ctx, db_session) -> None:
    service, provider, user, account = ctx
    cal = await _add_calendar(db_session, account, "c1", role="owner")
    event = CalendarEventInput(title="Match", when=EventTime(NOW, NOW + timedelta(hours=2)))

    record = await service.create_event(user, cal.id, event)
    assert record.id == "e1"
    assert provider.created[0].title == "Match"

    await service.update_event(user, cal.id, "e1", event)
    await service.delete_event(user, cal.id, "e1")
    assert provider.deleted == ["e1"]

    results = await service.batch_create_events(user, cal.id, [event, event])
    assert all(r.success for r in results) and len(results) == 2


async def test_event_write_rejected_on_readonly_calendar(ctx, db_session) -> None:
    service, _, user, account = ctx
    cal = await _add_calendar(db_session, account, "ro", role="reader")
    event = CalendarEventInput(title="X", when=EventTime(NOW, NOW))

    with pytest.raises(CalendarPermissionError):
        await service.create_event(user, cal.id, event)
