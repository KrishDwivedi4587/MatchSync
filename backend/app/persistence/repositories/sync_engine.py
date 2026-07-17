"""Repositories for the synchronization engine.

A **new** module: no existing repository file is modified. Everything here either
extends ``BaseRepository`` or issues read queries the engine needs. All SQL for
the engine lives here, never in the services.

The queries implement incremental synchronization — the engine never scans the
whole fixtures table:

- ``list_scope_fixtures``  fixtures matching the subscription's scope + window,
                           optionally only those changed since a watermark.
- ``list_repair_fixture_ids`` mappings whose push never landed or whose hash
                           drifted; these must be revisited even if the fixture
                           itself did not change.
- ``list_prune_mappings``  mappings whose fixture is gone or left the window.
- ``version_snapshots``    Stage 7 fixture_versions snapshots, keyed by the hash
                           we last pushed, so the diff engine can name the fields
                           that changed without any new column.
- ``failure_counts``       retry budget derived from sync_operations history.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, func, insert, or_, select, update
from sqlalchemy.orm import selectinload

from app.domain.value_objects.enums import (
    CalendarEventState,
    FixtureStatus,
    OperationStatus,
    SubscriptionType,
)
from app.persistence.models.calendar_event import CalendarEvent
from app.persistence.models.catalog import Competition
from app.persistence.models.fixture import Fixture
from app.persistence.models.ingestion import FixtureVersion
from app.persistence.models.subscription import Subscription
from app.persistence.models.sync import SyncHistory, SyncOperation
from app.persistence.repositories.base import BaseRepository

_GONE_STATUSES = (FixtureStatus.DELETED,)


def _scope_filter(subscription: Subscription) -> Any:
    """Translate a subscription's polymorphic scope into a WHERE clause."""
    if subscription.scope_type is SubscriptionType.COMPETITION:
        return Fixture.competition_id == subscription.competition_id
    if subscription.scope_type is SubscriptionType.TEAM:
        return or_(
            Fixture.home_team_id == subscription.team_id,
            Fixture.away_team_id == subscription.team_id,
        )
    # SPORT: every competition of the sport.
    return Competition.sport_id == subscription.sport_id


class SyncFixtureRepository(BaseRepository[Fixture]):
    model = Fixture

    def _base(self, subscription: Subscription, start: datetime, end: datetime) -> Select:
        return (
            select(Fixture)
            .join(Competition, Fixture.competition_id == Competition.id)
            .where(
                _scope_filter(subscription),
                Fixture.scheduled_start >= start,
                Fixture.scheduled_start < end,
            )
        )

    async def list_scope_fixtures(
        self,
        subscription: Subscription,
        start: datetime,
        end: datetime,
        *,
        changed_since: datetime | None = None,
        include_ids: set[uuid.UUID] | None = None,
        limit: int | None = None,
    ) -> Sequence[Fixture]:
        """Fixtures in scope. Incremental when ``changed_since`` is supplied.

        ``include_ids`` force-includes fixtures needing repair even if they are
        older than the watermark.
        """
        stmt = self._base(subscription, start, end)

        if changed_since is not None:
            freshness = Fixture.updated_at >= changed_since
            if include_ids:
                stmt = stmt.where(or_(freshness, Fixture.id.in_(include_ids)))
            else:
                stmt = stmt.where(freshness)

        stmt = stmt.options(
            selectinload(Fixture.competition).selectinload(Competition.sport),
            selectinload(Fixture.home_team),
            selectinload(Fixture.away_team),
        ).order_by(Fixture.scheduled_start, Fixture.id)

        if limit:
            stmt = stmt.limit(limit)
        return (await self.session.scalars(stmt)).all()

    async def list_scope_fixture_ids(
        self, subscription: Subscription, start: datetime, end: datetime
    ) -> set[uuid.UUID]:
        """Ids of every fixture still in scope + window.

        Cheap (id-only, indexed) and **authoritative**: the planner uses it to
        decide membership. In incremental mode the loaded fixture list contains
        only *changed* fixtures, so it must never be used to infer membership —
        doing so would delete the event of every unchanged fixture.
        """
        stmt = (
            select(Fixture.id)
            .join(Competition, Fixture.competition_id == Competition.id)
            .where(
                _scope_filter(subscription),
                Fixture.scheduled_start >= start,
                Fixture.scheduled_start < end,
                Fixture.deleted_at.is_(None),
                Fixture.status.not_in(_GONE_STATUSES),
            )
        )
        return set((await self.session.scalars(stmt)).all())

    async def list_gone_fixtures(self, subscription_id: uuid.UUID) -> Sequence[Fixture]:
        """Fixtures this subscription maps but which are now deleted upstream.

        Soft-deleted fixtures fall out of every scope query, so they must be
        picked up through their mapping or their events would linger forever.
        """
        stmt = (
            select(Fixture)
            .join(CalendarEvent, CalendarEvent.fixture_id == Fixture.id)
            .where(
                CalendarEvent.subscription_id == subscription_id,
                CalendarEvent.deleted_at.is_(None),
                CalendarEvent.state == CalendarEventState.ACTIVE,
                or_(Fixture.deleted_at.is_not(None), Fixture.status.in_(_GONE_STATUSES)),
            )
            .options(
                selectinload(Fixture.competition).selectinload(Competition.sport),
                selectinload(Fixture.home_team),
                selectinload(Fixture.away_team),
            )
        )
        return (await self.session.scalars(stmt)).all()

    async def version_snapshots(
        self, wanted: list[tuple[uuid.UUID, str]]
    ) -> dict[uuid.UUID, dict[str, Any]]:
        """Look up the Stage 7 snapshot matching each fixture's last-pushed hash."""
        if not wanted:
            return {}
        clauses = [
            and_(FixtureVersion.fixture_id == fid, FixtureVersion.content_hash == h)
            for fid, h in wanted
        ]
        stmt = select(FixtureVersion).where(or_(*clauses))
        rows = (await self.session.scalars(stmt)).all()
        # Later versions win if a hash somehow repeats.
        result: dict[uuid.UUID, dict[str, Any]] = {}
        for row in sorted(rows, key=lambda r: r.version):
            if row.snapshot:
                result[row.fixture_id] = row.snapshot
        return result


