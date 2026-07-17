"""Calendar model — an external calendar belonging to a linked account."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.value_objects.enums import CalendarProvider
from app.persistence.models.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDMixin,
    enum_column,
)

if TYPE_CHECKING:
    from app.persistence.models.account import GoogleAccount
    from app.persistence.models.calendar_event import CalendarEvent
    from app.persistence.models.subscription import Subscription


class Calendar(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "calendars"
    __table_args__ = (
        # A provider's calendar id is unique within one account.
        UniqueConstraint("google_account_id", "external_calendar_id"),
    )

    google_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("google_accounts.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[CalendarProvider] = mapped_column(
        enum_column(CalendarProvider, "calendar_provider"),
        default=CalendarProvider.GOOGLE,
    )
    # The provider's calendar identifier (not its name — names change).
    external_calendar_id: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    time_zone: Mapped[str | None] = mapped_column(String(64), default=None)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    # Whether MatchSync writes fixtures into this calendar.
    is_sync_target: Mapped[bool] = mapped_column(Boolean, default=False)
    access_role: Mapped[str | None] = mapped_column(String(32), default=None)

    google_account: Mapped[GoogleAccount] = relationship(back_populates="calendars")
    subscriptions: Mapped[list[Subscription]] = relationship(
        back_populates="target_calendar",
        passive_deletes=True,
    )
    calendar_events: Mapped[list[CalendarEvent]] = relationship(
        back_populates="calendar",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
