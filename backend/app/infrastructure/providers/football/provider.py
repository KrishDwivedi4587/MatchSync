"""Football provider — adapter for a football-data.org-style API (v4).

Auth: ``X-Auth-Token: <key>``.
Endpoints used:
    GET /competitions
    GET /competitions/{code}
    GET /competitions/{code}/teams
    GET /competitions/{code}/matches?dateFrom&dateTo
    GET /competitions/{code}/standings

> The JSON shapes below follow the vendor's documented v4 responses. Every field
> access goes through ``require``/``optional`` so a schema change degrades to a
> skipped record rather than a crash. Validate against the live API before
> production use.

All vendor vocabulary dies in this file.
"""

from __future__ import annotations

from typing import Any

from app.domain.ports.sports_provider import (
    Competition,
    Fixture,
    Participant,
    ParticipantSide,
    ProviderCapability,
    Season,
    Sport,
    Standing,
    Team,
)
from app.domain.sports.normalization import (
    normalize_country,
    normalize_datetime,
    normalize_external_id,
    normalize_name,
    normalize_optional_datetime,
    normalize_optional_name,
    normalize_status,
    normalize_venue,
    optional,
    require,
    season_label_from_dates,
)
from app.domain.value_objects.enums import CompetitionType, FixtureStatus, SportCategory
from app.domain.value_objects.time_window import TimeWindow
from app.exceptions.sports import CompetitionNotFoundError, TeamNotFoundError
from app.infrastructure.providers.base import BaseHttpSportsProvider
from app.infrastructure.providers.mapping import as_list, normalize_many

SPORT_KEY = "football"

# Vendor status vocabulary -> shared FixtureStatus. Unknown values fall back to
# SCHEDULED (see normalize_status) so a new vendor status never drops a fixture.
_STATUS_MAP: dict[str, FixtureStatus] = {
    "SCHEDULED": FixtureStatus.SCHEDULED,
    "TIMED": FixtureStatus.SCHEDULED,
    "IN_PLAY": FixtureStatus.LIVE,
    "PAUSED": FixtureStatus.LIVE,
    "FINISHED": FixtureStatus.FINISHED,
    "AWARDED": FixtureStatus.FINISHED,
    "POSTPONED": FixtureStatus.POSTPONED,
    "SUSPENDED": FixtureStatus.POSTPONED,
    "CANCELLED": FixtureStatus.CANCELLED,
    "CANCELED": FixtureStatus.CANCELLED,
}

_TYPE_MAP: dict[str, CompetitionType] = {
    "LEAGUE": CompetitionType.LEAGUE,
    "CUP": CompetitionType.CUP,
    "PLAYOFFS": CompetitionType.TOURNAMENT,
}


