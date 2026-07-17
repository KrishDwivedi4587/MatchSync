"""Synchronization API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.domain.sync.models import SyncActionType, SyncMode
from app.domain.value_objects.enums import (
    OperationStatus,
    OperationType,
    SubscriptionStatus,
    SyncStatus,
    SyncTrigger,
)


class SyncRequest(BaseModel):
    subscription_id: uuid.UUID | None = None
    mode: SyncMode = SyncMode.INCREMENTAL


class CalendarSyncRequest(BaseModel):
    calendar_id: uuid.UUID
    mode: SyncMode = SyncMode.INCREMENTAL


class PlanStatsOut(BaseModel):
    create: int
    recreate: int
    update: int
    cancel: int
    delete: int
    reconcile: int
    conflict: int
    no_op: int
    total: int
    mutations: int
    no_op_ratio: float


class SyncActionOut(BaseModel):
    type: SyncActionType
    identity_key: str
    reason: str
    fixture_id: uuid.UUID | None = None
    external_event_id: str | None = None
    changed_fields: list[str] = []


class SyncPlanOut(BaseModel):
    subscription_id: uuid.UUID
    mode: SyncMode
    is_empty: bool
    stats: PlanStatsOut
    actions: list[SyncActionOut]


class SyncReportOut(BaseModel):
    run_id: uuid.UUID | None = None
    subscription_id: uuid.UUID
    mode: SyncMode
    status: SyncStatus
    plan: PlanStatsOut
    created: int
    updated: int
    deleted: int
    skipped: int
    failed: int
    duplicates_prevented: int
    api_calls: int
    plan_ms: int
    execute_ms: int
    total_ms: int
    error_summary: str | None = None


class SyncBatchResponse(BaseModel):
    reports: list[SyncReportOut]


class SyncOperationOut(BaseModel):
    operation_type: OperationType
    status: OperationStatus
    fixture_id: uuid.UUID | None = None
    calendar_event_id: uuid.UUID | None = None
    message: str | None = None
    created_at: datetime


class SyncRunOut(BaseModel):
    id: uuid.UUID
    subscription_id: uuid.UUID
    trigger: SyncTrigger
    status: SyncStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_count: int
    updated_count: int
    deleted_count: int
    skipped_count: int
    failed_count: int
    error_summary: str | None = None


class SyncRunDetailOut(SyncRunOut):
    operations: list[SyncOperationOut] = []


class SyncHistoryResponse(BaseModel):
    runs: list[SyncRunOut]


class SubscriptionStatusOut(BaseModel):
    subscription_id: uuid.UUID
    status: SubscriptionStatus
    sync_frequency_minutes: int
    last_synced_at: datetime | None = None
    next_sync_at: datetime | None = None
    last_run: SyncRunOut | None = None


class SyncStatusResponse(BaseModel):
    subscriptions: list[SubscriptionStatusOut]


class SyncMetricsResponse(BaseModel):
    metrics: dict[str, Any]
