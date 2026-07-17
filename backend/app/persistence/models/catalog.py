"""Sports catalog: Sport, Competition, Team, and the team<->competition link.

This is reference data populated from providers. It is deliberately generic so
adding a sport is a data operation, never a schema change (Stage 1, §7).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.value_objects.enums import CompetitionType, SportCategory
from app.persistence.models.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDMixin,
    enum_column,
)

if TYPE_CHECKING:
    from app.persistence.models.fixture import Fixture


# Many-to-many: a team competes in many competitions; a competition has many
# teams. A plain association table (no extra business columns needed yet).
team_competition = Table(
    "team_competition",
    Base.metadata,
    Column(
        "team_id",
        ForeignKey("teams.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "competition_id",
        ForeignKey("competitions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Sport(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "sports"

    # Natural, stable key used across the app and by the provider registry.
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[SportCategory] = mapped_column(enum_column(SportCategory, "sport_category"))
    # Which provider handles this sport (matches ProviderMetadata.key).
    provider_key: Mapped[str] = mapped_column(String(64))
    icon: Mapped[str | None] = mapped_column(String(255), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    competitions: Mapped[list[Competition]] = relationship(
        back_populates="sport",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    teams: Mapped[list[Team]] = relationship(
        back_populates="sport",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Competition(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "competitions"
    __table_args__ = (UniqueConstraint("sport_id", "provider_competition_id"),)

    sport_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sports.id", ondelete="CASCADE"), index=True
    )
    provider_competition_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[CompetitionType] = mapped_column(
        enum_column(CompetitionType, "competition_type"),
        default=CompetitionType.LEAGUE,
    )
    country: Mapped[str | None] = mapped_column(String(128), default=None)
    season: Mapped[str | None] = mapped_column(String(32), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    sport: Mapped[Sport] = relationship(back_populates="competitions")
    teams: Mapped[list[Team]] = relationship(
        secondary=team_competition, back_populates="competitions"
    )
    fixtures: Mapped[list[Fixture]] = relationship(
        back_populates="competition",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Team(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A club, national team, or esports team.

    Also models an individual competitor (a "team of one") so tennis/F1 reuse
    this table later without a new entity.
    """

    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("sport_id", "provider_team_id"),)

    sport_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sports.id", ondelete="CASCADE"), index=True
    )
    provider_team_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255))
    short_name: Mapped[str | None] = mapped_column(String(64), default=None)
    country: Mapped[str | None] = mapped_column(String(128), default=None)
    logo_url: Mapped[str | None] = mapped_column(Text, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    sport: Mapped[Sport] = relationship(back_populates="teams")
    competitions: Mapped[list[Competition]] = relationship(
        secondary=team_competition, back_populates="teams"
    )
