"""Sports API schemas (public request/response DTOs)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.domain.ports.sports_provider import SearchEntityType
from app.domain.value_objects.enums import CompetitionType, SportCategory


class SportOut(BaseModel):
    key: str
    name: str
    category: SportCategory
    provider_key: str


class SeasonOut(BaseModel):
    label: str
    start: datetime | None = None
    end: datetime | None = None
    is_current: bool = False


class CompetitionOut(BaseModel):
    external_id: str
    name: str
    sport_key: str
    type: CompetitionType
    country: str | None = None
    season: SeasonOut | None = None
    logo_url: str | None = None


class TeamOut(BaseModel):
    external_id: str
    name: str
    sport_key: str
    short_name: str | None = None
    country: str | None = None
    logo_url: str | None = None


class ProviderInfoOut(BaseModel):
    key: str
    name: str
    version: str
    capabilities: list[str]
    supported_sports: list[str]
    configured: bool


class SearchHitOut(BaseModel):
    type: SearchEntityType
    id: str
    name: str
    sport_key: str | None = None
    subtitle: str | None = None
    logo_url: str | None = None


class SearchResultsOut(BaseModel):
    query: str
    total: int
    hits: list[SearchHitOut]


class ProviderRefreshReportOut(BaseModel):
    provider_key: str
    success: bool
    sports: int
    competitions: int
    teams: int
    errors: list[str]


class MetadataRefreshReportOut(BaseModel):
    ok: bool
    providers: list[ProviderRefreshReportOut]
