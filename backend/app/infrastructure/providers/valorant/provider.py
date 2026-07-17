"""Valorant esports provider — adapter for a VCT-style esports API.

Auth: ``x-api-key: <key>``.
Endpoints used:
    GET /events
    GET /events/{id}
    GET /events/{id}/teams
    GET /matches?event={id}&from&to

> This targets a community/unofficial esports API. Its envelope (``{"data": …}``)
> and field names are the least stable of the three providers, so every access
> uses ``optional`` with aliases. Validate against the live API before use.

**Why this provider is interesting:** esports has no home/away and no venues.
Matches are bracket rounds between two rosters. The adapter therefore emits
participants with ``ParticipantSide.NEUTRAL`` and no venue — the same ``Fixture``
model, just populated differently. Nothing downstream branches on sport.
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
    optional,
    require,
    season_label_from_dates,
)
from app.domain.value_objects.enums import CompetitionType, FixtureStatus, SportCategory
from app.domain.value_objects.time_window import TimeWindow
from app.exceptions.sports import CompetitionNotFoundError, TeamNotFoundError
from app.infrastructure.providers.base import BaseHttpSportsProvider
from app.infrastructure.providers.mapping import as_list, normalize_many

SPORT_KEY = "valorant"

_STATUS_MAP: dict[str, FixtureStatus] = {
    "UPCOMING": FixtureStatus.SCHEDULED,
    "SCHEDULED": FixtureStatus.SCHEDULED,
    "LIVE": FixtureStatus.LIVE,
    "ONGOING": FixtureStatus.LIVE,
    "COMPLETED": FixtureStatus.FINISHED,
    "FINISHED": FixtureStatus.FINISHED,
    "POSTPONED": FixtureStatus.POSTPONED,
    "CANCELLED": FixtureStatus.CANCELLED,
    "CANCELED": FixtureStatus.CANCELLED,
}


class ValorantProvider(BaseHttpSportsProvider):
    key = "valorant"
    name = "Valorant Esports"
    version = "v1"
    capabilities = frozenset(
        {
            ProviderCapability.LIVE_SCORES,
            ProviderCapability.TOURNAMENTS,
            ProviderCapability.BRACKETS,
            ProviderCapability.TEAM_LOGOS,
        }
    )  # no VENUES (online), no STANDINGS (bracket format)
    supported_sports: tuple[str, ...] = (SPORT_KEY,)

    async def list_sports(self) -> list[Sport]:
        return [
            Sport(
                key=SPORT_KEY,
                name="Valorant",
                category=SportCategory.ESPORTS,
                provider_key=self.key,
            )
        ]

    # --- competitions (events/tournaments) ---------------------------------
    def _to_competition(self, raw: dict[str, Any]) -> Competition:
        dates = raw.get("dates") or {}
        label = season_label_from_dates(dates.get("start"), dates.get("end"))
        season = (
            Season(
                label=label,
                start=normalize_optional_datetime(dates.get("start")),
                end=normalize_optional_datetime(dates.get("end")),
                is_current=str(optional(raw, "status") or "").lower() == "ongoing",
            )
            if label
            else None
        )

        return Competition(
            external_id=normalize_external_id(require(raw, "id", context="event")),
            name=normalize_name(optional(raw, "name", "title"), field="event name"),
            sport_key=SPORT_KEY,
            # Every esports event is a bracketed tournament, not a league.
            type=CompetitionType.TOURNAMENT,
            country=normalize_country(optional(raw, "region", "country")),
            season=season,
            logo_url=optional(raw, "img", "logo", "image"),
        )

    async def list_competitions(
        self, sport_key: str, *, season: str | None = None
    ) -> list[Competition]:
        payload = await self._get("/events")
        return normalize_many(
            as_list(payload, "data", "events"),
            self._to_competition,
            provider=self.key,
            kind="competition",
        )

    async def get_competition(self, external_id: str) -> Competition:
        payload = await self._get(f"/events/{external_id}")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise CompetitionNotFoundError()
        return self._to_competition(data)

    # --- teams -------------------------------------------------------------
    def _to_team(self, raw: dict[str, Any]) -> Team:
        return Team(
            external_id=normalize_external_id(require(raw, "id", context="team")),
            name=normalize_name(optional(raw, "name", "title"), field="team name"),
            sport_key=SPORT_KEY,
            short_name=normalize_optional_name(optional(raw, "tag", "abbreviation")),
            country=normalize_country(optional(raw, "country", "region")),
            logo_url=optional(raw, "logo", "img"),
        )

    async def list_teams(self, competition_id: str) -> list[Team]:
        payload = await self._get(f"/events/{competition_id}/teams")
        return normalize_many(
            as_list(payload, "data", "teams"), self._to_team, provider=self.key, kind="team"
        )

    async def get_team(self, external_id: str) -> Team:
        payload = await self._get(f"/teams/{external_id}")
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise TeamNotFoundError()
        return self._to_team(data)

    # --- fixtures ----------------------------------------------------------
    def _to_fixture(self, competition_id: str, raw: dict[str, Any]) -> Fixture:
        # Esports matches have no home/away — both rosters are NEUTRAL.
        rosters = as_list(raw.get("teams"), "teams")
        participants = tuple(
            Participant(
                external_id=normalize_external_id(side.get("id"), field="participant id"),
                name=normalize_name(
                    side.get("name") or side.get("title"), field="participant name"
                ),
                side=ParticipantSide.NEUTRAL,
            )
            for side in rosters
            if isinstance(side, dict) and side.get("id") is not None
        )

        return Fixture(
            external_id=normalize_external_id(require(raw, "id", context="match")),
            competition_id=competition_id,
            sport_key=SPORT_KEY,
            start=normalize_datetime(
                optional(raw, "utc", "scheduled_at", "date"), field="match start"
            ),
            status=normalize_status(optional(raw, "status"), _STATUS_MAP),
            participants=participants,
            venue=None,  # online tournaments have no venue
            round=normalize_optional_name(optional(raw, "round")),
            stage=normalize_optional_name(optional(raw, "stage", "bracket")),
            provider_updated_at=normalize_optional_datetime(optional(raw, "updated_at")),
        )

    async def get_fixtures(self, competition_id: str, window: TimeWindow) -> list[Fixture]:
        payload = await self._get(
            "/matches",
            params={
                "event": competition_id,
                "from": window.start.date().isoformat(),
                "to": window.end.date().isoformat(),
            },
        )
        return normalize_many(
            as_list(payload, "data", "matches"),
            lambda raw: self._to_fixture(competition_id, raw),
            provider=self.key,
            kind="fixture",
        )
