"""Basketball provider — adapter for a balldontlie-style API (v1).

Auth: ``Authorization: <key>`` (raw key, no scheme).
Endpoints used:
    GET /teams?per_page&cursor
    GET /games?start_date&end_date&per_page&cursor

> Shapes follow the vendor's documented v1 responses; validate against the live
> API before production use.

**Why this provider is interesting:** the vendor has *no competitions endpoint*
and *no standings*. Rather than leak that asymmetry upward, the adapter:

- synthesizes a single competition ("NBA") so ``list_competitions`` still
  returns a normalized ``Competition`` like every other provider, and
- simply omits ``STANDINGS`` from its capabilities, so callers never ask.

This is the whole point of the abstraction: the sync engine cannot tell that
this API is shaped differently.
"""

from __future__ import annotations

from typing import Any

from app.domain.ports.sports_provider import (
    Competition,
    Fixture,
    Participant,
    ParticipantSide,
    ProviderCapability,
    Sport,
    Team,
)
from app.domain.sports.normalization import (
    normalize_country,
    normalize_datetime,
    normalize_external_id,
    normalize_name,
    normalize_optional_name,
    normalize_status,
    optional,
    require,
)
from app.domain.value_objects.enums import CompetitionType, FixtureStatus, SportCategory
from app.domain.value_objects.time_window import TimeWindow
from app.exceptions.sports import CompetitionNotFoundError, TeamNotFoundError
from app.infrastructure.providers.base import BaseHttpSportsProvider
from app.infrastructure.providers.mapping import as_list, normalize_many

SPORT_KEY = "basketball"

# The synthetic competition this vendor implies but never names.
_NBA_ID = "nba"
_NBA_NAME = "NBA"

_STATUS_MAP: dict[str, FixtureStatus] = {
    "FINAL": FixtureStatus.FINISHED,
    "FINISHED": FixtureStatus.FINISHED,
    "IN PROGRESS": FixtureStatus.LIVE,
    "LIVE": FixtureStatus.LIVE,
    "POSTPONED": FixtureStatus.POSTPONED,
    "CANCELLED": FixtureStatus.CANCELLED,
    "CANCELED": FixtureStatus.CANCELLED,
}

_PAGE_SIZE = 100
_MAX_PAGES = 25  # safety valve against a pathological cursor loop


class BasketballProvider(BaseHttpSportsProvider):
    key = "basketball-api"
    name = "Basketball Data"
    version = "v1"
    capabilities = frozenset(
        {
            ProviderCapability.LIVE_SCORES,
            ProviderCapability.STATISTICS,
        }
    )  # deliberately no STANDINGS / VENUES / SEASONS
    supported_sports: tuple[str, ...] = (SPORT_KEY,)

    async def list_sports(self) -> list[Sport]:
        return [
            Sport(
                key=SPORT_KEY, name="Basketball", category=SportCategory.TEAM, provider_key=self.key
            )
        ]

    # --- cursor pagination (vendor-specific; hidden from callers) -----------
    async def _paginate(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: Any = None
        for _ in range(_MAX_PAGES):
            query = {**params, "per_page": _PAGE_SIZE}
            if cursor is not None:
                query["cursor"] = cursor
            payload = await self._get(path, params=query)
            items.extend(as_list(payload, "data"))
            cursor = (
                (payload.get("meta") or {}).get("next_cursor")
                if isinstance(payload, dict)
                else None
            )
            if not cursor:
                break
        return items

    # --- competitions (synthesized) ----------------------------------------
    @staticmethod
    def _nba() -> Competition:
        return Competition(
            external_id=_NBA_ID,
            name=_NBA_NAME,
            sport_key=SPORT_KEY,
            type=CompetitionType.LEAGUE,
            country="USA",
        )

    async def list_competitions(
        self, sport_key: str, *, season: str | None = None
    ) -> list[Competition]:
        return [self._nba()]

    async def get_competition(self, external_id: str) -> Competition:
        if external_id != _NBA_ID:
            raise CompetitionNotFoundError()
        return self._nba()

    # --- teams -------------------------------------------------------------
    def _to_team(self, raw: dict[str, Any]) -> Team:
        return Team(
            external_id=normalize_external_id(require(raw, "id", context="team")),
            name=normalize_name(optional(raw, "full_name", "name"), field="team name"),
            sport_key=SPORT_KEY,
            short_name=normalize_optional_name(optional(raw, "abbreviation")),
            country=normalize_country("USA"),
            logo_url=optional(raw, "logo"),  # vendor rarely supplies one
        )

    async def list_teams(self, competition_id: str) -> list[Team]:
        if competition_id != _NBA_ID:
            raise CompetitionNotFoundError()
        raw_teams = await self._paginate("/teams", {})
        return normalize_many(raw_teams, self._to_team, provider=self.key, kind="team")

    async def get_team(self, external_id: str) -> Team:
        payload = await self._get(f"/teams/{external_id}")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise TeamNotFoundError()
        return self._to_team(data)

    # --- fixtures ----------------------------------------------------------
    def _to_fixture(self, raw: dict[str, Any]) -> Fixture:
        home = raw.get("home_team") or {}
        away = raw.get("visitor_team") or {}  # vendor says "visitor", we say "away"
        participants = tuple(
            Participant(
                external_id=normalize_external_id(side.get("id"), field="participant id"),
                name=normalize_name(
                    side.get("full_name") or side.get("name"), field="participant name"
                ),
                side=which,
            )
            for side, which in ((home, ParticipantSide.HOME), (away, ParticipantSide.AWAY))
            if side.get("id") is not None
        )

        return Fixture(
            external_id=normalize_external_id(require(raw, "id", context="game")),
            competition_id=_NBA_ID,
            sport_key=SPORT_KEY,
            # Vendor sends a date-only or naive datetime -> normalized to UTC.
            start=normalize_datetime(optional(raw, "datetime", "date"), field="game datetime"),
            status=normalize_status(optional(raw, "status"), _STATUS_MAP),
            participants=participants,
            stage="Postseason" if raw.get("postseason") else None,
        )

    async def get_fixtures(self, competition_id: str, window: TimeWindow) -> list[Fixture]:
        if competition_id != _NBA_ID:
            raise CompetitionNotFoundError()
        raw_games = await self._paginate(
            "/games",
            {
                "start_date": window.start.date().isoformat(),
                "end_date": window.end.date().isoformat(),
            },
        )
        return normalize_many(raw_games, self._to_fixture, provider=self.key, kind="fixture")
