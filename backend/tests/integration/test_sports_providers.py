"""Provider adapter tests against mocked upstream APIs.

The headline test is ``test_all_providers_emit_identical_model_shapes``: three
vendors with three different envelopes, field names, status vocabularies, and
date formats must produce structurally identical domain models.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.domain.ports.sports_provider import (
    Competition,
    Fixture,
    ParticipantSide,
    ProviderCapability,
    ProviderConfig,
    Team,
)
from app.domain.value_objects.enums import CompetitionType, FixtureStatus
from app.domain.value_objects.time_window import TimeWindow
from app.exceptions.sports import (
    CapabilityNotSupportedError,
    MalformedResponseError,
    ProviderAuthenticationError,
    ProviderUnavailableError,
    RateLimitError,
)
from app.infrastructure.http.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from app.infrastructure.http.resilient import ResilientHttpClient, RetryPolicy
from app.infrastructure.providers.basketball import BasketballProvider
from app.infrastructure.providers.football import FootballProvider
from app.infrastructure.providers.valorant import ValorantProvider

WINDOW = TimeWindow(start=datetime(2026, 8, 1, tzinfo=UTC), end=datetime(2026, 8, 8, tzinfo=UTC))


def _build(provider_cls, handler, *, max_attempts: int = 2, breaker: CircuitBreaker | None = None):
    config = ProviderConfig(
        key=provider_cls.key,
        name=provider_cls.name,
        base_url="https://api.test",
        api_key="secret-key",
        max_attempts=max_attempts,
    )
    http = ResilientHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(max_attempts=max_attempts, base_delay=0.0, max_delay=0.0),
    )
    return provider_cls(config, http, breaker or CircuitBreaker(provider_cls.key))


# --------------------------------------------------------------------------
# Payload fixtures — deliberately different shapes per vendor
# --------------------------------------------------------------------------
FOOTBALL_COMPETITIONS = {
    "competitions": [
        {
            "id": 2021,
            "code": "PL",
            "name": " Premier   League ",
            "type": "LEAGUE",
            "area": {"name": "England"},
            "emblem": "http://x/pl.png",
            "currentSeason": {"startDate": "2025-08-01", "endDate": "2026-05-30"},
        }
    ]
}
FOOTBALL_TEAMS = {
    "teams": [
        {
            "id": 57,
            "name": "Arsenal FC",
            "shortName": "Arsenal",
            "tla": "ARS",
            "crest": "http://x/ars.png",
            "area": {"name": "England"},
        }
    ]
}
FOOTBALL_MATCHES = {
    "matches": [
        {
            "id": 12345,
            "utcDate": "2026-08-01T15:00:00Z",
            "status": "TIMED",
            "matchday": 1,
            "stage": "REGULAR_SEASON",
            "lastUpdated": "2026-07-01T10:00:00Z",
            "homeTeam": {"id": 57, "name": "Arsenal FC"},
            "awayTeam": {"id": 61, "name": "Chelsea FC"},
        }
    ]
}

BASKETBALL_TEAMS = {"data": [{"id": 1, "full_name": "Los Angeles Lakers", "abbreviation": "LAL"}]}
BASKETBALL_GAMES = {
    "data": [
        {
            "id": 999,
            "date": "2026-08-01",
            "status": "Final",
            "postseason": True,
            "home_team": {"id": 1, "full_name": "Los Angeles Lakers"},
            "visitor_team": {"id": 2, "full_name": "Boston Celtics"},
        }
    ]
}

VALORANT_EVENTS = {
    "data": [
        {
            "id": "vct-champions",
            "name": "VCT Champions",
            "status": "ongoing",
            "region": "Global",
            "img": "http://x/vct.png",
            "dates": {"start": "2026-08-01", "end": "2026-08-20"},
        }
    ]
}
VALORANT_TEAMS = {"data": [{"id": "sen", "name": "Sentinels", "tag": "SEN", "country": "USA"}]}
VALORANT_MATCHES = {
    "data": [
        {
            "id": "m1",
            "utc": "2026-08-01T15:00:00Z",
            "status": "Upcoming",
            "round": "Upper Final",
            "stage": "Playoffs",
            "teams": [{"id": "sen", "name": "Sentinels"}, {"id": "lev", "name": "Leviatan"}],
        }
    ]
}


def _router(routes: dict[str, dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        for suffix, payload in routes.items():
            if request.url.path.endswith(suffix):
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"error": "not found"})

    return handler


# --------------------------------------------------------------------------
# The headline test
# --------------------------------------------------------------------------
async def test_all_providers_emit_identical_model_shapes() -> None:
    """Three vendors, three payload shapes, one set of domain models."""
    football = _build(
        FootballProvider,
        _router(
            {
                "/competitions": FOOTBALL_COMPETITIONS,
                "/PL/teams": FOOTBALL_TEAMS,
                "/PL/matches": FOOTBALL_MATCHES,
            }
        ),
    )
    basketball = _build(
        BasketballProvider,
        _router(
            {
                "/teams": BASKETBALL_TEAMS,
                "/games": BASKETBALL_GAMES,
            }
        ),
    )
    valorant = _build(
        ValorantProvider,
        _router(
            {
                "/events": VALORANT_EVENTS,
                "/vct-champions/teams": VALORANT_TEAMS,
                "/matches": VALORANT_MATCHES,
            }
        ),
    )

    cases = [
        (football, "football", "PL"),
        (basketball, "basketball", "nba"),
        (valorant, "valorant", "vct-champions"),
    ]

    for provider, sport_key, competition_id in cases:
        competitions = await provider.list_competitions(sport_key)
        teams = await provider.list_teams(competition_id)
        fixtures = await provider.get_fixtures(competition_id, WINDOW)

        assert competitions and teams and fixtures
        comp, team, fixture = competitions[0], teams[0], fixtures[0]

        # Identical types, identical invariants — regardless of vendor.
        assert isinstance(comp, Competition) and isinstance(comp.type, CompetitionType)
        assert comp.sport_key == sport_key and isinstance(comp.external_id, str)

        assert isinstance(team, Team) and team.sport_key == sport_key
        assert isinstance(team.external_id, str) and team.name == team.name.strip()

        assert isinstance(fixture, Fixture) and fixture.sport_key == sport_key
        assert isinstance(fixture.status, FixtureStatus)
        assert fixture.start.tzinfo is UTC  # always timezone-aware UTC
        assert len(fixture.participants) == 2
        assert all(isinstance(p.external_id, str) for p in fixture.participants)


async def test_football_normalizes_names_dates_and_status() -> None:
    provider = _build(
        FootballProvider,
        _router(
            {
                "/competitions": FOOTBALL_COMPETITIONS,
                "/PL/matches": FOOTBALL_MATCHES,
            }
        ),
    )
    comp = (await provider.list_competitions("football"))[0]
    assert comp.external_id == "PL"  # stable code preferred over numeric id
    assert comp.name == "Premier League"  # whitespace collapsed
    assert comp.season is not None and comp.season.label == "2025/26"

    fixture = (await provider.get_fixtures("PL", WINDOW))[0]
    assert fixture.status is FixtureStatus.SCHEDULED  # "TIMED" -> SCHEDULED
    assert fixture.start == datetime(2026, 8, 1, 15, 0, tzinfo=UTC)
    assert fixture.home.name == "Arsenal FC" and fixture.away.name == "Chelsea FC"


async def test_basketball_synthesizes_a_competition_and_maps_visitor_to_away() -> None:
    provider = _build(
        BasketballProvider,
        _router(
            {
                "/teams": BASKETBALL_TEAMS,
                "/games": BASKETBALL_GAMES,
            }
        ),
    )
    comps = await provider.list_competitions("basketball")
    assert [c.external_id for c in comps] == ["nba"]  # vendor has no such endpoint

    fixture = (await provider.get_fixtures("nba", WINDOW))[0]
    assert fixture.status is FixtureStatus.FINISHED  # "Final" -> FINISHED
    assert fixture.away.name == "Boston Celtics"  # "visitor_team" -> away
    assert fixture.start.tzinfo is UTC  # date-only string -> midnight UTC
    assert fixture.stage == "Postseason"


async def test_valorant_uses_neutral_sides_and_no_venue() -> None:
    provider = _build(
        ValorantProvider,
        _router(
            {
                "/events": VALORANT_EVENTS,
                "/matches": VALORANT_MATCHES,
            }
        ),
    )
    comp = (await provider.list_competitions("valorant"))[0]
    assert comp.type is CompetitionType.TOURNAMENT

    fixture = (await provider.get_fixtures("vct-champions", WINDOW))[0]
    assert fixture.venue is None  # online event
    assert all(p.side is ParticipantSide.NEUTRAL for p in fixture.participants)
    assert fixture.home is None and fixture.away is None
    assert fixture.status is FixtureStatus.SCHEDULED  # "Upcoming" -> SCHEDULED


# --------------------------------------------------------------------------
# Resilience: schema drift, malformed payloads, failures
# --------------------------------------------------------------------------
async def test_partial_failure_skips_bad_records_and_keeps_good_ones() -> None:
    payload = {
        "teams": [
            {"id": 57, "name": "Arsenal FC"},
            {"id": None, "name": "Broken"},  # missing required id
            {"name": "No id at all"},  # missing key entirely
            {"id": 61, "name": "Chelsea FC"},
        ]
    }
    provider = _build(FootballProvider, _router({"/PL/teams": payload}))
    teams = await provider.list_teams("PL")
    assert [t.name for t in teams] == ["Arsenal FC", "Chelsea FC"]


async def test_schema_change_field_rename_is_absorbed_by_aliases() -> None:
    # Vendor renamed "crest" -> "crestUrl"; the alias chain absorbs it.
    payload = {"teams": [{"id": 57, "name": "Arsenal", "crestUrl": "http://x.png"}]}
    provider = _build(FootballProvider, _router({"/PL/teams": payload}))
    assert (await provider.list_teams("PL"))[0].logo_url == "http://x.png"


async def test_envelope_shape_change_is_tolerated() -> None:
    # Vendor returns a bare list instead of {"teams": [...]}.
    provider = _build(FootballProvider, _router({"/PL/teams": [{"id": 57, "name": "Arsenal"}]}))
    assert len(await provider.list_teams("PL")) == 1


async def test_malformed_json_raises_malformed_response_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(MalformedResponseError):
        await _build(FootballProvider, handler).list_competitions("football")


async def test_auth_failure_maps_to_provider_authentication_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "bad token"})

    with pytest.raises(ProviderAuthenticationError):
        await _build(FootballProvider, handler).list_competitions("football")


async def test_rate_limit_is_retried_then_surfaces() -> None:
    attempts = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "0"}, json={})

    with pytest.raises(RateLimitError):
        await _build(FootballProvider, handler, max_attempts=3).list_competitions("football")
    assert attempts["n"] == 3  # retried before giving up


async def test_server_error_retries_then_provider_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    with pytest.raises(ProviderUnavailableError):
        await _build(FootballProvider, handler).list_competitions("football")


async def test_unconfigured_provider_refuses_network_calls() -> None:
    config = ProviderConfig(key="f", name="F", base_url="https://x", api_key=None)
    http = ResilientHttpClient(client=httpx.AsyncClient())
    provider = FootballProvider(config, http)
    assert provider.provider_info().configured is False
    with pytest.raises(ProviderAuthenticationError):
        await provider.list_competitions("football")


async def test_api_key_is_sent_in_the_configured_header() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"competitions": []})

    config = ProviderConfig(
        key="football-api",
        name="F",
        base_url="https://api.test",
        api_key="secret-key",
        auth_header="X-Auth-Token",
    )
    http = ResilientHttpClient(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await FootballProvider(config, http).list_competitions("football")
    assert seen["x-auth-token"] == "secret-key"
    assert "gzip" in seen["accept-encoding"]


# --------------------------------------------------------------------------
# Capabilities & circuit breaker
# --------------------------------------------------------------------------
def test_capabilities_differ_per_provider() -> None:
    assert ProviderCapability.STANDINGS in FootballProvider.capabilities
    assert ProviderCapability.STANDINGS not in BasketballProvider.capabilities
    assert ProviderCapability.BRACKETS in ValorantProvider.capabilities
    assert ProviderCapability.VENUES not in ValorantProvider.capabilities


async def test_unsupported_capability_raises_before_any_network_call() -> None:
    def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not call the provider")

    with pytest.raises(CapabilityNotSupportedError):
        await _build(BasketballProvider, handler).get_standings("nba")


async def test_circuit_breaker_opens_after_repeated_failures() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={})

    breaker = CircuitBreaker("football-api", CircuitBreakerConfig(failure_threshold=2))
    provider = _build(FootballProvider, handler, max_attempts=1, breaker=breaker)

    for _ in range(2):
        with pytest.raises(ProviderUnavailableError):
            await provider.list_competitions("football")
    calls_before_open = calls["n"]

    # Circuit now open: fails fast without touching the network.
    with pytest.raises(ProviderUnavailableError):
        await provider.list_competitions("football")
    assert calls["n"] == calls_before_open
