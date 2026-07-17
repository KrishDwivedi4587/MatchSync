"""Read queries for the application layer (Stage 10).

New *queries*, not new persistence logic: eager-loading variants so the
subscription-management UI never triggers N+1 lookups for sport/competition/team
names. Existing repositories are untouched.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.domain.value_objects.enums import SubscriptionType
from app.persistence.models.subscription import Subscription
from app.persistence.repositories.base import BaseRepository

_RELATIONS = (
    selectinload(Subscription.sport),
    selectinload(Subscription.competition),
    selectinload(Subscription.team),
    selectinload(Subscription.target_calendar),
)


class ApplicationSubscriptionRepository(BaseRepository[Subscription]):
    model = Subscription

    async def list_for_user_detailed(self, user_id: uuid.UUID) -> Sequence[Subscription]:
        stmt = (
            select(Subscription)
            .where(Subscription.user_id == user_id, Subscription.deleted_at.is_(None))
            .options(*_RELATIONS)
            .order_by(Subscription.created_at)
        )
        return (await self.session.scalars(stmt)).all()

    async def find_existing(
        self,
        user_id: uuid.UUID,
        calendar_id: uuid.UUID,
        scope_type: SubscriptionType,
        competition_id: uuid.UUID | None,
        team_id: uuid.UUID | None,
    ) -> Subscription | None:
        """Explicit duplicate check.

        The frozen UNIQUE(user, calendar, scope, competition, team) constraint
        does not fire when competition_id/team_id are NULL (SQL treats NULLs as
        distinct), so duplicate prevention for sport/competition/team scopes must
        be done here.
        """
        stmt = select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.target_calendar_id == calendar_id,
            Subscription.scope_type == scope_type,
            (
                Subscription.competition_id.is_(competition_id)
                if competition_id is None
                else Subscription.competition_id == competition_id
            ),
            (
                Subscription.team_id.is_(team_id)
                if team_id is None
                else Subscription.team_id == team_id
            ),
            Subscription.deleted_at.is_(None),
        )
        return (await self.session.scalars(stmt)).first()

    async def get_for_user_detailed(
        self, subscription_id: uuid.UUID, user_id: uuid.UUID
    ) -> Subscription | None:
        stmt = (
            select(Subscription)
            .where(
                Subscription.id == subscription_id,
                Subscription.user_id == user_id,
                Subscription.deleted_at.is_(None),
            )
            .options(*_RELATIONS)
        )
        return (await self.session.scalars(stmt)).first()
