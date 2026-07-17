"""Fixture model — a normalized match/event cached from a provider.

This is the canonical internal representation the sync engine reconciles
against. ``identity_key`` is the stable, engine-derived identity (globally
unique -> one row per real match); ``content_hash`` covers only mutable fields
so change detection is O(1).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.value_objects.enums import FixtureStatus
from app.persistence.models.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDMixin,
    enum_column,
)

if TYPE_CHECKING:
    from app.persistence.models.calendar_event import CalendarEvent
    from app.persistence.models.catalog import Competition, Team


class Fixture(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "fixtures"
    __table_args__ = (
        # A provider's fixture id is unique within a competition.
        UniqueConstraint("competition_id", "provider_fixture_id"),
        # Hot paths: list a competition's fixtures in a time window, and scan by
        # status + time for the sync/scheduler passes.
        Index("ix_fixtures_competition_start", "competition_id", "scheduled_start"),
        Index("ix_fixtures_status_start", "status", "scheduled_start"),
    )

    competition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("competitions.id", ondelete="CASCADE"), index=True
    )
    provider_fixture_id: Mapped[str] = mapped_column(String(128))
    # Stable identity for dedup across reschedules (unique -> one fixture per match).
    identity_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # Hash over mutable fields (start/venue/status) — drives UPDATE detection.
    content_hash: Mapped[str] = mapped_column(String(64))

    # Two-sided fixtures (football/basketball/valorant). SET NULL so removing a
    # team never deletes historical fixtures. Individual-sport participants get
    # a join table in a future stage.
    home_team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), default=None, index=True
    )
    away_team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), default=None, index=True
    )

    scheduled_start: Mapped[datetime] = mapped_column(index=True)
    scheduled_end: Mapped[datetime | None] = mapped_column(default=None)
    status: Mapped[FixtureStatus] = mapped_column(
        enum_column(FixtureStatus, "fixture_status"), default=FixtureStatus.SCHEDULED
    )
    round: Mapped[str | None] = mapped_column(String(64), default=None)
    stage: Mapped[str | None] = mapped_column(String(64), default=None)
    venue: Mapped[str | None] = mapped_column(String(255), default=None)
    provider_updated_at: Mapped[datetime | None] = mapped_column(default=None)

    # --- ingestion state (Stage 7, additive) -------------------------------
    # Monotonic counter; every persisted change writes a `fixture_versions` row.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    # When the provider first stopped returning this fixture. Deleting on a
    # single flaky read is forbidden (Stage 1), so we require a second absence.
    missing_since: Mapped[datetime | None] = mapped_column(default=None)

    competition: Mapped[Competition] = relationship(back_populates="fixtures")
    home_team: Mapped[Team | None] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team | None] = relationship(foreign_keys=[away_team_id])
    calendar_events: Mapped[list[CalendarEvent]] = relationship(
        back_populates="fixture",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
