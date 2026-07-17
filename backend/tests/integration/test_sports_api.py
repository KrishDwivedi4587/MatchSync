"""Sports metadata API endpoint tests.

Authentication is covered in Stage 4, so ``get_current_user`` is overridden.
The provider registry and cache are replaced with in-process fakes.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.v1.deps import (
    get_current_user,
    get_db,
    get_sports_cache,
    get_sports_registry_dep,
)
from app.infrastructure.cache import InMemoryCache
from app.infrastructure.providers.registry import SportsProviderRegistry
from app.main import app
from app.persistence.models.system import ProviderMetadata
from app.persistence.models.user import User
from tests.integration.test_sports_service import PROVIDER_KEY, SPORT_KEY, FakeSportsProvider


@pytest_asyncio.fixture
async def api(engine: AsyncEngine) -> AsyncGenerator[tuple[AsyncClient, FakeSportsProvider]]:
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as setup:
        user = User(email="sports@example.com")
        setup.add(user)
        setup.add(ProviderMetadata(key=PROVIDER_KEY, name="Fake", provider_type="sports"))
        await setup.commit()

    provider = FakeSportsProvider()
    registry = SportsProviderRegistry()
    registry.register(provider)
    cache = InMemoryCache()

    async def _get_db() -> AsyncGenerator:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_sports_registry_dep] = lambda: registry
    app.dependency_overrides[get_sports_cache] = lambda: cache

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, provider

    app.dependency_overrides.clear()


async def test_list_sports(api) -> None:
    client, _ = api
    r = await client.get("/api/v1/sports")
    assert r.status_code == 200
    assert r.json() == [
        {"key": SPORT_KEY, "name": "Fakeball", "category": "team", "provider_key": PROVIDER_KEY}
    ]


async def test_list_competitions_requires_sport_param(api) -> None:
    client, _ = api
    assert (await client.get("/api/v1/competitions")).status_code == 422

    r = await client.get(f"/api/v1/competitions?sport={SPORT_KEY}")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["external_id"] == "C1"
    assert body[0]["season"]["label"] == "2025/26"


async def test_list_teams(api) -> None:
    client, _ = api
    r = await client.get(f"/api/v1/teams?sport={SPORT_KEY}&competition=C1")
    assert r.status_code == 200
    assert r.json()[0]["name"] == "Team One"


async def test_list_providers_exposes_configuration_state(api) -> None:
    client, _ = api
    r = await client.get("/api/v1/providers")
    assert r.status_code == 200
    info = r.json()[0]
    assert info["key"] == PROVIDER_KEY
    assert info["supported_sports"] == [SPORT_KEY]
    assert info["capabilities"] == ["live_scores"]
    assert info["configured"] is True


async def test_capabilities_matrix(api) -> None:
    client, _ = api
    r = await client.get("/api/v1/capabilities")
    assert r.json() == {PROVIDER_KEY: ["live_scores"]}


async def test_metadata_refresh_reports_counts(api) -> None:
    client, _ = api
    r = await client.post("/api/v1/metadata/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["providers"][0] == {
        "provider_key": PROVIDER_KEY,
        "success": True,
        "sports": 1,
        "competitions": 1,
        "teams": 1,
        "errors": [],
    }


async def test_search_returns_provider_independent_hits(api) -> None:
    client, _ = api
    await client.post("/api/v1/metadata/refresh")

    r = await client.get("/api/v1/search?q=team")
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "team"
    assert any(hit["type"] == "team" and hit["name"] == "Team One" for hit in body["hits"])


async def test_search_can_filter_by_type(api) -> None:
    client, _ = api
    await client.post("/api/v1/metadata/refresh")
    r = await client.get("/api/v1/search?q=one&types=team")
    assert all(hit["type"] == "team" for hit in r.json()["hits"])


async def test_search_rejects_empty_query(api) -> None:
    client, _ = api
    assert (await client.get("/api/v1/search?q=")).status_code == 422


def test_sports_router_exposes_no_sync_or_schedule_routes() -> None:
    """The sports platform itself must not expose sync or scheduling.

    (Fixture endpoints arrived in Stage 7 and /sync in Stage 8; neither belongs
    to the sports platform. They are covered by their own test modules.)
    """
    sports_paths = {
        "/api/v1/sports",
        "/api/v1/competitions",
        "/api/v1/teams",
        "/api/v1/providers",
        "/api/v1/capabilities",
        "/api/v1/search",
        "/api/v1/metadata/refresh",
    }
    registered = {getattr(route, "path", "") for route in app.routes}
    assert sports_paths <= registered
    assert not any("sync" in p or "schedule" in p for p in sports_paths)
