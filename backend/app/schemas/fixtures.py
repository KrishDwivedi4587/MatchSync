"""Fixture ingestion API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.domain.value_objects.enums import (
    FixtureChangeType,
    FixtureStatus,
    ImportStatus,
)


# --- reads -----------------------------------------------------------------
class TeamRefOut(BaseModel):
    id: uuid.UUID
    name: str
    short_name: str | None = None
    logo_url: str | None = None


class FixtureOut(BaseModel):
    id: uuid.UUID
    competition_id: uuid.UUID
    competition_name: str | None = None
    provider_fixture_id: str
    identity_key: str
    scheduled_start: datetime
    scheduled_end: datetime | None = None
    status: FixtureStatus
    venue: str | None = None
    round: str | None = None
    stage: str | None = None
    version: int
    home_team: TeamRefOut | None = None
    away_team: TeamRefOut | None = None


class FixtureListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    fixtures: list[FixtureOut]


class FixtureVersionOut(BaseModel):
    version: int
    change_type: FixtureChangeType
    changed_fields: list[str]
    content_hash: str
    created_at: datetime
    import_run_id: uuid.UUID | None = None


class FixtureDetailOut(FixtureOut):
    versions: list[FixtureVersionOut] = Field(default_factory=list)


# --- imports ---------------------------------------------------------------
class ImportRequest(BaseModel):
    """Import fixtures for one sport, optionally scoped to competitions."""

    sport: str
    competitions: list[str] | None = None
    past_days: int | None = Field(default=None, ge=0, le=3650)
    future_days: int | None = Field(default=None, ge=1, le=3650)


class ProviderImportRequest(BaseModel):
    """Import every sport served by one provider."""

    provider: str
    past_days: int | None = Field(default=None, ge=0, le=3650)
    future_days: int | None = Field(default=None, ge=1, le=3650)


class ImportStatsOut(BaseModel):
    fetched: int
    invalid: int
    duplicates: int
    created: int
    updated: int
    unchanged: int
    skipped_out_of_window: int
    skipped_stale: int
    missing_marked: int
    deleted: int
    failed: int


class ImportIssueOut(BaseModel):
    code: str
    message: str
    severity: str
    external_id: str | None = None
    competition_id: str | None = None


class ImportReportOut(BaseModel):
    id: uuid.UUID
    provider_key: str
    sport_key: str | None = None
    status: ImportStatus
    duration_ms: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stats: ImportStatsOut
    errors: list[ImportIssueOut]
    warnings: list[ImportIssueOut]


class ImportRunSummaryOut(BaseModel):
    id: uuid.UUID
    provider_key: str
    sport_key: str | None = None
    status: ImportStatus
    duration_ms: int
    created_at: datetime
    finished_at: datetime | None = None
    fetched_count: int
    created_count: int
    updated_count: int
    unchanged_count: int
    skipped_count: int
    duplicate_count: int
    invalid_count: int
    failed_count: int
    deleted_count: int
    error_summary: str | None = None


class ImportStatusResponse(BaseModel):
    runs: list[ImportRunSummaryOut]


class ImportRunDetailOut(ImportRunSummaryOut):
    report: dict[str, Any] | None = None
