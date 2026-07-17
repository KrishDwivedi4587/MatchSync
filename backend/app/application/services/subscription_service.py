"""Subscription management (Stage 10).

Owns the CRUD *lifecycle* of subscriptions — the central user-facing concept.
It does **not** synchronize: creating a subscription sets ``next_sync_at`` so the
existing scheduler (Stage 9) enqueues it, and "sync now" is the existing
``POST /jobs/sync`` endpoint. Pausing simply flips status; the Stage 8 engine and
Stage 9 scheduler already honour ``ACTIVE``/``PAUSED``.

The create request speaks the same identifiers the browse endpoints return
(sport key, provider external ids); this service resolves them to the internal
catalog UUIDs the ``subscriptions`` FKs require, using existing repositories.
Nothing here re-implements sync, calendar, or provider logic.
"""

from __future__ import annotations

import builtins
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.value_objects.enums import SubscriptionStatus, SubscriptionType
from app.exceptions.base import ConflictError, NotFoundError, ValidationAppError
from app.persistence.models.subscription import Subscription
from app.persistence.models.user import User
from app.persistence.repositories.application import ApplicationSubscriptionRepository
from app.persistence.repositories.catalog import (
    CompetitionRepository,
    SportRepository,
    TeamRepository,
)
from app.persistence.repositories.user import CalendarRepository

logger = get_logger(__name__)


class SubscriptionNotFoundError(NotFoundError):
    code = "subscription_not_found"
    message = "The subscription does not exist."


class DuplicateSubscriptionError(ConflictError):
    code = "subscription_exists"
    message = "You are already subscribed to this with that calendar."


class InvalidSubscriptionError(ValidationAppError):
    code = "subscription_invalid"


@dataclass(frozen=True)
class SubscriptionInput:
    calendar_id: uuid.UUID
    sport_key: str
    scope_type: SubscriptionType
    competition_external_id: str | None = None
    team_external_id: str | None = None
    sync_frequency_minutes: int = 360
    event_prefix: str | None = None


