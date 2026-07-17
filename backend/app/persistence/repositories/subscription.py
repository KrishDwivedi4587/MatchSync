"""Subscription and CalendarEvent repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import or_, select

from app.domain.value_objects.enums import SubscriptionStatus
from app.persistence.models.calendar_event import CalendarEvent
from app.persistence.models.subscription import Subscription
from app.persistence.repositories.base import BaseRepository


class SubscriptionRepository(BaseRepository[Subscription]):
    model = Subscription

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[Subscription]:
        stmt = select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.deleted_at.is_(None),
        )
        return (await self.session.scalars(stmt)).all()

    async def list_due(self, now: datetime, *, limit: int = 500) -> Sequence[Subscription]:
        """Active subscriptions whose next sync is due — the scheduler scan.

        Data access only: computing/advancing ``next_sync_at`` is the sync
        service's job in a later stage.
        """
        stmt = (
            select(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.deleted_at.is_(None),
                or_(
                    Subscription.next_sync_at.is_(None),
                    Subscription.next_sync_at <= now,
                ),
            )
            .order_by(Subscription.next_sync_at.nulls_first())
            .limit(limit)
        )
        return (await self.session.scalars(stmt)).all()


class CalendarEventRepository(BaseRepository[CalendarEvent]):
    model = CalendarEvent

    async def get_by_subscription_and_fixture(
        self, subscription_id: uuid.UUID, fixture_id: uuid.UUID
    ) -> CalendarEvent | None:
        stmt = select(CalendarEvent).where(
            CalendarEvent.subscription_id == subscription_id,
            CalendarEvent.fixture_id == fixture_id,
        )
        return (await self.session.scalars(stmt)).first()

    async def list_for_subscription(self, subscription_id: uuid.UUID) -> Sequence[CalendarEvent]:
        stmt = select(CalendarEvent).where(
            CalendarEvent.subscription_id == subscription_id,
            CalendarEvent.deleted_at.is_(None),
        )
        return (await self.session.scalars(stmt)).all()
