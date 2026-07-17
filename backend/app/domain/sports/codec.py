"""Serialization for cached domain models.

Only metadata types (Sport, Competition, Team) are cached, so only they need a
codec. Fixtures are never cached — they are volatile and belong to Stage 7.

Kept in the domain layer because it defines the canonical wire shape of the
models; the cache backend stores opaque strings and knows none of this.
"""

from __future__ import annotations

from typing import Any

from app.domain.ports.sports_provider import Competition, Season, Sport, Team
from app.domain.sports.normalization import normalize_optional_datetime
from app.domain.value_objects.enums import CompetitionType, SportCategory


def sport_to_dict(sport: Sport) -> dict[str, Any]:
    return {
        "key": sport.key,
        "name": sport.name,
        "category": sport.category.value,
        "provider_key": sport.provider_key,
    }


def sport_from_dict(data: dict[str, Any]) -> Sport:
    return Sport(
        key=data["key"],
        name=data["name"],
        category=SportCategory(data["category"]),
        provider_key=data["provider_key"],
    )


def _season_to_dict(season: Season | None) -> dict[str, Any] | None:
    if season is None:
        return None
    return {
        "label": season.label,
        "start": season.start.isoformat() if season.start else None,
        "end": season.end.isoformat() if season.end else None,
        "is_current": season.is_current,
    }


def _season_from_dict(data: dict[str, Any] | None) -> Season | None:
    if not data:
        return None
    return Season(
        label=data["label"],
        start=normalize_optional_datetime(data.get("start")),
        end=normalize_optional_datetime(data.get("end")),
        is_current=bool(data.get("is_current", False)),
    )


def competition_to_dict(competition: Competition) -> dict[str, Any]:
    return {
        "external_id": competition.external_id,
        "name": competition.name,
        "sport_key": competition.sport_key,
        "type": competition.type.value,
        "country": competition.country,
        "season": _season_to_dict(competition.season),
        "logo_url": competition.logo_url,
    }


def competition_from_dict(data: dict[str, Any]) -> Competition:
    return Competition(
        external_id=data["external_id"],
        name=data["name"],
        sport_key=data["sport_key"],
        type=CompetitionType(data["type"]),
        country=data.get("country"),
        season=_season_from_dict(data.get("season")),
        logo_url=data.get("logo_url"),
    )


def team_to_dict(team: Team) -> dict[str, Any]:
    return {
        "external_id": team.external_id,
        "name": team.name,
        "sport_key": team.sport_key,
        "short_name": team.short_name,
        "country": team.country,
        "logo_url": team.logo_url,
    }


def team_from_dict(data: dict[str, Any]) -> Team:
    return Team(
        external_id=data["external_id"],
        name=data["name"],
        sport_key=data["sport_key"],
        short_name=data.get("short_name"),
        country=data.get("country"),
        logo_url=data.get("logo_url"),
    )
