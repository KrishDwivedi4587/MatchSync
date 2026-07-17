"""Fixture repository."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, insert, or_, select, update
from sqlalchemy.orm import selectinload

from app.domain.value_objects.enums import FixtureStatus
from app.persistence.models.catalog import Competition, Sport, Team
from app.persistence.models.fixture import Fixture
from app.persistence.repositories.base import BaseRepository


class FixtureRepository(BaseRepository[Fixture]):
    model = Fixture

    async def get_by_identity_key(self, identity_key: str) -> Fixture | None:
        stmt = select(Fixture).where(Fixture.identity_key == identity_key)
        return await self.session.scalar(stmt)

    async def get_by_provider_id(
        self, competition_id: uuid.UUID, provider_fixture_id: str
    ) -> Fixture | None:
        stmt = select(Fixture).where(
            Fixture.competition_id == competition_id,
            Fixture.provider_fixture_id == provider_fixture_id,
        )
        return await self.session.scalar(stmt)

    async def list_for_competition_in_window(
        self,
        competition_id: uuid.UUID,
        start: datetime,
        end: datetime,
    ) -> Sequence[Fixture]:
        """Fixtures for a competition within [start, end) — the sync fetch window."""
        stmt = (
            select(Fixture)
            .where(
                Fixture.competition_id == competition_id,
                Fixture.scheduled_start >= start,
                Fixture.scheduled_start < end,
                Fixture.deleted_at.is_(None),
            )
            .order_by(Fixture.scheduled_start)
        )
        return (await self.session.scalars(stmt)).all()

    # --- ingestion support (Stage 7, additive) -----------------------------
    async def list_for_matching(
        self, competition_id: uuid.UUID, start: datetime, end: datetime
    ) -> Sequence[Fixture]:
        """Fixtures in scope for dedup matching, **including soft-deleted ones**.

        Soft-deleted rows must be visible so a fixture that reappears upstream is
        RESTORED rather than colliding with the unique ``identity_key``.
        """
        stmt = select(Fixture).where(
            Fixture.competition_id == competition_id,
            Fixture.scheduled_start >= start,
            Fixture.scheduled_start < end,
        )
        return (await self.session.scalars(stmt)).all()

    async def bulk_insert(self, rows: list[dict[str, Any]]) -> None:
        """Insert many fixtures in one round-trip. Rows must carry ``id``."""
        if rows:
            await self.session.execute(insert(Fixture), rows)

    async def bulk_update(self, rows: list[dict[str, Any]]) -> None:
        """Update many fixtures by primary key in one round-trip."""
        if rows:
            await self.session.execute(update(Fixture), rows)

    # --- read API (Stage 7 query endpoints) --------------------------------
    def _filtered(
        self,
        *,
        sport_key: str | None,
        competition_id: uuid.UUID | None,
        status: FixtureStatus | None,
        start_from: datetime | None,
        start_to: datetime | None,
        query: str | None,
        include_deleted: bool,
    ) -> Select:
        stmt = select(Fixture).join(Competition, Fixture.competition_id == Competition.id)

        if not include_deleted:
            stmt = stmt.where(Fixture.deleted_at.is_(None))
        if sport_key:
            stmt = stmt.join(Sport, Competition.sport_id == Sport.id).where(Sport.key == sport_key)
        if competition_id:
            stmt = stmt.where(Fixture.competition_id == competition_id)
        if status:
            stmt = stmt.where(Fixture.status == status)
        if start_from:
            stmt = stmt.where(Fixture.scheduled_start >= start_from)
        if start_to:
            stmt = stmt.where(Fixture.scheduled_start < start_to)
        if query:
            term = f"%{query.lower()}%"
            home = select(Team.id).where(func.lower(Team.name).like(term)).scalar_subquery()
            away = select(Team.id).where(func.lower(Team.name).like(term)).scalar_subquery()
            stmt = stmt.where(
                or_(
                    func.lower(Competition.name).like(term),
                    Fixture.home_team_id.in_(home),
                    Fixture.away_team_id.in_(away),
                )
            )
        return stmt

    async def search(
        self,
        *,
        sport_key: str | None = None,
        competition_id: uuid.UUID | None = None,
        status: FixtureStatus | None = None,
        start_from: datetime | None = None,
        start_to: datetime | None = None,
        query: str | None = None,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[Fixture], int]:
        """Paginated fixture search. Returns (page, total)."""
        base = self._filtered(
            sport_key=sport_key,
            competition_id=competition_id,
            status=status,
            start_from=start_from,
            start_to=start_to,
            query=query,
            include_deleted=include_deleted,
        )
        total = await self.session.scalar(select(func.count()).select_from(base.subquery()))
        page = base.order_by(Fixture.scheduled_start).limit(limit).offset(offset)
        page = page.options(
            selectinload(Fixture.competition),
            selectinload(Fixture.home_team),
            selectinload(Fixture.away_team),
        )
        return (await self.session.scalars(page)).all(), int(total or 0)

    async def get_with_relations(self, fixture_id: uuid.UUID) -> Fixture | None:
        stmt = (
            select(Fixture)
            .where(Fixture.id == fixture_id)
            .options(
                selectinload(Fixture.competition),
                selectinload(Fixture.home_team),
                selectinload(Fixture.away_team),
            )
        )
        return await self.session.scalar(stmt)
