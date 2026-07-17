"""Metadata refresh — persists provider catalogs into the Stage 3 tables.

Refreshes **reference data only**: sports, competitions, teams, logos, and the
``provider_metadata`` health row. It deliberately does **not** fetch fixtures;
that is Stage 7's Fixture Import Pipeline.

Partial-failure semantics: each provider is refreshed independently and its
errors are collected into a report. One dead vendor never aborts the others,
and one malformed record never aborts its provider (see ``normalize_many``).

Uses the existing repositories exclusively — no raw SQL, no new tables.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.ports.sports_provider import (
    Competition as CompetitionDTO,
)
from app.domain.ports.sports_provider import (
    MetadataRefreshReport,
    ProviderRefreshReport,
    SportsProvider,
)
from app.domain.ports.sports_provider import (
    Sport as SportDTO,
)
from app.domain.ports.sports_provider import (
    Team as TeamDTO,
)
from app.domain.value_objects.enums import ProviderStatus
from app.exceptions.base import AppError
from app.infrastructure.providers.registry import SportsProviderRegistry
from app.persistence.models.catalog import Competition, Sport, Team
from app.persistence.repositories.catalog import (
    CompetitionRepository,
    SportRepository,
    TeamRepository,
)
from app.persistence.repositories.system import ProviderMetadataRepository

logger = get_logger(__name__)


class MetadataService:
    def __init__(
        self,
        session: AsyncSession,
        registry: SportsProviderRegistry,
        sports: SportRepository,
        competitions: CompetitionRepository,
        teams: TeamRepository,
        providers: ProviderMetadataRepository,
    ) -> None:
        self._session = session
        self._registry = registry
        self._sports = sports
        self._competitions = competitions
        self._teams = teams
        self._providers = providers

    async def refresh(self, *, sport_keys: list[str] | None = None) -> MetadataRefreshReport:
        """Refresh metadata for all providers (or those serving ``sport_keys``)."""
        if sport_keys:
            providers = {self._registry.get_for_sport(key) for key in sport_keys}
        else:
            providers = set(self._registry.all())

        reports = [await self._refresh_provider(p) for p in sorted(providers, key=lambda p: p.key)]
        await self._session.commit()
        return MetadataRefreshReport(providers=tuple(reports))

    async def _refresh_provider(self, provider: SportsProvider) -> ProviderRefreshReport:
        started = time.perf_counter()
        errors: list[str] = []
        sports_count = competitions_count = teams_count = 0

        try:
            for sport_dto in await provider.list_sports():
                sport = await self._upsert_sport(sport_dto)
                sports_count += 1

                for competition_dto in await provider.list_competitions(sport_dto.key):
                    competition = await self._upsert_competition(sport, competition_dto)
                    competitions_count += 1

                    try:
                        team_dtos = await provider.list_teams(competition_dto.external_id)
                    except AppError as exc:
                        # A single competition's teams failing must not abort the
                        # provider — record and continue.
                        errors.append(f"teams[{competition_dto.external_id}]: {exc.code}")
                        continue

                    for team_dto in team_dtos:
                        team = await self._upsert_team(sport, team_dto)
                        await self._teams.link_competition(team.id, competition.id)
                        teams_count += 1

        except AppError as exc:
            await self._mark_provider(provider.key, ok=False, error=exc.message)
            logger.warning("sports.metadata.provider_failed", provider=provider.key, code=exc.code)
            return ProviderRefreshReport(
                provider_key=provider.key, success=False, errors=(exc.code,)
            )

        await self._mark_provider(provider.key, ok=True, error=None)
        logger.info(
            "sports.metadata.refreshed",
            provider=provider.key,
            sports=sports_count,
            competitions=competitions_count,
            teams=teams_count,
            partial_errors=len(errors),
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return ProviderRefreshReport(
            provider_key=provider.key,
            success=True,
            sports=sports_count,
            competitions=competitions_count,
            teams=teams_count,
            errors=tuple(errors),
        )

    # --- upserts (keyed on the Stage 3 unique constraints) -----------------
    async def _upsert_sport(self, dto: SportDTO) -> Sport:
        sport = await self._sports.get_by_key(dto.key)
        if sport is None:
            return await self._sports.add(
                Sport(
                    key=dto.key,
                    name=dto.name,
                    category=dto.category,
                    provider_key=dto.provider_key,
                )
            )
        sport.name = dto.name
        sport.category = dto.category
        sport.provider_key = dto.provider_key
        return sport

    async def _upsert_competition(self, sport: Sport, dto: CompetitionDTO) -> Competition:
        existing = await self._competitions.get_by_provider_id(sport.id, dto.external_id)
        season_label = dto.season.label if dto.season else None
        if existing is None:
            return await self._competitions.add(
                Competition(
                    sport_id=sport.id,
                    provider_competition_id=dto.external_id,
                    name=dto.name,
                    type=dto.type,
                    country=dto.country,
                    season=season_label,
                )
            )
        existing.name = dto.name
        existing.type = dto.type
        existing.country = dto.country
        existing.season = season_label
        existing.deleted_at = None  # reappeared upstream
        return existing

    async def _upsert_team(self, sport: Sport, dto: TeamDTO) -> Team:
        existing = await self._teams.get_by_provider_id(sport.id, dto.external_id)
        if existing is None:
            return await self._teams.add(
                Team(
                    sport_id=sport.id,
                    provider_team_id=dto.external_id,
                    name=dto.name,
                    short_name=dto.short_name,
                    country=dto.country,
                    logo_url=dto.logo_url,
                )
            )
        existing.name = dto.name
        existing.short_name = dto.short_name
        existing.country = dto.country
        existing.logo_url = dto.logo_url
        existing.deleted_at = None
        return existing

    async def _mark_provider(self, provider_key: str, *, ok: bool, error: str | None) -> None:
        """Record provider health on the existing ``provider_metadata`` row."""
        row = await self._providers.get_by_key(provider_key)
        if row is None:
            return  # seeded by scripts/seed.py; absent rows are not created here
        now = datetime.now(UTC)
        row.last_health_check_at = now
        row.status = ProviderStatus.HEALTHY if ok else ProviderStatus.DOWN
        if ok:
            row.last_success_at = now
            row.last_error = None
        else:
            row.last_error = error
