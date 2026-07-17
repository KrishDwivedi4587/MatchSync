"""Repository-level tests: CRUD, lookups, and query helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.value_objects.enums import SportCategory, SubscriptionStatus
from app.persistence.models import Sport
from app.persistence.repositories import (
    CalendarEventRepository,
    FixtureRepository,
    SportRepository,
    SubscriptionRepository,
    UserRepository,
)
from tests.integration.conftest import Graph


async def test_base_repository_crud(db_session: AsyncSession) -> None:
    repo = SportRepository(db_session)
    sport = await repo.create(
        key="tennis",
        name="Tennis",
        category=SportCategory.INDIVIDUAL,
        provider_key="tennis-api",
    )
    await db_session.commit()

    assert await repo.get(sport.id) is not None
    assert await repo.exists(sport.id) is True
    assert await repo.count() == 1

    await repo.update(sport, name="Lawn Tennis")
    await db_session.commit()
    assert (await repo.get(sport.id)).name == "Lawn Tennis"  # type: ignore[union-attr]


async def test_sport_repo_lookups(db_session: AsyncSession) -> None:
    repo = SportRepository(db_session)
    db_session.add_all(
        [
            Sport(
                key="football",
                name="Football",
                category=SportCategory.TEAM,
                provider_key="football-api",
                display_order=1,
                is_active=True,
            ),
            Sport(
                key="cricket",
                name="Cricket",
                category=SportCategory.TEAM,
                provider_key="cricket-api",
                display_order=2,
                is_active=False,
            ),
        ]
    )
    await db_session.commit()

    assert (await repo.get_by_key("football")) is not None
    active = await repo.list_active()
    assert [s.key for s in active] == ["football"]  # inactive excluded, ordered


async def test_user_repo_get_by_email(graph: Graph, db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    found = await repo.get_by_email("fan@example.com")
    assert found is not None
    assert found.id == graph.user.id


async def test_fixture_repo_by_identity_and_window(graph: Graph, db_session: AsyncSession) -> None:
    repo = FixtureRepository(db_session)
    assert await repo.get_by_identity_key(graph.fixture.identity_key) is not None

    window = await repo.list_for_competition_in_window(
        graph.competition.id,
        datetime.now(UTC),
        datetime.now(UTC) + timedelta(days=30),
    )
    assert graph.fixture.id in {f.id for f in window}


async def test_subscription_list_due(graph: Graph, db_session: AsyncSession) -> None:
    repo = SubscriptionRepository(db_session)
    # next_sync_at is NULL -> due immediately.
    due = await repo.list_due(datetime.now(UTC))
    assert graph.subscription.id in {s.id for s in due}

    # Pausing removes it from the due set.
    graph.subscription.status = SubscriptionStatus.PAUSED
    await db_session.commit()
    due_after = await repo.list_due(datetime.now(UTC))
    assert graph.subscription.id not in {s.id for s in due_after}


async def test_calendar_event_lookup(graph: Graph, db_session: AsyncSession) -> None:
    repo = CalendarEventRepository(db_session)
    found = await repo.get_by_subscription_and_fixture(graph.subscription.id, graph.fixture.id)
    assert found is not None
    assert found.id == graph.event.id