class FootballProvider(BaseHttpSportsProvider):
    key = "football-api"
    name = "Football Data"
    version = "v4"
    capabilities = frozenset(
        {
            ProviderCapability.LIVE_SCORES,
            ProviderCapability.STANDINGS,
            ProviderCapability.VENUES,
            ProviderCapability.SEASONS,
            ProviderCapability.TEAM_LOGOS,
        }
    )
    supported_sports: tuple[str, ...] = (SPORT_KEY,)

    # --- sports ------------------------------------------------------------
    async def list_sports(self) -> list[Sport]:
        return [
            Sport(
                key=SPORT_KEY, name="Football", category=SportCategory.TEAM, provider_key=self.key
            )
        ]

    # --- competitions ------------------------------------------------------
    def _to_competition(self, raw: dict[str, Any]) -> Competition:
        # The vendor's stable code ("PL") is preferred over the numeric id: it is
        # what every other endpoint accepts as a path parameter.
        code = optional(raw, "code") or require(raw, "id", context="competition")
        area = raw.get("area") or {}
        current = raw.get("currentSeason") or {}

        season = None
        label = season_label_from_dates(current.get("startDate"), current.get("endDate"))
        if label:
            season = Season(
                label=label,
                start=normalize_optional_datetime(current.get("startDate")),
                end=normalize_optional_datetime(current.get("endDate")),
                is_current=True,
            )

        return Competition(
            external_id=normalize_external_id(code, field="competition id"),
            name=normalize_name(require(raw, "name", context="competition")),
            sport_key=SPORT_KEY,
            type=_TYPE_MAP.get(str(optional(raw, "type") or "").upper(), CompetitionType.LEAGUE),
            country=normalize_country(area.get("name")),
            season=season,
            logo_url=optional(raw, "emblem", "logo"),
        )

    async def list_competitions(
        self, sport_key: str, *, season: str | None = None
    ) -> list[Competition]:
        payload = await self._get("/competitions")
        return normalize_many(
            as_list(payload, "competitions"),
            self._to_competition,
            provider=self.key,
            kind="competition",
        )

    async def get_competition(self, external_id: str) -> Competition:
        payload = await self._get(f"/competitions/{external_id}")
        if not isinstance(payload, dict) or not payload.get("name"):
            raise CompetitionNotFoundError()
        return self._to_competition(payload)

    # --- teams -------------------------------------------------------------
    def _to_team(self, raw: dict[str, Any]) -> Team:
        return Team(
            external_id=normalize_external_id(require(raw, "id", context="team")),
            name=normalize_name(require(raw, "name", context="team")),
            sport_key=SPORT_KEY,
            short_name=normalize_optional_name(optional(raw, "shortName", "tla")),
            country=normalize_country((raw.get("area") or {}).get("name")),
            logo_url=optional(raw, "crest", "crestUrl"),
        )

    async def list_teams(self, competition_id: str) -> list[Team]:
        payload = await self._get(f"/competitions/{competition_id}/teams")
        return normalize_many(
            as_list(payload, "teams"), self._to_team, provider=self.key, kind="team"
        )

    async def get_team(self, external_id: str) -> Team:
        payload = await self._get(f"/teams/{external_id}")
        if not isinstance(payload, dict) or not payload.get("name"):
            raise TeamNotFoundError()
        return self._to_team(payload)

    # --- fixtures (fetch + normalize only; no persistence, no sync) ---------
    def _to_fixture(self, competition_id: str, raw: dict[str, Any]) -> Fixture:
        home = raw.get("homeTeam") or {}
        away = raw.get("awayTeam") or {}
        participants = tuple(
            Participant(
                external_id=normalize_external_id(side.get("id"), field="participant id"),
                name=normalize_name(side.get("name"), field="participant name"),
                side=which,
            )
            for side, which in ((home, ParticipantSide.HOME), (away, ParticipantSide.AWAY))
            if side.get("id") is not None
        )

        return Fixture(
            external_id=normalize_external_id(require(raw, "id", context="match")),
            competition_id=competition_id,
            sport_key=SPORT_KEY,
            start=normalize_datetime(require(raw, "utcDate", context="match"), field="utcDate"),
            status=normalize_status(optional(raw, "status"), _STATUS_MAP),
            participants=participants,
            venue=normalize_venue(optional(raw, "venue")),
            round=str(raw["matchday"]) if raw.get("matchday") is not None else None,
            stage=normalize_optional_name(optional(raw, "stage")),
            provider_updated_at=normalize_optional_datetime(optional(raw, "lastUpdated")),
        )

    async def get_fixtures(self, competition_id: str, window: TimeWindow) -> list[Fixture]:
        payload = await self._get(
            f"/competitions/{competition_id}/matches",
            params={
                "dateFrom": window.start.date().isoformat(),
                "dateTo": window.end.date().isoformat(),
            },
        )
        return normalize_many(
            as_list(payload, "matches"),
            lambda raw: self._to_fixture(competition_id, raw),
            provider=self.key,
            kind="fixture",
        )

    # --- standings (capability-gated) --------------------------------------
    async def get_standings(self, competition_id: str) -> list[Standing]:
        self.require_capability(ProviderCapability.STANDINGS)
        payload = await self._get(f"/competitions/{competition_id}/standings")

        rows: list[dict[str, Any]] = []
        for table in as_list(payload, "standings"):
            rows.extend(as_list(table, "table"))

        def to_standing(raw: dict[str, Any]) -> Standing:
            team = raw.get("team") or {}
            return Standing(
                competition_id=competition_id,
                team_external_id=normalize_external_id(team.get("id"), field="team id"),
                position=int(require(raw, "position", context="standing")),
                played=int(optional(raw, "playedGames") or 0),
                won=int(optional(raw, "won") or 0),
                drawn=int(optional(raw, "draw") or 0),
                lost=int(optional(raw, "lost") or 0),
                points=int(optional(raw, "points") or 0),
                goal_difference=optional(raw, "goalDifference"),
            )

        return normalize_many(rows, to_standing, provider=self.key, kind="standing")
