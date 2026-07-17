"""Database test fixtures.

Tests run against an in-memory async SQLite database so the suite needs no live
Postgres (CI still has a real Postgres service for higher-fidelity checks). The
models are written to be dialect-portable (generic ``Uuid``, ``JSON`` with a
JSONB variant, native-enum-with-values) specifically so this works.

Foreign-key enforcement is enabled via a ``PRAGMA`` so cascade/constraint tests
behave like Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.domain.value_objects.enums import CalendarProvider, SportCategory, SubscriptionType
from app.persistence.models import (
    Base,
    Calendar,
    CalendarEvent,
    Competition,
    Fixture,
    GoogleAccount,
    Sport,
    Subscription,
    Team,
    User,
)


@pytest_asyncio.fixture
async def engine() -> AsyncGenerator[AsyncEngine]:
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,  # one shared in-memory connection
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng.sync_engine, "connect")
    def _enable_fk(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@dataclass
class Graph:
    """A minimal, valid object graph for tests to build on."""

    user: User
    account: GoogleAccount
    calendar: Calendar
    sport: Sport
    competition: Competition
    team: Team
    fixture: Fixture
    subscription: Subscription
    event: CalendarEvent


@pytest_asyncio.fixture
async def graph(db_session: AsyncSession) -> Graph:
    """Create and commit a complete, valid graph across all core tables."""
    user = User(email="fan@example.com", display_name="Fan")
    account = GoogleAccount(
        user=user,
        provider=CalendarProvider.GOOGLE,
        provider_subject="sub-123",
        email="fan@example.com",
    )
    calendar = Calendar(
        google_account=account,
        external_calendar_id="cal-1",
        summary="Sports",
        is_sync_target=True,
    )
    sport = Sport(
        key="football",
        name="Football",
        category=SportCategory.TEAM,
        provider_key="football-api",
    )
    competition = Competition(
        sport=sport,
        provider_competition_id="PL",
        name="Premier League",
    )
    team = Team(sport=sport, provider_team_id="ARS", name="Arsenal")
    competition.teams.append(team)
    fixture = Fixture(
        competition=competition,
        provider_fixture_id="f-1",
        identity_key="football:PL:ars-che:2026-08-01",
        content_hash="abc123",
        home_team=team,
        scheduled_start=datetime.now(UTC) + timedelta(days=7),
    )
    subscription = Subscription(
        user=user,
        target_calendar=calendar,
        sport=sport,
        scope_type=SubscriptionType.COMPETITION,
        competition=competition,
    )
    event = CalendarEvent(
        subscription=subscription,
        fixture=fixture,
        calendar=calendar,
        fixture_identity_key=fixture.identity_key,
    )

    db_session.add(user)
    await db_session.commit()
    return Graph(
        user=user,
        account=account,
        calendar=calendar,
        sport=sport,
        competition=competition,
        team=team,
        fixture=fixture,
        subscription=subscription,
        event=event,
    )


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()
