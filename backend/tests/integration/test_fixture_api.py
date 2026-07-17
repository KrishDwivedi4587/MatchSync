"""Fixture ingestion + read API endpoint tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.v1.deps import (
    get_current_user,
    get_db,
    get_fixture_ingestion_service,
)
from app.application.services.fixture_ingestion_service import FixtureIngestionService
from app.core.config import get_settings
from app.domain.value_objects.enums import CompetitionType, FixtureStatus, SportCategory
from app.main import app
from app.persistence.models.catalog import Competition, Sport, Team
from app.persistence.models.user import User
from app.persistence.repositories.catalog import (
    CompetitionRepository,
    SportRepository,
    TeamRepository,
)
from app.persistence.repositories.fixture import FixtureRepository
from app.persistence.repositories.ingestion import (
    FixtureVersionRepository,
    ImportRunRepository,
)
from tests.integration.test_fixture_ingestion import (
    COMP_ID,
    PROVIDER,
    SPORT_KEY,
    START,
    FakeRegistry,
    FakeSportsService,
    make_dto,
)


@pytest_asyncio.fixture
async def api(engine: AsyncEngine) -> AsyncGenerator[tuple[AsyncClient, FakeSportsService]]:
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as setup:
        user = User(email="fx@example.com")
        sport = Sport(
            key=SPORT_KEY, name="Football", category=SportCategory.TEAM, provider_key=PROVIDER
        )
        competition = Competition(
            sport=sport,
            provider_competition_id=COMP_ID,
            name="Premier League",
            type=CompetitionType.LEAGUE,
        )
        setup.add_all(
            [
                user,
                sport,
                competition,
                Team(sport=sport, provider_team_id="57", name="Arsenal"),
                Team(sport=sport, provider_team_id="61", name="Chelsea"),
            ]
        )
        await setup.commit()

    sports_service = FakeSportsService()

    async def _get_db() -> AsyncGenerator:
        async with factory() as session:
            yield session

    def _ingestion(db=None):
        session = db
        return session

    async def _get_ingestion() -> AsyncGenerator:
        async with factory() as session:
            yield FixtureIngestionService(
                session,
                sports_service,
                FakeRegistry(),
                SportRepository(session),
                CompetitionRepository(session),
                TeamRepository(session),
                FixtureRepository(session),
                FixtureVersionRepository(session),
                ImportRunRepository(session),
                get_settings(),
            )

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_fixture_ingestion_service] = _get_ingestion

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, sports_service

    app.dependency_overrides.clear()


async def test_import_endpoint_returns_report(api) -> None:
    client, sports = api
    sports.fixtures = [make_dto()]

    r = await client.post("/api/v1/fixtures/import", json={"sport": SPORT_KEY})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["provider_key"] == PROVIDER
    assert body["stats"]["created"] == 1
    assert body["errors"] == []


async def test_import_endpoint_is_idempotent(api) -> None:
    client, sports = api
    sports.fixtures = [make_dto()]
    await client.post("/api/v1/fixtures/import", json={"sport": SPORT_KEY})
    r = await client.post("/api/v1/fixtures/import", json={"sport": SPORT_KEY})

    assert r.json()["stats"]["created"] == 0
    assert r.json()["stats"]["unchanged"] == 1

    listing = await client.get("/api/v1/fixtures")
    assert listing.json()["total"] == 1  # zero duplicates


async def test_import_provider_endpoint(api) -> None:
    client, sports = api
    sports.fixtures = [make_dto()]
    r = await client.post("/api/v1/fixtures/import/provider", json={"provider": PROVIDER})
    assert r.status_code == 200
    assert r.json()["stats"]["created"] == 1


async def test_import_accepts_custom_window(api) -> None:
    client, sports = api
    sports.fixtures = [make_dto()]
    r = await client.post(
        "/api/v1/fixtures/import", json={"sport": SPORT_KEY, "past_days": 1, "future_days": 2}
    )
    # START is far in the future relative to "now", so it falls outside a 2-day window.
    assert r.json()["stats"]["skipped_out_of_window"] == 1


async def test_import_status_and_report_endpoints(api) -> None:
    client, sports = api
    sports.fixtures = [make_dto()]
    report = (await client.post("/api/v1/fixtures/import", json={"sport": SPORT_KEY})).json()

    status = await client.get("/api/v1/fixtures/import/status")
    assert status.status_code == 200
    runs = status.json()["runs"]
    assert runs[0]["id"] == report["id"]
    assert runs[0]["created_count"] == 1

    detail = await client.get(f"/api/v1/fixtures/import/report/{report['id']}")
    assert detail.status_code == 200
    assert detail.json()["report"]["stats"]["created"] == 1


async def test_report_for_unknown_run_is_404(api) -> None:
    client, _ = api
    import uuid

    r = await client.get(f"/api/v1/fixtures/import/report/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "import_run_not_found"


# --- reads ------------------------------------------------------------------
async def test_list_fixtures_with_filters_and_pagination(api) -> None:
    client, sports = api
    sports.fixtures = [
        make_dto(external_id="m1", start=START),
        make_dto(external_id="m2", start=START + timedelta(days=1), status=FixtureStatus.POSTPONED),
    ]
    await client.post("/api/v1/fixtures/import", json={"sport": SPORT_KEY})

    everything = await client.get("/api/v1/fixtures")
    assert everything.json()["total"] == 2

    by_status = await client.get("/api/v1/fixtures?status=postponed")
    assert by_status.json()["total"] == 1

    by_sport = await client.get(f"/api/v1/fixtures?sport={SPORT_KEY}")
    assert by_sport.json()["total"] == 2

    paged = await client.get("/api/v1/fixtures?limit=1&offset=1")
    body = paged.json()
    assert body["total"] == 2 and len(body["fixtures"]) == 1 and body["offset"] == 1


async def test_search_fixtures_by_team_name(api) -> None:
    client, sports = api
    sports.fixtures = [make_dto()]
    await client.post("/api/v1/fixtures/import", json={"sport": SPORT_KEY})

    r = await client.get("/api/v1/fixtures?q=arsenal")
    assert r.json()["total"] == 1
    r = await client.get("/api/v1/fixtures?q=nonexistent")
    assert r.json()["total"] == 0


async def test_fixture_detail_includes_version_history(api) -> None:
    client, sports = api
    sports.fixtures = [make_dto()]
    await client.post("/api/v1/fixtures/import", json={"sport": SPORT_KEY})
    sports.fixtures = [make_dto(status=FixtureStatus.POSTPONED)]
    await client.post("/api/v1/fixtures/import", json={"sport": SPORT_KEY})

    listing = await client.get("/api/v1/fixtures")
    fixture_id = listing.json()["fixtures"][0]["id"]

    r = await client.get(f"/api/v1/fixtures/{fixture_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 2
    assert body["home_team"]["name"] == "Arsenal"
    assert [v["version"] for v in body["versions"]] == [2, 1]
    assert body["versions"][0]["change_type"] == "postponed"


async def test_unknown_fixture_is_404(api) -> None:
    client, _ = api
    import uuid

    r = await client.get(f"/api/v1/fixtures/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "fixture_not_found"


def test_fixtures_router_exposes_no_synchronization_routes() -> None:
    """The ingestion platform must not expose sync.

    (Synchronization endpoints DO exist as of Stage 8 under /sync; they are not
    part of the fixture platform and are covered by test_sync_api.py.)
    """
    fixture_paths = {
        getattr(route, "path", "")
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/v1/fixtures")
    }
    assert [p for p in fixture_paths if "sync" in p] == []
    assert [p for p in fixture_paths if "subscription" in p] == []
