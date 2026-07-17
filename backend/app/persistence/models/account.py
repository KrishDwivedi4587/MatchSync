"""Linked identity account + its OAuth tokens.

The table is named ``google_accounts`` (Stage 1's entity name) but carries a
``provider`` discriminator so Apple/Microsoft identities reuse the same shape
later. Tokens are isolated in a separate table so encryption and access
auditing are contained and the token blob never rides along in account reads.
"""

from __future__ import annotations

import uuid
from datetime import datetime
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
    from app.persistence.models.calendar import Calendar
    from app.persistence.models.user import User


class GoogleAccount(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "google_accounts"
    __table_args__ = (
        # A provider's subject id is unique within that provider.
        # (Constraint name is derived from the naming convention in Base.metadata.)
        UniqueConstraint("provider", "provider_subject"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[CalendarProvider] = mapped_column(
        enum_column(CalendarProvider, "calendar_provider"),
        default=CalendarProvider.GOOGLE,
    )
    # The OAuth ``sub`` claim — stable per-provider account identifier.
    provider_subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(320), index=True)
    # Scopes actually granted (may differ from requested); JSON list.
    scopes: Mapped[list[str]] = mapped_column(default=list)
    # One account can be flagged the user's default connection.
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="google_accounts")
    token: Mapped[OAuthToken | None] = relationship(
        back_populates="google_account",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    calendars: Mapped[list[Calendar]] = relationship(
        back_populates="google_account",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class OAuthToken(UUIDMixin, TimestampMixin, Base):
    """Encrypted OAuth tokens (crown jewels).

    This stage defines the columns only — encryption/refresh logic belongs to
    the authentication stage. Token strings are stored already-encrypted; the
    column names make that contract explicit.
    """

    __tablename__ = "oauth_tokens"

    google_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("google_accounts.id", ondelete="CASCADE"), unique=True
    )
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, default=None)
    token_type: Mapped[str] = mapped_column(String(32), default="Bearer")
    expires_at: Mapped[datetime | None] = mapped_column(default=None)
    scopes: Mapped[list[str]] = mapped_column(default=list)
    # Supports key rotation: re-encrypt and bump the version.
    token_version: Mapped[int] = mapped_column(default=1)
    rotated_at: Mapped[datetime | None] = mapped_column(default=None)

    google_account: Mapped[GoogleAccount] = relationship(back_populates="token")
