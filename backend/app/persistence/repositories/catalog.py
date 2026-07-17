"""Sport, Competition, and Team repositories (reference data)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, SQLColumnExpression, func, insert, select

from app.persistence.models.catalog import Competition, Sport, Team, team_competition
from app.persistence.repositories.base import BaseRepository


def _ilike(column: SQLColumnExpression[str], term: str) -> ColumnElement[bool]:
    """Portable case-insensitive contains (Postgres + SQLite)."""
    return func.lower(column).like(f"%{term.lower()}%")


class SportRepository(BaseRepository[Sport]):
    model = Sport

    async def get_by_key(self, key: str) -> Sport | None:
        return (await self.session.scalars(select(Sport).where(Sport.key == key))).first()

    async def list_active(self) -> Sequence[Sport]:
        stmt = select(Sport).where(Sport.is_active.is_(True)).order_by(Sport.display_order)
        return (await self.session.scalars(stmt)).all()


class CompetitionRepository(BaseRepository[Competition]):
    model = Competition

    async def get_by_provider_id(
        self, sport_id: uuid.UUID, provider_competition_id: str
    ) -> Competition | None:
        stmt = select(Competition).where(
            Competition.sport_id == sport_id,
            Competition.provider_competition_id == provider_competition_id,
        )
        return (await self.session.scalars(stmt)).first()

    async def list_for_sport(self, sport_id: uuid.UUID) -> Sequence[Competition]:
        stmt = select(Competition).where(
            Competition.sport_id == sport_id,
            Competition.deleted_at.is_(None),
        )
        return (await self.session.scalars(stmt)).all()

    async def search(self, term: str, *, limit: int = 20) -> Sequence[Competition]:
        stmt = (
            select(Competition)
            .where(_ilike(Competition.name, term), Competition.deleted_at.is_(None))
            .order_by(Competition.name)
            .limit(limit)
        )
        return (await self.session.scalars(stmt)).all()


class TeamRepository(BaseRepository[Team]):
    model = Team

    async def get_by_provider_id(self, sport_id: uuid.UUID, provider_team_id: str) -> Team | None:
        stmt = select(Team).where(
            Team.sport_id == sport_id,
            Team.provider_team_id == provider_team_id,
        )
        return (await self.session.scalars(stmt)).first()

    async def list_for_sport(self, sport_id: uuid.UUID) -> Sequence[Team]:
        stmt = select(Team).where(
            Team.sport_id == sport_id,
            Team.deleted_at.is_(None),
        )
        return (await self.session.scalars(stmt)).all()

    async def search(self, term: str, *, limit: int = 20) -> Sequence[Team]:
        stmt = (
            select(Team)
            .where(_ilike(Team.name, term), Team.deleted_at.is_(None))
            .order_by(Team.name)
            .limit(limit)
        )
        return (await self.session.scalars(stmt)).all()

    async def map_provider_ids(
        self, sport_id: uuid.UUID, provider_team_ids: list[str]
    ) -> dict[str, uuid.UUID]:
        """Bulk-resolve provider team ids -> internal UUIDs (one round-trip)."""
        if not provider_team_ids:
            return {}
        stmt = select(Team.provider_team_id, Team.id).where(
            Team.sport_id == sport_id,
            Team.provider_team_id.in_(provider_team_ids),
        )
        rows = await self.session.execute(stmt)
        # NOT dict(rows): SQLAlchemy's Result exposes .keys() (column names), so
        # dict() would take its mapping-constructor branch and crash with
        # "'ChunkedIteratorResult' object is not subscriptable". The explicit
        # comprehension iterates rows as (provider_id, team_id) pairs.
        return {provider_id: team_id for provider_id, team_id in rows}  # noqa: C416

    async def link_competition(self, team_id: uuid.UUID, competition_id: uuid.UUID) -> None:
        """Idempotently associate a team with a competition (m2m join table).

        Written as check-then-insert rather than a dialect-specific upsert so it
        works on both Postgres and the SQLite test database.
        """
        exists = await self.session.scalar(
            select(func.count())
            .select_from(team_competition)
            .where(
                team_competition.c.team_id == team_id,
                team_competition.c.competition_id == competition_id,
            )
        )
        if not exists:
            await self.session.execute(
                insert(team_competition).values(team_id=team_id, competition_id=competition_id)
            )
