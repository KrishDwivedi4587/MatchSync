"""SportsService — the sports platform's public surface (the "SDK").

Future services call these methods and never learn which vendor, which sport, or
which HTTP API supplied the data:

    await sports_service.list_sports()
    await sports_service.list_competitions("football")
    await sports_service.list_teams("football", "PL")
    await sports_service.get_fixtures("football", "PL", window)   # Stage 7 uses this
    await sports_service.search("arsenal")
    await sports_service.refresh_metadata()

Responsibilities: resolve sport -> provider via the registry, apply read-through
caching for metadata, guard optional calls behind capabilities, and delegate.

Explicitly NOT here: fixture persistence, reconciliation, scheduling, calendars,
or anything sport-specific. ``get_fixtures`` fetches and returns normalized
models — it stores nothing.
"""

from __future__ import annotations

from app.application.services.metadata_service import MetadataService
from app.core.logging import get_logger
from app.domain.ports.sports_provider import (
    Competition,
    Fixture,
    MetadataRefreshReport,
    ProviderCapability,
    ProviderInfo,
    SearchEntityType,
    SearchHit,
    SearchResults,
    Sport,
    SportsProvider,
    Standing,
    Team,
)
from app.domain.sports.codec import (
    competition_from_dict,
    competition_to_dict,
    sport_from_dict,
    sport_to_dict,
    team_from_dict,
    team_to_dict,
)
from app.domain.value_objects.time_window import TimeWindow
from app.exceptions.sports import CapabilityNotSupportedError
from app.infrastructure.cache import Cache, cache_key, cached_json
from app.infrastructure.providers.registry import SportsProviderRegistry
from app.persistence.repositories.catalog import (
    CompetitionRepository,
    SportRepository,
    TeamRepository,
)

logger = get_logger(__name__)


