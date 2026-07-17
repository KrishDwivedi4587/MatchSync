"""Synchronization endpoints.

    POST /sync              -> sync one subscription (or all of the user's)
    POST /sync/user         -> sync every active subscription of the user
    POST /sync/calendar     -> sync every subscription targeting one calendar
    GET  /sync/plan         -> preview the plan (zero writes, zero API calls)
    GET  /sync/status       -> per-subscription schedule + last run
    GET  /sync/history      -> recent runs
    GET  /sync/report/{id}  -> one run + its operations
    GET  /sync/metrics      -> aggregate metrics

Every mutation is user-scoped: a subscription is only reachable by its owner.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.v1.deps import (
    CurrentUser,
    get_sync_history_repository,
    get_sync_service,
    get_sync_subscription_repository,
)
from app.application.services.sync_service import (
    SubscriptionNotFoundError,
    SyncReport,
    SyncService,
)
from app.domain.sync.models import SyncMode, SyncPlan
from app.domain.value_objects.enums import SyncTrigger
from app.persistence.models.sync import SyncHistory
from app.persistence.repositories.sync import SyncRepository
from app.persistence.repositories.sync_engine import SyncSubscriptionRepository
from app.schemas.sync import (
    CalendarSyncRequest,
    PlanStatsOut,
    SubscriptionStatusOut,
    SyncActionOut,
    SyncBatchResponse,
    SyncHistoryResponse,
    SyncMetricsResponse,
    SyncPlanOut,
    SyncReportOut,
    SyncRequest,
    SyncRunDetailOut,
    SyncRunOut,
    SyncStatusResponse,
)

router = APIRouter(prefix="/sync", tags=["sync"])

Engine = Annotated[SyncService, Depends(get_sync_service)]
History = Annotated[SyncRepository, Depends(get_sync_history_repository)]
Subs = Annotated[SyncSubscriptionRepository, Depends(get_sync_subscription_repository)]


class SyncRunNotFoundError(SubscriptionNotFoundError):
    code = "sync_run_not_found"
    message = "The synchronization run does not exist."


def _plan_out(plan: SyncPlan) -> SyncPlanOut:
    return SyncPlanOut(
        subscription_id=plan.subscription_id,
        mode=plan.mode,
        is_empty=plan.is_empty,
        stats=PlanStatsOut(**plan.stats.as_dict()),
        actions=[
            SyncActionOut(
                type=a.type,
                identity_key=a.identity_key,
                reason=a.reason,
                fixture_id=a.fixture_id,
                external_event_id=a.external_event_id,
                changed_fields=list(a.changed_fields),
            )
            for a in plan.actions
        ],
    )


def _report_out(report: SyncReport) -> SyncReportOut:
    return SyncReportOut(
        run_id=report.run_id,
        subscription_id=report.subscription_id,
        mode=report.plan.mode,
        status=report.status,
        plan=PlanStatsOut(**report.plan.stats.as_dict()),
        created=report.created,
        updated=report.updated,
        deleted=report.deleted,
        skipped=report.skipped,
        failed=report.failed,
        duplicates_prevented=report.duplicates_prevented,
        api_calls=report.api_calls,
        plan_ms=report.plan_ms,
        execute_ms=report.execute_ms,
        total_ms=report.total_ms,
        error_summary=report.error_summary,
    )


def _run_out(run: SyncHistory) -> SyncRunOut:
    return SyncRunOut.model_validate(run, from_attributes=True)


# --- mutations ---------------------------------------------------------------
@router.post("", response_model=SyncBatchResponse, summary="Synchronize")
async def synchronize(payload: SyncRequest, user: CurrentUser, engine: Engine) -> SyncBatchResponse:
    if payload.subscription_id:
        report = await engine.synchronize(
            user, payload.subscription_id, mode=payload.mode, trigger=SyncTrigger.MANUAL
        )
        return SyncBatchResponse(reports=[_report_out(report)])
    reports = await engine.synchronize_user(user, mode=payload.mode)
    return SyncBatchResponse(reports=[_report_out(r) for r in reports])


@router.post("/user", response_model=SyncBatchResponse, summary="Synchronize all subscriptions")
async def synchronize_user(
    user: CurrentUser,
    engine: Engine,
    mode: Annotated[SyncMode, Query()] = SyncMode.INCREMENTAL,
) -> SyncBatchResponse:
    reports = await engine.synchronize_user(user, mode=mode)
    return SyncBatchResponse(reports=[_report_out(r) for r in reports])


@router.post("/calendar", response_model=SyncBatchResponse, summary="Synchronize one calendar")
async def synchronize_calendar(
    payload: CalendarSyncRequest, user: CurrentUser, engine: Engine
) -> SyncBatchResponse:
    reports = await engine.synchronize_calendar(user, payload.calendar_id, mode=payload.mode)
    return SyncBatchResponse(reports=[_report_out(r) for r in reports])


# --- reads --------------------------------------------------------------------
@router.get("/plan", response_model=SyncPlanOut, summary="Preview the plan (no writes)")
async def preview_plan(
    user: CurrentUser,
    engine: Engine,
    subscription_id: Annotated[uuid.UUID, Query()],
    mode: Annotated[SyncMode, Query()] = SyncMode.INCREMENTAL,
) -> SyncPlanOut:
    plan = await engine.build_plan(user, subscription_id, mode=mode)
    return _plan_out(plan)


@router.get("/status", response_model=SyncStatusResponse, summary="Per-subscription status")
async def sync_status(user: CurrentUser, subs: Subs, history: History) -> SyncStatusResponse:
    subscriptions = await subs.list_active_for_user(user.id)
    rows: list[SubscriptionStatusOut] = []
    for subscription in subscriptions:
        runs = await history.list_for_subscription(subscription.id, limit=1)
        rows.append(
            SubscriptionStatusOut(
                subscription_id=subscription.id,
                status=subscription.status,
                sync_frequency_minutes=subscription.sync_frequency_minutes,
                last_synced_at=subscription.last_synced_at,
                next_sync_at=subscription.next_sync_at,
                last_run=_run_out(runs[0]) if runs else None,
            )
        )
    return SyncStatusResponse(subscriptions=rows)


@router.get("/history", response_model=SyncHistoryResponse, summary="Recent runs")
async def sync_history(
    user: CurrentUser,
    subs: Subs,
    history: History,
    subscription_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SyncHistoryResponse:
    if subscription_id:
        owned = await subs.get_for_user(subscription_id, user.id)
        if owned is None:
            raise SubscriptionNotFoundError()
        runs = list(await history.list_for_subscription(subscription_id, limit=limit))
    else:
        runs = []
        for subscription in await subs.list_active_for_user(user.id):
            runs.extend(await history.list_for_subscription(subscription.id, limit=limit))
        runs.sort(key=lambda r: r.created_at, reverse=True)
        runs = runs[:limit]
    return SyncHistoryResponse(runs=[_run_out(r) for r in runs])


@router.get("/report/{run_id}", response_model=SyncRunDetailOut, summary="One run + operations")
async def sync_report(
    run_id: uuid.UUID, user: CurrentUser, subs: Subs, history: History
) -> SyncRunDetailOut:
    run = await history.get_with_operations(run_id)
    if run is None:
        raise SyncRunNotFoundError()
    # Ownership: the run must belong to one of the caller's subscriptions.
    if await subs.get_for_user(run.subscription_id, user.id) is None:
        raise SyncRunNotFoundError()
    return SyncRunDetailOut.model_validate(run, from_attributes=True)


@router.get("/metrics", response_model=SyncMetricsResponse, summary="Aggregate metrics")
async def sync_metrics(user: CurrentUser, engine: Engine) -> SyncMetricsResponse:
    return SyncMetricsResponse(metrics=await engine.metrics(user))
