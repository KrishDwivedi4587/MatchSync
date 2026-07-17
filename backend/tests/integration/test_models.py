"""Model-level tests: relationships, constraints, cascade, soft delete."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.value_objects.enums import SportCategory, SubscriptionType
from app.persistence.models import (
    Calendar,
    CalendarEvent,
    Competition,
    Fixture,
    GoogleAccount,
    OAuthToken,
    Sport,
    Subscription,
    Team,
    User,
)
from tests.integration.conftest import Graph


async def test_graph_relationships(graph: Graph, db_session: AsyncSession) -> None:
    """Every relationship in the core graph is navigable."""
    user = await db_session.get(User, graph.user.id)
    assert user is not None
    assert user.google_accounts[0].email == "fan@example.com"
    assert user.google_accounts[0].calendars[0].is_sync_target is True
    assert graph.competition.teams[0].name == "Arsenal"
    assert graph.fixture.home_team is not None
    assert graph.subscription.calendar_events[0].id == graph.event.id


async def test_unique_email(graph: Graph, db_session: AsyncSession) -> None:
    db_session.add(User(email="fan@example.com"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_fixture_identity_key_unique(graph: Graph, db_session: AsyncSession) -> None:
    dup = Fixture(
        competition_id=graph.competition.id,
        provider_fixture_id="f-2",
        identity_key=graph.fixture.identity_key,  # duplicate identity
        content_hash="zzz",
        scheduled_start=datetime.now(UTC) + timedelta(days=8),
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_calendar_event_dedup_constraint(graph: Graph, db_session: AsyncSession) -> None:
    """A second event for the same (subscription, fixture) is rejected."""
    dup = CalendarEvent(
        subscription_id=graph.subscription.id,
        fixture_id=graph.fixture.id,
        calendar_id=graph.calendar.id,
        fixture_identity_key=graph.fixture.identity_key,
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_subscription_scope_check_constraint(graph: Graph, db_session: AsyncSession) -> None:
    """scope_type=TEAM without a team_id violates the CHECK constraint."""
    bad = Subscription(
        user_id=graph.user.id,
        target_calendar_id=graph.calendar.id,
        sport_id=graph.sport.id,
        scope_type=SubscriptionType.TEAM,
        team_id=None,  # invalid for TEAM scope
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_cascade_delete_user(graph: Graph, db_session: AsyncSession) -> None:
    """Hard-deleting a user cascades to accounts, tokens, calendars, subs, events."""
    db_session.add(OAuthToken(google_account_id=graph.account.id, access_token_encrypted="x"))
    await db_session.commit()

    user = await db_session.get(User, graph.user.id)
    assert user is not None
    await db_session.delete(user)
    await db_session.commit()

    for model in (GoogleAccount, OAuthToken, Calendar, Subscription, CalendarEvent):
        count = await db_session.scalar(select(func.count()).select_from(model))
        assert count == 0, f"{model.__name__} rows should be cascade-deleted"


async def test_soft_delete_excluded_from_default_list(
    graph: Graph, db_session: AsyncSession
) -> None:
    from app.persistence.repositories import CompetitionRepository

    repo = CompetitionRepository(db_session)
    await repo.soft_delete(graph.competition)
    await db_session.commit()

    active = await repo.list_for_sport(graph.sport.id)
    assert graph.competition.id not in {c.id for c in active}
    # Still retrievable by PK (soft delete keeps the row).
    assert await repo.get(graph.competition.id) is not None


async def test_team_competition_association(db_session: AsyncSession) -> None:
    sport = Sport(
        key="basketball",
        name="Basketball",
        category=SportCategory.TEAM,
        provider_key="basketball-api",
    )
    comp = Competition(sport=sport, provider_competition_id="NBA", name="NBA")
    team = Team(sport=sport, provider_team_id="LAL", name="Lakers")
    comp.teams.append(team)
    db_session.add(sport)
    await db_session.commit()

    loaded = await db_session.get(Competition, comp.id)
    assert loaded is not None
    assert loaded.teams[0].name == "Lakers"
    assert team.competitions[0].name == "NBA"
