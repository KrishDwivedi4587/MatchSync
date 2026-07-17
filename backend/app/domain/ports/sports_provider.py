"""Sports-provider port.

The contract every sports data source implements (Football, Valorant,
Basketball today; F1, Cricket, Tennis, NFL, MMA, Olympics later). Deliberately
the same shape as ``CalendarProvider`` from Stage 5: a Protocol, frozen
provider-agnostic dataclasses, declared capabilities, and a factory/registry.

Nothing here mentions a vendor, an HTTP path, or a JSON key. The future sync
engine consumes only these models and never learns which API produced them.

**Identity note:** ``external_id`` is the provider's *native* id (matching the
``provider_competition_id`` / ``provider_team_id`` columns frozen in Stage 3).
Because a sport maps to exactly one provider in the registry, native ids are
unambiguous within a sport. ``qualified_id()`` yields a globally unique form
when cross-provider uniqueness is needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.domain.value_objects.enums import (
    CompetitionType,
    FixtureStatus,
    SportCategory,
)
from app.domain.value_objects.time_window import TimeWindow


# --------------------------------------------------------------------------
# Capabilities
# --------------------------------------------------------------------------
class ProviderCapability(StrEnum):
    """What a provider can do beyond the mandatory core.

    The core (sports, competitions, teams, fixtures) is required of every
    provider. Everything below is optional and must be advertised. Callers ask
    ``registry.supports(...)`` before invoking an optional method, so a provider
    that lacks standings never has to raise from a half-implemented method.
    """

    LIVE_SCORES = "live_scores"
    STANDINGS = "standings"
    VENUES = "venues"
    LINEUPS = "lineups"
    STATISTICS = "statistics"
    BRACKETS = "brackets"
    TOURNAMENTS = "tournaments"
    TEAM_LOGOS = "team_logos"
    SEASONS = "seasons"
    REMOTE_SEARCH = "remote_search"


class ParticipantSide(StrEnum):
    """Which side of a two-sided fixture a participant occupies.

    ``NEUTRAL`` covers sports with no home/away (F1 grids, tennis draws, MMA
    cards), so the same Fixture model serves every sport.
    """

    HOME = "home"
    AWAY = "away"
    NEUTRAL = "neutral"


# --------------------------------------------------------------------------
# Domain models (provider-independent)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Sport:
    """A sport served by a provider. ``key`` is the stable catalog key."""

    key: str
    name: str
    category: SportCategory
    provider_key: str


@dataclass(frozen=True)
class Season:
    """A season or edition of a competition."""

    label: str  # normalized, e.g. "2025/26" or "2026"
    start: datetime | None = None
    end: datetime | None = None
    is_current: bool = False


@dataclass(frozen=True)
class Venue:
    """Where a fixture is played. Optional — many providers omit it."""

    name: str
    city: str | None = None
    country: str | None = None
    external_id: str | None = None


@dataclass(frozen=True)
class Team:
    """A club, national team, esports roster, or an individual competitor.

    Individual-sport athletes (an F1 driver, a tennis player) are modelled as a
    team of one, exactly as Stage 3's ``teams`` table intends.
    """

    external_id: str
    name: str
    sport_key: str
    short_name: str | None = None
    country: str | None = None
    logo_url: str | None = None


@dataclass(frozen=True)
class Competition:
    """A league, tournament, cup, or season-long event series."""

    external_id: str
    name: str
    sport_key: str
    type: CompetitionType = CompetitionType.LEAGUE
    country: str | None = None
    season: Season | None = None
    logo_url: str | None = None


@dataclass(frozen=True)
class Tournament:
    """A bracketed competition's stage structure (BRACKETS capability)."""

    external_id: str
    name: str
    competition_id: str
    stages: tuple[str, ...] = ()


@dataclass(frozen=True)
class Participant:
    """A competitor in a fixture, plus the side it occupies."""

    external_id: str
    name: str
    side: ParticipantSide = ParticipantSide.NEUTRAL
    team: Team | None = None


