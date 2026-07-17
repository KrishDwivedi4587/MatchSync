"""Repositories for the ingestion audit tables."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import desc, insert, select

from app.persistence.models.ingestion import FixtureVersion, ImportRun
from app.persistence.repositories.base import BaseRepository


class ImportRunRepository(BaseRepository[ImportRun]):
    model = ImportRun

    async def list_recent(
        self, *, provider_key: str | None = None, limit: int = 20
    ) -> Sequence[ImportRun]:
        stmt = select(ImportRun).order_by(desc(ImportRun.created_at)).limit(limit)
        if provider_key:
            stmt = stmt.where(ImportRun.provider_key == provider_key)
        return (await self.session.scalars(stmt)).all()


class FixtureVersionRepository(BaseRepository[FixtureVersion]):
    model = FixtureVersion

    async def list_for_fixture(self, fixture_id: uuid.UUID) -> Sequence[FixtureVersion]:
        stmt = (
            select(FixtureVersion)
            .where(FixtureVersion.fixture_id == fixture_id)
            .order_by(desc(FixtureVersion.version))
        )
        return (await self.session.scalars(stmt)).all()

    async def bulk_insert(self, rows: list[dict[str, Any]]) -> None:
        """Insert many version rows in one round-trip. Rows must carry ``id``."""
        if rows:
            await self.session.execute(insert(FixtureVersion), rows)
