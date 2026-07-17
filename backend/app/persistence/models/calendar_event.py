"""CalendarEvent model — maps (subscription, fixture) -> a calendar event.

This is Stage 1's ``CalendarEventMapping`` and the linchpin of duplicate
prevention. The unique constraint on (subscription_id, fixture_id) is the
database-level guarantee that one fixture yields at most one event per
subscription, even under concurrent workers.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.value_objects.enums import CalendarEventState
from app.persistence.models.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDMixin,
    enum_column,
)

if TYPE_CHECKING:
    from app.persistence.models.calendar import Calendar
    from app.persistence.models.fixture import Fixture
    from app.persistence.models.subscription import Subscription


class CalendarEvent(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        # One event per fixture per subscription — the dedup guarantee.
        UniqueConstraint("subscription_id", "fixture_id"),
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    fixture_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fixtures.id", ondelete="CASCADE"), index=True
    )
    calendar_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calendars.id", ondelete="CASCADE"), index=True
    )
    # The provider's event id (nullable until the event is first created).
    external_event_id: Mapped[str | None] = mapped_column(String(255), default=None, index=True)
    # Denormalized identity key for deterministic provider event-id derivation
    # (extra defense against duplicates if a mapping row is ever lost).
    fixture_identity_key: Mapped[str] = mapped_column(String(255))
    # Last content hash pushed to the provider -> lets us skip no-op updates.
    synced_content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    state: Mapped[CalendarEventState] = mapped_column(
        enum_column(CalendarEventState, "calendar_event_state"),
        default=CalendarEventState.ACTIVE,
    )
    last_pushed_at: Mapped[datetime | None] = mapped_column(default=None)

    subscription: Mapped[Subscription] = relationship(back_populates="calendar_events")
    fixture: Mapped[Fixture] = relationship(back_populates="calendar_events")
    calendar: Mapped[Calendar] = relationship(back_populates="calendar_events")
