"""Subscription model — the user's intent to sync a scope into a calendar.

The unit the sync engine operates on. Uses Stage 1's polymorphic scope: a
``scope_type`` plus an optional competition/team reference. A CHECK constraint
enforces that the reference matches the scope type.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.value_objects.enums import SubscriptionStatus, SubscriptionType
from app.persistence.models.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDMixin,
    enum_column,
)

if TYPE_CHECKING:
    from app.persistence.models.calendar import Calendar
    from app.persistence.models.calendar_event import CalendarEvent
    from app.persistence.models.catalog import Competition, Sport, Team
    from app.persistence.models.sync import SyncHistory
    from app.persistence.models.user import User


class Subscription(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        # Scope/reference consistency: the right ref must be set for each scope.
        CheckConstraint(
            "(scope_type = 'sport' AND competition_id IS NULL AND team_id IS NULL) "
            "OR (scope_type = 'competition' AND competition_id IS NOT NULL AND team_id IS NULL) "
            "OR (scope_type = 'team' AND team_id IS NOT NULL AND competition_id IS NULL)",
            name="scope_reference_consistency",
        ),
        # Best-effort dedup of identical subscriptions per user+calendar. NULLs
        # in competition_id/team_id are treated as distinct by Postgres; a
        # future partial/expression index can tighten this if needed.
        UniqueConstraint(
            "user_id",
            "target_calendar_id",
            "scope_type",
            "competition_id",
            "team_id",
            # Explicit short name: the convention-generated name would exceed
            # Postgres's 63-char identifier limit and get truncated.
            name="uq_subscriptions_user_scope",
        ),
        # Scheduler scan: "find subscriptions due for sync".
        Index("ix_subscriptions_next_sync", "status", "next_sync_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # If the target calendar is removed, the subscription goes with it.
    target_calendar_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calendars.id", ondelete="CASCADE"), index=True
    )
    # RESTRICT: cannot delete a sport that still has subscriptions.
    sport_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sports.id", ondelete="RESTRICT"), index=True
    )
    scope_type: Mapped[SubscriptionType] = mapped_column(
        enum_column(SubscriptionType, "subscription_type")
    )
    competition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("competitions.id", ondelete="CASCADE"), default=None, index=True
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), default=None, index=True
    )

    status: Mapped[SubscriptionStatus] = mapped_column(
        enum_column(SubscriptionStatus, "subscription_status"),
        default=SubscriptionStatus.ACTIVE,
    )
    # Per-subscription cadence enables tiered plans later.
    sync_frequency_minutes: Mapped[int] = mapped_column(Integer, default=360)
    last_synced_at: Mapped[datetime | None] = mapped_column(default=None)
    next_sync_at: Mapped[datetime | None] = mapped_column(default=None)
    # Optional per-subscription event title prefix.
    event_prefix: Mapped[str | None] = mapped_column(String(64), default=None)

    user: Mapped[User] = relationship(back_populates="subscriptions")
    target_calendar: Mapped[Calendar] = relationship(back_populates="subscriptions")
    sport: Mapped[Sport] = relationship()
    competition: Mapped[Competition | None] = relationship()
    team: Mapped[Team | None] = relationship()
    calendar_events: Mapped[list[CalendarEvent]] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    sync_history: Mapped[list[SyncHistory]] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
