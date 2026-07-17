"""SportsService + MetadataService tests against a fake provider and SQLite.

Proves the service works against any implementation of the port, that metadata
refresh upserts idempotently, and that partial failures degrade gracefully.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.metadata_service import MetadataService
from app.application.services.sports_service import SportsService
from app.domain.ports.sports_provider import (
    Competition,
    Fixture,
    Participant,
    ParticipantSide,
    ProviderCapability,
    ProviderConfig,
    ProviderInfo,
    SearchEntityType,
    Season,
    Sport,
    Team,
)
from app.domain.value_objects.enums import (
    CompetitionType,
    FixtureStatus,
    ProviderStatus,
    SportCategory,
)
from app.domain.value_objects.time_window import TimeWindow
from app.exceptions.sports import CapabilityNotSupportedError, SportsProviderError
from app.infrastructure.cache import InMemoryCache
from app.infrastructure.providers.registry import SportsProviderRegistry
from app.persistence.models.catalog import Competition as CompetitionModel
from app.persistence.models.catalog import Team as TeamModel
from app.persistence.models.system import ProviderMetadata
from app.persistence.repositories.catalog import (
    CompetitionRepository,
    SportRepository,
    TeamRepository,
)
from app.persistence.repositories.system import ProviderMetadataRepository

PROVIDER_KEY = "fake-api"
SPORT_KEY = "fakeball"
WINDOW = TimeWindow(datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 8, tzinfo=UTC))


class FakeSportsProvider:
    key = PROVIDER_KEY
    name = "Fake"
    version = "1.0"
    capabilities = frozenset({ProviderCapability.LIVE_SCORES})
    supported_sports = (SPORT_KEY,)

    def __init__(self) -> None:
        self.config = ProviderConfig(
            key=self.key,
            name=self.name,
            base_url="https://x",
            api_key="k",
            cache_ttl_seconds=60,
        )
        self.competition_calls = 0
        self.fail_sports = False
        self.fail_teams_for: set[str] = set()
        self.competitions = [
            Competition(
                external_id="C1",
                name="Cup One",
                sport_key=SPORT_KEY,
                type=CompetitionType.LEAGUE,
                country="Nowhere",
                season=Season(label="2025/26"),
            )
        ]
        self.teams = [Team(external_id="T1", name="Team One", sport_key=SPORT_KEY, short_name="T1")]

    def provider_info(self) -> ProviderInfo:
        return ProviderInfo(
            key=self.key,
            name=self.name,
            version=self.version,
            capabilities=self.capabilities,
            supported_sports=self.supported_sports,
        )

    async def list_sports(self) -> list[Sport]:
        if self.fail_sports:
            raise SportsProviderError("upstream exploded")
        return [
            Sport(
                key=SPORT_KEY, name="Fakeball", category=SportCategory.TEAM, provider_key=self.key
            )
        ]

    async def list_competitions(self, sport_key: str, *, season: str | None = None):
        self.competition_calls += 1
        return list(self.competitions)

    async def get_competition(self, external_id: str) -> Competition:
        return self.competitions[0]

    async def list_teams(self, competition_id: str) -> list[Team]:
        if competition_id in self.fail_teams_for:
            raise SportsProviderError("teams endpoint down")
        return list(self.teams)

    async def get_team(self, external_id: str) -> Team:
        return self.teams[0]

    async def get_fixtures(self, competition_id: str, window: TimeWindow) -> list[Fixture]:
        return [
            Fixture(
                external_id="F1",
                competition_id=competition_id,
                sport_key=SPORT_KEY,
                start=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
                status=FixtureStatus.SCHEDULED,
                participants=(Participant("T1", "Team One", ParticipantSide.HOME),),
            )
        ]

    async def get_standings(self, competition_id: str):
        raise AssertionError("not supported")  # pragma: no cover


@pytest_asyncio.fixture
async def ctx(db_session: AsyncSession):
    db_session.add(ProviderMetadata(key=PROVIDER_KEY, name="Fake", provider_type="sports"))
    await db_session.commit()

    provider = FakeSportsProvider()
    registry = SportsProviderRegistry()
    registry.register(provider)

    metadata = MetadataService(
        db_session,
        registry,
        SportRepository(db_session),
        CompetitionRepository(db_session),
        TeamRepository(db_session),
        ProviderMetadataRepository(db_session),
    )
    cache = InMemoryCache()
    service = SportsService(
        registry,
        cache,
        SportRepository(db_session),
        CompetitionRepository(db_session),
        TeamRepository(db_session),
        metadata,
    )
    return service, provider, db_session, cache


# --- metadata refresh ------------------------------------------------------
async def test_refresh_persists_sports_competitions_and_teams(ctx) -> None:
    service, _, db, _ = ctx
    report = await service.refresh_metadata()

    assert report.ok is True
    assert report.providers[0].sports == 1
    assert report.providers[0].competitions == 1
    assert report.providers[0].teams == 1

    assert await db.scalar(select(func.count()).select_from(CompetitionModel)) == 1
    assert await db.scalar(select(func.count()).select_from(TeamModel)) == 1


async def test_refresh_is_idempotent_and_updates_in_place(ctx) -> None:
    service, provider, db, _ = ctx
    await service.refresh_metadata()

    provider.competitions = [
        Competition(external_id="C1", name="Cup One Renamed", sport_key=SPORT_KEY)
    ]
    await service.refresh_metadata()

    assert await db.scalar(select(func.count()).select_from(CompetitionModel)) == 1
    comp = await db.scalar(select(CompetitionModel))
    assert comp.name == "Cup One Renamed"


async def test_refresh_links_teams_to_competitions_without_duplicates(ctx) -> None:
    service, _, db, _ = ctx
    await service.refresh_metadata()
    await service.refresh_metadata()  # link must be idempotent

    from app.persistence.models.catalog import team_competition

    links = await db.scalar(select(func.count()).select_from(team_competition))
    assert links == 1


async def test_partial_failure_keeps_provider_successful(ctx) -> None:
    """A competition whose teams fail is recorded, not fatal."""
    service, provider, db, _ = ctx
    provider.fail_teams_for = {"C1"}

    report = await service.refresh_metadata()
    provider_report = report.providers[0]
    assert provider_report.success is True
    assert provider_report.competitions == 1
    assert provider_report.teams == 0
    assert provider_report.errors and "teams[C1]" in provider_report.errors[0]

    row = await ProviderMetadataRepository(db).get_by_key(PROVIDER_KEY)
    assert row.status is ProviderStatus.HEALTHY


async def test_provider_failure_is_isolated_and_marks_provider_down(ctx) -> None:
    service, provider, db, _ = ctx
    provider.fail_sports = True

    report = await service.refresh_metadata()
    assert report.ok is False
    assert report.providers[0].success is False

    row = await ProviderMetadataRepository(db).get_by_key(PROVIDER_KEY)
    assert row.status is ProviderStatus.DOWN
    assert row.last_error


# --- caching ---------------------------------------------------------------
async def test_competitions_are_cached_and_provider_called_once(ctx) -> None:
    service, provider, _, _ = ctx
    await service.list_competitions(SPORT_KEY)
    await service.list_competitions(SPORT_KEY)
    assert provider.competition_calls == 1


async def test_refresh_invalidates_the_cache(ctx) -> None:
    service, provider, _, _ = ctx
    await service.list_competitions(SPORT_KEY)
    assert provider.competition_calls == 1

    await service.refresh_metadata()  # invalidates the provider namespace
    await service.list_competitions(SPORT_KEY)
    # refresh itself calls the provider once, then the post-refresh read misses.
    assert provider.competition_calls == 3


async def test_cached_models_survive_a_serialization_roundtrip(ctx) -> None:
    service, _, _, _ = ctx
    first = await service.list_competitions(SPORT_KEY)
    second = await service.list_competitions(SPORT_KEY)  # from cache
    assert first == second
    assert second[0].season is not None and second[0].season.label == "2025/26"


# --- fixtures (fetched, never persisted) -----------------------------------
async def test_get_fixtures_returns_normalized_models_and_persists_nothing(ctx) -> None:
    service, _, db, _ = ctx
    fixtures = await service.get_fixtures(SPORT_KEY, "C1", WINDOW)
    assert len(fixtures) == 1 and fixtures[0].status is FixtureStatus.SCHEDULED

    from app.persistence.models.fixture import Fixture as FixtureModel

    assert await db.scalar(select(func.count()).select_from(FixtureModel)) == 0


# --- capabilities ----------------------------------------------------------
async def test_standings_rejected_when_capability_absent(ctx) -> None:
    service, _, _, _ = ctx
    with pytest.raises(CapabilityNotSupportedError):
        await service.get_standings(SPORT_KEY, "C1")


def test_capability_matrix_is_exposed(ctx) -> None:
    service, _, _, _ = ctx
    assert service.capabilities() == {PROVIDER_KEY: ["live_scores"]}
    assert service.list_providers()[0].key == PROVIDER_KEY


# --- search ----------------------------------------------------------------
async def test_search_finds_competitions_and_teams_case_insensitively(ctx) -> None:
    service, _, _, _ = ctx
    await service.refresh_metadata()

    results = await service.search("team one")
    assert results.total >= 1
    assert any(h.type is SearchEntityType.TEAM and h.name == "Team One" for h in results.hits)

    results = await service.search("CUP")
    assert any(h.type is SearchEntityType.COMPETITION for h in results.hits)


async def test_search_can_be_restricted_by_type(ctx) -> None:
    service, _, _, _ = ctx
    await service.refresh_metadata()
    results = await service.search("one", types={SearchEntityType.TEAM})
    assert results.hits and all(h.type is SearchEntityType.TEAM for h in results.hits)


async def test_blank_search_returns_no_hits(ctx) -> None:
    service, _, _, _ = ctx
    assert (await service.search("   ")).hits == ()