class SyncMappingRepository(BaseRepository[CalendarEvent]):
    model = CalendarEvent

    async def list_for_subscription(
        self, subscription_id: uuid.UUID, *, include_deleted: bool = False
    ) -> Sequence[CalendarEvent]:
        stmt = select(CalendarEvent).where(CalendarEvent.subscription_id == subscription_id)
        if not include_deleted:
            stmt = stmt.where(CalendarEvent.deleted_at.is_(None))
        return (await self.session.scalars(stmt.order_by(CalendarEvent.id))).all()

    async def list_repair_fixture_ids(self, subscription_id: uuid.UUID) -> set[uuid.UUID]:
        """Mappings that must be revisited regardless of fixture freshness.

        Either the create never landed (no external id) or the pushed hash no
        longer matches the fixture — a previous run failed part-way.
        """
        stmt = (
            select(CalendarEvent.fixture_id)
            .join(Fixture, CalendarEvent.fixture_id == Fixture.id)
            .where(
                CalendarEvent.subscription_id == subscription_id,
                CalendarEvent.deleted_at.is_(None),
                CalendarEvent.state == CalendarEventState.ACTIVE,
                or_(
                    CalendarEvent.external_event_id.is_(None),
                    CalendarEvent.synced_content_hash.is_(None),
                    CalendarEvent.synced_content_hash != Fixture.content_hash,
                ),
            )
        )
        return set((await self.session.scalars(stmt)).all())

    async def bulk_insert(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            await self.session.execute(insert(CalendarEvent), rows)

    async def bulk_update(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            await self.session.execute(update(CalendarEvent), rows)


class SyncRunRepository(BaseRepository[SyncHistory]):
    model = SyncHistory

    async def add_operations(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            await self.session.execute(insert(SyncOperation), rows)

    async def failure_counts(
        self, subscription_id: uuid.UUID, *, since: datetime
    ) -> dict[uuid.UUID, int]:
        """Retry budget, derived from operation history — no new column needed."""
        stmt = (
            select(SyncOperation.fixture_id, func.count())
            .join(SyncHistory, SyncOperation.sync_history_id == SyncHistory.id)
            .where(
                SyncHistory.subscription_id == subscription_id,
                SyncOperation.status == OperationStatus.FAILED,
                SyncOperation.fixture_id.is_not(None),
                SyncOperation.created_at >= since,
            )
            .group_by(SyncOperation.fixture_id)
        )
        rows = await self.session.execute(stmt)
        return {fixture_id: count for fixture_id, count in rows if fixture_id}

    async def metrics(self, subscription_ids: list[uuid.UUID]) -> dict[str, Any]:
        """Aggregate run metrics for the caller's subscriptions."""
        if not subscription_ids:
            return {}
        stmt = select(
            func.count(SyncHistory.id),
            func.coalesce(func.sum(SyncHistory.created_count), 0),
            func.coalesce(func.sum(SyncHistory.updated_count), 0),
            func.coalesce(func.sum(SyncHistory.deleted_count), 0),
            func.coalesce(func.sum(SyncHistory.skipped_count), 0),
            func.coalesce(func.sum(SyncHistory.failed_count), 0),
        ).where(SyncHistory.subscription_id.in_(subscription_ids))
        runs, created, updated, deleted, skipped, failed = (await self.session.execute(stmt)).one()
        return {
            "runs": int(runs),
            "created": int(created),
            "updated": int(updated),
            "deleted": int(deleted),
            "skipped": int(skipped),
            "failed": int(failed),
        }

    async def status_counts(self, subscription_ids: list[uuid.UUID]) -> dict[str, int]:
        if not subscription_ids:
            return {}
        stmt = (
            select(SyncHistory.status, func.count())
            .where(SyncHistory.subscription_id.in_(subscription_ids))
            .group_by(SyncHistory.status)
        )
        return {status.value: int(count) for status, count in await self.session.execute(stmt)}


class SyncSubscriptionRepository(BaseRepository[Subscription]):
    model = Subscription

    async def get_for_user(
        self, subscription_id: uuid.UUID, user_id: uuid.UUID
    ) -> Subscription | None:
        stmt = select(Subscription).where(
            Subscription.id == subscription_id,
            Subscription.user_id == user_id,
            Subscription.deleted_at.is_(None),
        )
        return await self.session.scalar(stmt)

    async def list_active_for_user(self, user_id: uuid.UUID) -> Sequence[Subscription]:
        from app.domain.value_objects.enums import SubscriptionStatus

        stmt = (
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.deleted_at.is_(None),
            )
            .order_by(Subscription.created_at)
        )
        return (await self.session.scalars(stmt)).all()

    async def list_for_calendar(
        self, calendar_id: uuid.UUID, user_id: uuid.UUID
    ) -> Sequence[Subscription]:
        from app.domain.value_objects.enums import SubscriptionStatus

        stmt = select(Subscription).where(
            Subscription.target_calendar_id == calendar_id,
            Subscription.user_id == user_id,
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.deleted_at.is_(None),
        )
        return (await self.session.scalars(stmt)).all()