@dataclass(frozen=True)
class Fixture:
    """A normalized match/event. The unit Stage 7's import pipeline consumes.

    Times are always timezone-aware UTC. ``status`` is the shared
    ``FixtureStatus`` enum, so no provider vocabulary survives here.
    """

    external_id: str
    competition_id: str
    sport_key: str
    start: datetime
    status: FixtureStatus
    participants: tuple[Participant, ...] = ()
    end: datetime | None = None
    venue: Venue | None = None
    round: str | None = None
    stage: str | None = None
    provider_updated_at: datetime | None = None

    @property
    def home(self) -> Participant | None:
        return next((p for p in self.participants if p.side is ParticipantSide.HOME), None)

    @property
    def away(self) -> Participant | None:
        return next((p for p in self.participants if p.side is ParticipantSide.AWAY), None)


@dataclass(frozen=True)
class Standing:
    """A row of a league table (STANDINGS capability)."""

    competition_id: str
    team_external_id: str
    position: int
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    points: int = 0
    goal_difference: int | None = None


@dataclass(frozen=True)
class ProviderInfo:
    """Self-description of a registered provider."""

    key: str
    name: str
    version: str
    capabilities: frozenset[ProviderCapability]
    supported_sports: tuple[str, ...]
    configured: bool = True


# --------------------------------------------------------------------------
# Search (provider-independent results)
# --------------------------------------------------------------------------
class SearchEntityType(StrEnum):
    SPORT = "sport"
    COMPETITION = "competition"
    TEAM = "team"
    TOURNAMENT = "tournament"


@dataclass(frozen=True)
class SearchHit:
    type: SearchEntityType
    id: str
    name: str
    sport_key: str | None = None
    subtitle: str | None = None
    logo_url: str | None = None


@dataclass(frozen=True)
class SearchResults:
    query: str
    hits: tuple[SearchHit, ...] = ()
    total: int = 0


# --------------------------------------------------------------------------
# Metadata refresh reporting
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ProviderRefreshReport:
    provider_key: str
    success: bool
    sports: int = 0
    competitions: int = 0
    teams: int = 0
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetadataRefreshReport:
    providers: tuple[ProviderRefreshReport, ...] = ()

    @property
    def ok(self) -> bool:
        return all(p.success for p in self.providers)


# --------------------------------------------------------------------------
# The port
# --------------------------------------------------------------------------
class SportsProvider(Protocol):
    """A sports data source. One provider may serve several sports."""

    key: str
    name: str
    version: str
    capabilities: frozenset[ProviderCapability]

    def provider_info(self) -> ProviderInfo: ...

    # --- core (mandatory for every provider) -------------------------------
    async def list_sports(self) -> list[Sport]: ...
    async def list_competitions(
        self, sport_key: str, *, season: str | None = None
    ) -> list[Competition]: ...
    async def get_competition(self, external_id: str) -> Competition: ...
    async def list_teams(self, competition_id: str) -> list[Team]: ...
    async def get_team(self, external_id: str) -> Team: ...

    # Fixtures are *fetched and normalized* here. Persisting, comparing, and
    # syncing them belongs to Stage 7 — this port only supplies them.
    async def get_fixtures(self, competition_id: str, window: TimeWindow) -> list[Fixture]: ...

    # --- optional (guarded by capabilities) --------------------------------
    async def get_standings(self, competition_id: str) -> list[Standing]: ...


@dataclass(frozen=True)
class ProviderConfig:
    """Everything a provider needs to talk to its API. No secrets are logged."""

    key: str
    name: str
    base_url: str
    api_key: str | None = None
    auth_header: str = "Authorization"
    auth_scheme: str = ""  # e.g. "Bearer "; empty means raw key
    timeout_seconds: float = 10.0
    max_attempts: int = 4
    cache_ttl_seconds: int = 3600
    extra_headers: dict[str, str] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return bool(self.base_url) and bool(self.api_key)


def qualified_id(provider_key: str, external_id: str) -> str:
    """Globally-unique id across providers: ``"football-api:2021"``."""
    return f"{provider_key}:{external_id}"
