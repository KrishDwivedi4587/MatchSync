"""User model — the MatchSync account (identity within our system)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.value_objects.enums import UserStatus
from app.persistence.models.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDMixin,
    enum_column,
)

if TYPE_CHECKING:
    from app.persistence.models.account import GoogleAccount
    from app.persistence.models.subscription import Subscription


class User(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"

    # Email is the human-facing identifier. Unique + indexed for login lookups.
    # 320 = max RFC 5321 email length.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), default=None)
    # Default timezone for rendering event times when a calendar has none.
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    locale: Mapped[str | None] = mapped_column(String(16), default=None)
    status: Mapped[UserStatus] = mapped_column(
        enum_column(UserStatus, "user_status"), default=UserStatus.ACTIVE
    )

    # One user -> many linked accounts (see design note: one-to-many is a
    # justified refinement of Stage 1's 1-1 to satisfy the explicit "multiple
    # Google accounts" future requirement without a later redesign).
    google_accounts: Mapped[list[GoogleAccount]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    subscriptions: Mapped[list[Subscription]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