class SubscriptionService:
    def __init__(
        self,
        session: AsyncSession,
        subscriptions: ApplicationSubscriptionRepository,
        calendars: CalendarRepository,
        sports: SportRepository,
        competitions: CompetitionRepository,
        teams: TeamRepository,
    ) -> None:
        self._session = session
        self._subscriptions = subscriptions
        self._calendars = calendars
        self._sports = sports
        self._competitions = competitions
        self._teams = teams

    # --- reads ------------------------------------------------------------
    async def list(self, user: User) -> list[Subscription]:
        return list(await self._subscriptions.list_for_user_detailed(user.id))

    async def get(self, user: User, subscription_id: uuid.UUID) -> Subscription:
        subscription = await self._subscriptions.get_for_user_detailed(subscription_id, user.id)
        if subscription is None:
            raise SubscriptionNotFoundError()
        return subscription

    # --- create -----------------------------------------------------------
    async def create(self, user: User, data: SubscriptionInput) -> Subscription:
        resolved = await self._resolve(user, data)

        # Explicit duplicate check (the DB unique constraint does not fire on
        # NULL competition_id/team_id — see repository docstring).
        existing = await self._subscriptions.find_existing(
            user.id,
            resolved.calendar_id,
            data.scope_type,
            resolved.competition_id,
            resolved.team_id,
        )
        if existing is not None:
            raise DuplicateSubscriptionError()

        subscription = Subscription(
            user_id=user.id,
            target_calendar_id=resolved.calendar_id,
            sport_id=resolved.sport_id,
            scope_type=data.scope_type,
            competition_id=resolved.competition_id,
            team_id=resolved.team_id,
            status=SubscriptionStatus.ACTIVE,
            sync_frequency_minutes=data.sync_frequency_minutes,
            event_prefix=data.event_prefix,
            # Due immediately: the scheduler enqueues it on the next scan, and the
            # UI can also trigger an instant sync via POST /jobs/sync.
            next_sync_at=datetime.now(UTC),
        )
        try:
            await self._subscriptions.add(subscription)
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            # The frozen UNIQUE(user, calendar, scope, competition, team) fired.
            raise DuplicateSubscriptionError() from exc

        logger.info(
            "subscription.created",
            subscription_id=str(subscription.id),
            user_id=str(user.id),
            scope=data.scope_type.value,
        )
        return await self.get(user, subscription.id)

    # ``builtins.list``: the ``list`` method above shadows the builtin in
    # this class body's later def-signature annotations.
    async def bulk_create(
        self, user: User, items: builtins.list[SubscriptionInput]
    ) -> builtins.list[Subscription]:
        """Best-effort bulk subscribe. Duplicates are skipped, not fatal."""
        created: list[Subscription] = []
        for item in items:
            try:
                created.append(await self.create(user, item))
            except DuplicateSubscriptionError:
                continue
        return created

    # --- update / lifecycle ------------------------------------------------
    async def update(
        self,
        user: User,
        subscription_id: uuid.UUID,
        *,
        sync_frequency_minutes: int | None = None,
        event_prefix: str | None = None,
        clear_event_prefix: bool = False,
    ) -> Subscription:
        subscription = await self.get(user, subscription_id)
        if sync_frequency_minutes is not None:
            subscription.sync_frequency_minutes = sync_frequency_minutes
        if clear_event_prefix:
            subscription.event_prefix = None
        elif event_prefix is not None:
            subscription.event_prefix = event_prefix
        await self._session.commit()
        logger.info("subscription.updated", subscription_id=str(subscription_id))
        return await self.get(user, subscription_id)

    async def pause(self, user: User, subscription_id: uuid.UUID) -> Subscription:
        subscription = await self.get(user, subscription_id)
        subscription.status = SubscriptionStatus.PAUSED
        await self._session.commit()
        logger.info("subscription.paused", subscription_id=str(subscription_id))
        return await self.get(user, subscription_id)

    async def resume(self, user: User, subscription_id: uuid.UUID) -> Subscription:
        subscription = await self.get(user, subscription_id)
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.next_sync_at = datetime.now(UTC)  # catch up promptly
        await self._session.commit()
        logger.info("subscription.resumed", subscription_id=str(subscription_id))
        return await self.get(user, subscription_id)

    async def delete(self, user: User, subscription_id: uuid.UUID) -> None:
        """Soft delete. Its calendar events are removed by the next sync run:
        the engine already deletes events whose subscription no longer covers the
        fixture (Stage 8 prune)."""
        subscription = await self.get(user, subscription_id)
        await self._subscriptions.soft_delete(subscription)
        await self._session.commit()
        logger.info("subscription.deleted", subscription_id=str(subscription_id))

    async def bulk_delete(self, user: User, ids: builtins.list[uuid.UUID]) -> int:
        removed = 0
        for subscription_id in ids:
            try:
                await self.delete(user, subscription_id)
                removed += 1
            except SubscriptionNotFoundError:
                continue
        return removed

    # --- resolution -------------------------------------------------------
    @dataclass(frozen=True)
    class _Resolved:
        calendar_id: uuid.UUID
        sport_id: uuid.UUID
        competition_id: uuid.UUID | None
        team_id: uuid.UUID | None

    async def _resolve(self, user: User, data: SubscriptionInput) -> SubscriptionService._Resolved:
        # Calendar must belong to the user.
        owned = {c.id for c in await self._calendars.list_for_user(user.id)}
        if data.calendar_id not in owned:
            raise InvalidSubscriptionError("That calendar is not connected to your account.")

        sport = await self._sports.get_by_key(data.sport_key)
        if sport is None:
            raise InvalidSubscriptionError(f"Unknown sport '{data.sport_key}'. Refresh metadata.")

        competition_id: uuid.UUID | None = None
        team_id: uuid.UUID | None = None

        if data.scope_type is SubscriptionType.COMPETITION:
            if not data.competition_external_id:
                raise InvalidSubscriptionError("A competition is required for this scope.")
            competition = await self._competitions.get_by_provider_id(
                sport.id, data.competition_external_id
            )
            if competition is None:
                raise InvalidSubscriptionError("That competition is not in the catalog yet.")
            competition_id = competition.id
        elif data.scope_type is SubscriptionType.TEAM:
            if not data.team_external_id:
                raise InvalidSubscriptionError("A team is required for this scope.")
            team = await self._teams.get_by_provider_id(sport.id, data.team_external_id)
            if team is None:
                raise InvalidSubscriptionError("That team is not in the catalog yet.")
            team_id = team.id

        return self._Resolved(data.calendar_id, sport.id, competition_id, team_id)
