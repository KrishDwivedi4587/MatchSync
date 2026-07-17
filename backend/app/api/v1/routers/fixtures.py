"""Fixture ingestion and read endpoints.

    POST /fixtures/import              -> import one sport (optionally scoped)
    POST /fixtures/import/provider     -> import every sport of one provider
    GET  /fixtures/import/status       -> recent import runs
    GET  /fixtures/import/report/{id}  -> full report for one run
    GET  /fixtures                     -> browse persisted fixtures
    GET  /fixtures/{id}                -> one fixture + its version history

No synchronization endpoints. Nothing here touches Google Calendar.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.v1.deps import (
    CurrentUser,
    get_fixture_ingestion_service,
    get_fixture_query_service,
)
from app.application.services.fixture_ingestion_service import FixtureIngestionService
from app.application.services.fixture_query_service import FixtureQueryService
from app.domain.fixtures.report import ImportReport
from app.domain.value_objects.enums import FixtureStatus
from app.domain.value_objects.time_window import TimeWindow
from app.persistence.models.fixture import Fixture
from app.persistence.models.ingestion import ImportRun
from app.schemas.fixtures import (
    FixtureDetailOut,
    FixtureListResponse,
    FixtureOut,
    FixtureVersionOut,
    ImportIssueOut,
    ImportReportOut,
    ImportRequest,
    ImportRunDetailOut,
    ImportRunSummaryOut,
    ImportStatsOut,
    ImportStatusResponse,
    ProviderImportRequest,
    TeamRefOut,
)

router = APIRouter(prefix="/fixtures", tags=["fixtures"])

Ingestion = Annotated[FixtureIngestionService, Depends(get_fixture_ingestion_service)]
Queries = Annotated[FixtureQueryService, Depends(get_fixture_query_service)]


def _window(
    service: FixtureIngestionService, past_days: int | None, future_days: int | None
) -> TimeWindow | None:
    if past_days is None and future_days is None:
        return None
    default = service.default_window()
    now = datetime.now(UTC)
    start = now - timedelta(days=past_days) if past_days is not None else default.start
    end = now + timedelta(days=future_days) if future_days is not None else default.end
    return TimeWindow(start=start, end=end)


def _team_out(team) -> TeamRefOut | None:
    if team is None:
        return None
    return TeamRefOut(
        id=team.id, name=team.name, short_name=team.short_name, logo_url=team.logo_url
    )


def _fixture_out(fixture: Fixture) -> FixtureOut:
    return FixtureOut(
        id=fixture.id,
        competition_id=fixture.competition_id,
        competition_name=fixture.competition.name if fixture.competition else None,
        provider_fixture_id=fixture.provider_fixture_id,
        identity_key=fixture.identity_key,
        scheduled_start=fixture.scheduled_start,
        scheduled_end=fixture.scheduled_end,
        status=fixture.status,
        venue=fixture.venue,
        round=fixture.round,
        stage=fixture.stage,
        version=fixture.version,
        home_team=_team_out(fixture.home_team),
        away_team=_team_out(fixture.away_team),
    )


def _report_out(report: ImportReport) -> ImportReportOut:
    return ImportReportOut(
        id=report.id,
        provider_key=report.provider_key,
        sport_key=report.sport_key,
        status=report.status,
        duration_ms=report.duration_ms,
        started_at=report.started_at,
        finished_at=report.finished_at,
        stats=ImportStatsOut(**report.stats.as_dict()),
        errors=[ImportIssueOut(**i.as_dict()) for i in report.errors],
        warnings=[ImportIssueOut(**i.as_dict()) for i in report.warnings],
    )


def _run_summary(run: ImportRun) -> ImportRunSummaryOut:
    return ImportRunSummaryOut.model_validate(run, from_attributes=True)


# --- imports ---------------------------------------------------------------
@router.post("/import", response_model=ImportReportOut, summary="Import fixtures for a sport")
async def import_fixtures(
    payload: ImportRequest, user: CurrentUser, service: Ingestion
) -> ImportReportOut:
    report = await service.import_sport(
        payload.sport,
        window=_window(service, payload.past_days, payload.future_days),
        competition_ids=payload.competitions,
    )
    return _report_out(report)


@router.post(
    "/import/provider",
    response_model=ImportReportOut,
    summary="Import every sport served by a provider",
)
async def import_provider(
    payload: ProviderImportRequest, user: CurrentUser, service: Ingestion
) -> ImportReportOut:
    report = await service.import_provider(
        payload.provider, window=_window(service, payload.past_days, payload.future_days)
    )
    return _report_out(report)


@router.get("/import/status", response_model=ImportStatusResponse, summary="Recent import runs")
async def import_status(
    user: CurrentUser,
    queries: Queries,
    provider: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ImportStatusResponse:
    runs = await queries.list_import_runs(provider_key=provider, limit=limit)
    return ImportStatusResponse(runs=[_run_summary(r) for r in runs])


@router.get(
    "/import/report/{run_id}",
    response_model=ImportRunDetailOut,
    summary="Full report for one import run",
)
async def import_report(
    run_id: uuid.UUID, user: CurrentUser, queries: Queries
) -> ImportRunDetailOut:
    run = await queries.get_import_run(run_id)
    return ImportRunDetailOut.model_validate(run, from_attributes=True)


# --- reads ------------------------------------------------------------------
@router.get("", response_model=FixtureListResponse, summary="Browse persisted fixtures")
async def list_fixtures(
    user: CurrentUser,
    queries: Queries,
    sport: Annotated[str | None, Query()] = None,
    competition_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[FixtureStatus | None, Query()] = None,
    start_from: Annotated[datetime | None, Query()] = None,
    start_to: Annotated[datetime | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FixtureListResponse:
    fixtures, total = await queries.list_fixtures(
        sport_key=sport,
        competition_id=competition_id,
        status=status,
        start_from=start_from,
        start_to=start_to,
        query=q,
        limit=limit,
        offset=offset,
    )
    return FixtureListResponse(
        total=total,
        limit=limit,
        offset=offset,
        fixtures=[_fixture_out(f) for f in fixtures],
    )


@router.get("/{fixture_id}", response_model=FixtureDetailOut, summary="One fixture + history")
async def get_fixture(
    fixture_id: uuid.UUID, user: CurrentUser, queries: Queries
) -> FixtureDetailOut:
    fixture = await queries.get_fixture(fixture_id)
    versions = await queries.get_fixture_versions(fixture_id)
    return FixtureDetailOut(
        **_fixture_out(fixture).model_dump(),
        versions=[FixtureVersionOut.model_validate(v, from_attributes=True) for v in versions],
    )
