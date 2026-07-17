"""Read-side service for persisted fixtures and import runs.

Kept separate from ingestion: writing and reading fixtures are different
concerns with different lifetimes. All SQL lives in the repositories.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from app.domain.value_objects.enums import FixtureStatus
from app.exceptions.base import NotFoundError
from app.persistence.models.fixture import Fixture
from app.persistence.models.ingestion import FixtureVersion, ImportRun
from app.persistence.repositories.fixture import FixtureRepository
from app.persistence.repositories.ingestion import (
    FixtureVersionRepository,
    ImportRunRepository,
)


class FixtureNotFoundError(NotFoundError):
    code = "fixture_not_found"
    message = "The fixture does not exist."


class ImportRunNotFoundError(NotFoundError):
    code = "import_run_not_found"
    message = "The import run does not exist."


class FixtureQueryService:
    def __init__(
        self,
        fixtures: FixtureRepository,
        versions: FixtureVersionRepository,
        runs: ImportRunRepository,
    ) -> None:
        self._fixtures = fixtures
        self._versions = versions
        self._runs = runs

    async def list_fixtures(
        self,
        *,
        sport_key: str | None = None,
        competition_id: uuid.UUID | None = None,
        status: FixtureStatus | None = None,
        start_from: datetime | None = None,
        start_to: datetime | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[Fixture], int]:
        return await self._fixtures.search(
            sport_key=sport_key,
            competition_id=competition_id,
            status=status,
            start_from=start_from,
            start_to=start_to,
            query=query,
            limit=limit,
            offset=offset,
        )

    async def get_fixture(self, fixture_id: uuid.UUID) -> Fixture:
        fixture = await self._fixtures.get_with_relations(fixture_id)
        if fixture is None or fixture.deleted_at is not None:
            raise FixtureNotFoundError()
        return fixture

    async def get_fixture_versions(self, fixture_id: uuid.UUID) -> Sequence[FixtureVersion]:
        return await self._versions.list_for_fixture(fixture_id)

    async def list_import_runs(
        self, *, provider_key: str | None = None, limit: int = 20
    ) -> Sequence[ImportRun]:
        return await self._runs.list_recent(provider_key=provider_key, limit=limit)

    async def get_import_run(self, run_id: uuid.UUID) -> ImportRun:
        run = await self._runs.get(run_id)
        if run is None:
            raise ImportRunNotFoundError()
        return run
