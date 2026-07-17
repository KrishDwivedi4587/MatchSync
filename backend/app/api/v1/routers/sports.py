"""Sports metadata endpoints.

    GET  /sports              -> sports served by registered providers
    GET  /competitions        -> competitions for a sport
    GET  /teams               -> teams in a competition
    GET  /providers           -> registered providers + configuration state
    GET  /capabilities        -> capability matrix
    GET  /search              -> provider-independent catalog search
    POST /metadata/refresh    -> refresh reference data (no fixtures)

No fixture endpoints and no synchronization endpoints — those belong to Stage 7.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.v1.deps import CurrentUser, get_sports_service
from app.application.services.sports_service import SportsService
from app.domain.ports.sports_provider import SearchEntityType
from app.schemas.sports import (
    CompetitionOut,
    MetadataRefreshReportOut,
    ProviderInfoOut,
    ProviderRefreshReportOut,
    SearchHitOut,
    SearchResultsOut,
    SportOut,
    TeamOut,
)

router = APIRouter(tags=["sports"])

Service = Annotated[SportsService, Depends(get_sports_service)]


@router.get("/sports", response_model=list[SportOut], summary="List available sports")
async def list_sports(user: CurrentUser, service: Service) -> list[SportOut]:
    return [SportOut(**s.__dict__) for s in await service.list_sports()]


@router.get(
    "/competitions", response_model=list[CompetitionOut], summary="List competitions for a sport"
)
async def list_competitions(
    user: CurrentUser,
    service: Service,
    sport: Annotated[str, Query(description="Sport key, e.g. 'football'")],
) -> list[CompetitionOut]:
    competitions = await service.list_competitions(sport)
    return [CompetitionOut.model_validate(c, from_attributes=True) for c in competitions]


@router.get("/teams", response_model=list[TeamOut], summary="List teams in a competition")
async def list_teams(
    user: CurrentUser,
    service: Service,
    sport: Annotated[str, Query(description="Sport key")],
    competition: Annotated[str, Query(description="Provider competition id")],
) -> list[TeamOut]:
    return [TeamOut(**t.__dict__) for t in await service.list_teams(sport, competition)]


@router.get("/providers", response_model=list[ProviderInfoOut], summary="List registered providers")
async def list_providers(user: CurrentUser, service: Service) -> list[ProviderInfoOut]:
    return [
        ProviderInfoOut(
            key=info.key,
            name=info.name,
            version=info.version,
            capabilities=sorted(c.value for c in info.capabilities),
            supported_sports=list(info.supported_sports),
            configured=info.configured,
        )
        for info in service.list_providers()
    ]


@router.get("/capabilities", response_model=dict[str, list[str]], summary="Capability matrix")
async def capabilities(user: CurrentUser, service: Service) -> dict[str, list[str]]:
    return service.capabilities()


@router.get("/search", response_model=SearchResultsOut, summary="Search the sports catalog")
async def search(
    user: CurrentUser,
    service: Service,
    q: Annotated[str, Query(min_length=1, max_length=100, description="Search term")],
    types: Annotated[list[SearchEntityType] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SearchResultsOut:
    results = await service.search(q, types=set(types) if types else None, limit=limit)
    return SearchResultsOut(
        query=results.query,
        total=results.total,
        hits=[SearchHitOut(**hit.__dict__) for hit in results.hits],
    )


@router.post(
    "/metadata/refresh",
    response_model=MetadataRefreshReportOut,
    summary="Refresh sports reference data (no fixtures)",
)
async def refresh_metadata(
    user: CurrentUser,
    service: Service,
    sport: Annotated[list[str] | None, Query(description="Limit to these sport keys")] = None,
) -> MetadataRefreshReportOut:
    report = await service.refresh_metadata(sport_keys=sport)
    return MetadataRefreshReportOut(
        ok=report.ok,
        providers=[
            ProviderRefreshReportOut(
                provider_key=p.provider_key,
                success=p.success,
                sports=p.sports,
                competitions=p.competitions,
                teams=p.teams,
                errors=list(p.errors),
            )
            for p in report.providers
        ],
    )