class SportsService:
    def __init__(
        self,
        registry: SportsProviderRegistry,
        cache: Cache,
        sports: SportRepository,
        competitions: CompetitionRepository,
        teams: TeamRepository,
        metadata_service: MetadataService,
    ) -> None:
        self._registry = registry
        self._cache = cache
        self._sports = sports
        self._competitions = competitions
        self._teams = teams
        self._metadata = metadata_service

    # --- providers & capabilities -------------------------------------------
    def list_providers(self) -> list[ProviderInfo]:
        return self._registry.provider_infos()

    def capabilities(self) -> dict[str, list[str]]:
        return self._registry.capabilities()

    def supports(self, sport_key: str, capability: ProviderCapability) -> bool:
        return capability in self._registry.get_for_sport(sport_key).capabilities

    # --- catalog reads (cached) ---------------------------------------------
    async def list_sports(self) -> list[Sport]:
        """Every sport served by every registered provider."""
        sports: list[Sport] = []
        for provider in self._registry.all():
            key = cache_key(provider.key, "sports")

            async def load(p: SportsProvider = provider) -> list[dict[str, object]]:
                return [sport_to_dict(s) for s in await p.list_sports()]

            payload = await cached_json(
                self._cache, key, provider.config.cache_ttl_seconds, load, label="sports"
            )
            sports.extend(sport_from_dict(item) for item in payload)
        return sports

    async def list_competitions(self, sport_key: str) -> list[Competition]:
        provider = self._registry.get_for_sport(sport_key)
        key = cache_key(provider.key, sport_key, "competitions")

        async def load() -> list[dict[str, object]]:
            return [competition_to_dict(c) for c in await provider.list_competitions(sport_key)]

        payload = await cached_json(
            self._cache, key, provider.config.cache_ttl_seconds, load, label="competitions"
        )
        return [competition_from_dict(item) for item in payload]

    async def get_competition(self, sport_key: str, external_id: str) -> Competition:
        provider = self._registry.get_for_sport(sport_key)
        key = cache_key(provider.key, sport_key, "competition", external_id)

        async def load() -> dict[str, object]:
            return competition_to_dict(await provider.get_competition(external_id))

        return competition_from_dict(
            await cached_json(
                self._cache, key, provider.config.cache_ttl_seconds, load, label="competition"
            )
        )

    async def list_teams(self, sport_key: str, competition_id: str) -> list[Team]:
        provider = self._registry.get_for_sport(sport_key)
        key = cache_key(provider.key, sport_key, "teams", competition_id)

        async def load() -> list[dict[str, object]]:
            return [team_to_dict(t) for t in await provider.list_teams(competition_id)]

        payload = await cached_json(
            self._cache, key, provider.config.cache_ttl_seconds, load, label="teams"
        )
        return [team_from_dict(item) for item in payload]

    async def get_team(self, sport_key: str, external_id: str) -> Team:
        provider = self._registry.get_for_sport(sport_key)
        key = cache_key(provider.key, sport_key, "team", external_id)

        async def load() -> dict[str, object]:
            return team_to_dict(await provider.get_team(external_id))

        return team_from_dict(
            await cached_json(
                self._cache, key, provider.config.cache_ttl_seconds, load, label="team"
            )
        )

    # --- fixtures (fetch + normalize; NOT cached, NOT persisted) ------------
    async def get_fixtures(
        self, sport_key: str, competition_id: str, window: TimeWindow
    ) -> list[Fixture]:
        """Normalized fixtures for a competition and time window.

        Stage 7's import pipeline is the intended consumer. Fixtures are volatile
        so they are never cached, and this stage persists nothing.
        """
        provider = self._registry.get_for_sport(sport_key)
        return await provider.get_fixtures(competition_id, window)

    # --- optional capability -------------------------------------------------
    async def get_standings(self, sport_key: str, competition_id: str) -> list[Standing]:
        provider = self._registry.get_for_sport(sport_key)
        if ProviderCapability.STANDINGS not in provider.capabilities:
            raise CapabilityNotSupportedError(f"Sport '{sport_key}' does not support standings.")
        return await provider.get_standings(competition_id)

    # --- search (over persisted metadata; provider-independent) -------------
    async def search(
        self, query: str, *, types: set[SearchEntityType] | None = None, limit: int = 20
    ) -> SearchResults:
        """Search the locally-persisted catalog.

        Deliberately reads the database rather than fanning out to providers:
        results are instant, quota-free, and identical regardless of which vendor
        supplied the record. Providers that offer remote search advertise
        ``REMOTE_SEARCH``; nothing uses it yet.
        """
        term = query.strip()
        if not term:
            return SearchResults(query=query)

        wanted = types or set(SearchEntityType)
        hits: list[SearchHit] = []

        if SearchEntityType.SPORT in wanted:
            for sport in await self._sports.list_active():
                if term.lower() in sport.name.lower():
                    hits.append(
                        SearchHit(
                            type=SearchEntityType.SPORT,
                            id=str(sport.id),
                            name=sport.name,
                            sport_key=sport.key,
                        )
                    )

        if SearchEntityType.COMPETITION in wanted:
            hits.extend(
                SearchHit(
                    type=SearchEntityType.COMPETITION,
                    id=str(c.id),
                    name=c.name,
                    subtitle=c.country,
                )
                for c in await self._competitions.search(term, limit=limit)
            )

        if SearchEntityType.TEAM in wanted:
            hits.extend(
                SearchHit(
                    type=SearchEntityType.TEAM,
                    id=str(t.id),
                    name=t.name,
                    subtitle=t.country,
                    logo_url=t.logo_url,
                )
                for t in await self._teams.search(term, limit=limit)
            )

        logger.info("sports.search", term_length=len(term), hits=len(hits))
        return SearchResults(query=query, hits=tuple(hits[:limit]), total=len(hits))

    # --- metadata refresh ----------------------------------------------------
    async def refresh_metadata(
        self, *, sport_keys: list[str] | None = None
    ) -> MetadataRefreshReport:
        """Refresh reference data, then invalidate the affected cache namespaces."""
        report = await self._metadata.refresh(sport_keys=sport_keys)
        for provider_report in report.providers:
            await self._cache.delete_prefix(cache_key(provider_report.provider_key))
        logger.info("sports.metadata.cache_invalidated", providers=len(report.providers))
        return report
