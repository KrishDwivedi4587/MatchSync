"""The synchronization engine.

    Persisted fixtures -> Planner -> Diff -> Plan -> CalendarService -> Results
                                                                          |
                                                     sync_history + sync_operations

It begins at a persisted fixture and ends at ``CalendarService``. It never
touches a sports provider, never touches Google directly, and never re-derives
fixture data.

Guarantees (see docs/sync.md for the formal statement):
- **Idempotent** — re-running on unchanged fixtures yields an empty plan, and an
  empty plan performs zero calendar calls and zero writes to ``calendar_events``,
  ``sync_operations`` or ``sync_history``.
- **Deterministic** — the plan is a pure function of the loaded state.
- **Duplicate-proof** — enforced structurally by the frozen
  ``UNIQUE(subscription_id, fixture_id)`` and by deterministic remote event ids.
- **Traceable** — every calendar mutation writes exactly one ``sync_operations``
  row. No-ops write none.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.calendar_service import CalendarService
from app.application.services.sync_executor import ActionResult, ExecutionOutcome, SyncExecutor
from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.calendar.metadata import EventMetadata, ownership_filter
from app.domain.ports.calendar_provider import CalendarEventInput, EventQuery
from app.domain.sync.models import (
    CancelledPolicy,
    ConflictPolicy,
    EventMapping,
    FixtureSnapshot,
    RemoteEvent,
    SyncAction,
    SyncActionType,
    SyncMode,
    SyncPlan,
)
from app.domain.sync.planner import plan_sync
from app.domain.sync.rendering import render_event
from app.domain.value_objects.enums import (
    CalendarEventState,
    OperationStatus,
    OperationType,
    SubscriptionStatus,
    SyncStatus,
    SyncTrigger,
)
from app.exceptions.base import AppError, NotFoundError
from app.persistence.models.calendar_event import CalendarEvent
from app.persistence.models.fixture import Fixture
from app.persistence.models.subscription import Subscription
from app.persistence.models.sync import SyncHistory
from app.persistence.models.user import User
from app.persistence.repositories.sync_engine import (
    SyncFixtureRepository,
    SyncMappingRepository,
    SyncRunRepository,
    SyncSubscriptionRepository,
)

logger = get_logger(__name__)

_ACTION_TO_OPERATION: dict[SyncActionType, OperationType] = {
    SyncActionType.CREATE: OperationType.CREATE,
    SyncActionType.RECREATE: OperationType.CREATE,
    SyncActionType.UPDATE: OperationType.UPDATE,
    SyncActionType.CANCEL: OperationType.CANCEL,
    SyncActionType.DELETE: OperationType.DELETE,
    SyncActionType.RECONCILE: OperationType.UPDATE,
    SyncActionType.CONFLICT: OperationType.SKIP,
    SyncActionType.NO_OP: OperationType.SKIP,
}


class SubscriptionNotFoundError(NotFoundError):
    code = "subscription_not_found"
    message = "The subscription does not exist."


class SyncReport:
    """Live result of one run (the persisted form is ``sync_history``)."""

    def __init__(self, subscription_id: uuid.UUID, plan: SyncPlan) -> None:
        self.subscription_id = subscription_id
        self.plan = plan
        self.run_id: uuid.UUID | None = None
        self.status = SyncStatus.SUCCESS
        self.created = self.updated = self.deleted = self.skipped = self.failed = 0
        self.duplicates_prevented = 0
        self.api_calls = 0
        self.plan_ms = 0
        self.execute_ms = 0
        self.total_ms = 0
        self.error_summary: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id) if self.run_id else None,
            "subscription_id": str(self.subscription_id),
            "mode": self.plan.mode.value,
            "status": self.status.value,
            "plan": self.plan.stats.as_dict(),
            "created": self.created,
            "updated": self.updated,
            "deleted": self.deleted,
            "skipped": self.skipped,
            "failed": self.failed,
            "duplicates_prevented": self.duplicates_prevented,
            "api_calls": self.api_calls,
            "plan_ms": self.plan_ms,
            "execute_ms": self.execute_ms,
            "total_ms": self.total_ms,
            "error_summary": self.error_summary,
        }


class SyncService:
    def __init__(
        self,
        session: AsyncSession,
        calendar_service: CalendarService,
        subscriptions: SyncSubscriptionRepository,
        fixtures: SyncFixtureRepository,
        mappings: SyncMappingRepository,
        runs: SyncRunRepository,
        settings: Settings,
    ) -> None:
        self._session = session
        self._calendar = calendar_service
        self._subscriptions = subscriptions
        self._fixtures = fixtures
        self._mappings = mappings
        self._runs = runs
        self._settings = settings
        self._executor = SyncExecutor(calendar_service, batch_size=settings.sync_batch_size)

    # --- public API -----------------------------------------------------------
    async def build_plan(
        self, user: User, subscription_id: uuid.UUID, *, mode: SyncMode = SyncMode.INCREMENTAL
    ) -> SyncPlan:
        """Preview only. Zero calendar mutations, zero database writes."""
        subscription = await self._require_subscription(user, subscription_id)
        state = await self._load(user, subscription, mode)
        return self._plan(subscription, mode, state)

    async def synchronize(
        self,
        user: User,
        subscription_id: uuid.UUID,
        *,
        mode: SyncMode = SyncMode.INCREMENTAL,
        trigger: SyncTrigger = SyncTrigger.MANUAL,
        advance_schedule: bool = True,
    ) -> SyncReport:
        subscription = await self._require_subscription(user, subscription_id)
        return await self._run(user, subscription, mode, trigger, advance_schedule)

    async def synchronize_user(
        self,
        user: User,
        *,
        mode: SyncMode = SyncMode.INCREMENTAL,
        trigger: SyncTrigger = SyncTrigger.MANUAL,
    ) -> list[SyncReport]:
        subscriptions = await self._subscriptions.list_active_for_user(user.id)
        return [await self._run(user, s, mode, trigger, True) for s in subscriptions]

    async def synchronize_calendar(
        self,
        user: User,
        calendar_id: uuid.UUID,
        *,
        mode: SyncMode = SyncMode.INCREMENTAL,
    ) -> list[SyncReport]:
        subscriptions = await self._subscriptions.list_for_calendar(calendar_id, user.id)
        return [await self._run(user, s, mode, SyncTrigger.MANUAL, True) for s in subscriptions]

    # --- run ------------------------------------------------------------------
    async def _run(
        self,
        user: User,
        subscription: Subscription,
        mode: SyncMode,
        trigger: SyncTrigger,
        advance_schedule: bool,
    ) -> SyncReport:
        clock = time.perf_counter()
        started = datetime.now(UTC)

        plan_clock = time.perf_counter()
        state = await self._load(user, subscription, mode)
        plan = self._plan(subscription, mode, state)
        plan_ms = int((time.perf_counter() - plan_clock) * 1000)

        report = SyncReport(subscription.id, plan)
        report.plan_ms = plan_ms
        report.skipped = plan.stats.no_op

        logger.info(
            "sync.planned",
            subscription_id=str(subscription.id),
            mode=mode.value,
            plan_ms=plan_ms,
            **plan.stats.as_dict(),
        )

        # Invariant I5: an empty plan is a complete no-op.
        if plan.is_empty and self._settings.sync_skip_empty_runs:
            report.total_ms = int((time.perf_counter() - clock) * 1000)
            if advance_schedule:
                await self._advance_schedule(subscription, started)
                await self._session.commit()
            logger.info("sync.noop", subscription_id=str(subscription.id))
            return report

        run = await self._runs.add(
            SyncHistory(
                subscription_id=subscription.id,
                trigger=trigger,
                status=SyncStatus.RUNNING,
                started_at=started,
            )
        )
        await self._session.commit()
        report.run_id = run.id

        exec_clock = time.perf_counter()
        bodies, cancel_bodies = self._render(subscription, state.fixtures_by_id, plan)
        outcome = await self._executor.execute(
            plan,
            user=user,
            calendar_id=subscription.target_calendar_id,
            bodies=bodies,
            cancel_bodies=cancel_bodies,
        )
        report.execute_ms = int((time.perf_counter() - exec_clock) * 1000)

        await self._persist(
            subscription, run, plan, outcome, state, report, started, advance_schedule
        )
        report.total_ms = int((time.perf_counter() - clock) * 1000)

        logger.info(
            "sync.finished",
            subscription_id=str(subscription.id),
            run_id=str(run.id),
            status=report.status.value,
            api_calls=report.api_calls,
            created=report.created,
            updated=report.updated,
            deleted=report.deleted,
            skipped=report.skipped,
            failed=report.failed,
            duplicates_prevented=report.duplicates_prevented,
            no_op_ratio=plan.stats.no_op_ratio,
            total_ms=report.total_ms,
        )
        return report

    # --- load -----------------------------------------------------------------
    async def _require_subscription(self, user: User, subscription_id: uuid.UUID) -> Subscription:
        subscription = await self._subscriptions.get_for_user(subscription_id, user.id)
        if subscription is None:
            raise SubscriptionNotFoundError()
        return subscription

    async def _load(self, user: User, subscription: Subscription, mode: SyncMode) -> _State:
        now = datetime.now(UTC)
        start = now - timedelta(days=self._settings.sync_window_past_days)
        end = now + timedelta(days=self._settings.sync_window_future_days)

        mappings = list(await self._mappings.list_for_subscription(subscription.id))
        repair_ids = await self._mappings.list_repair_fixture_ids(subscription.id)

        watermark = None
        if mode is SyncMode.INCREMENTAL and subscription.last_synced_at:
            watermark = _as_utc(subscription.last_synced_at)

        # Authoritative scope membership, independent of what this run loaded.
        in_scope = await self._fixtures.list_scope_fixture_ids(subscription, start, end)

        rows = list(
            await self._fixtures.list_scope_fixtures(
                subscription,
                start,
                end,
                changed_since=watermark,
                include_ids=repair_ids,
                limit=self._settings.sync_max_fixtures,
            )
        )
        # Soft-deleted fixtures fall out of every scope query; pull them in via
        # their mapping so their events are removed rather than orphaned.
        rows.extend(await self._fixtures.list_gone_fixtures(subscription.id))

        by_id: dict[uuid.UUID, Fixture] = {}
        for row in rows:
            by_id.setdefault(row.id, row)

        snapshots = await self._fixtures.version_snapshots(
            [
                (m.fixture_id, m.synced_content_hash)
                for m in mappings
                if m.synced_content_hash and m.fixture_id in by_id
            ]
        )

        since = now - timedelta(days=self._settings.sync_retry_window_days)
        failures = await self._runs.failure_counts(subscription.id, since=since)
        blocked = frozenset(
            fid for fid, count in failures.items() if count >= self._settings.sync_max_item_retries
        )

        remote: list[RemoteEvent] | None = None
        if mode is SyncMode.RECONCILE:
            remote = await self._load_remote(user, subscription, start, end)

        return _State(
            fixtures_by_id=by_id,
            snapshots=[_to_snapshot(f) for f in by_id.values()],
            mappings=[_to_mapping(m) for m in mappings],
            mapping_rows={m.id: m for m in mappings},
            previous_snapshots=snapshots,
            remote_events=remote,
            blocked=blocked,
            in_scope=frozenset(in_scope),
        )

    async def _load_remote(
        self, user: User, subscription: Subscription, start: datetime, end: datetime
    ) -> list[RemoteEvent]:
        records = await self._calendar.list_events(
            user,
            subscription.target_calendar_id,
            EventQuery(time_min=start, time_max=end, metadata_filter=ownership_filter()),
        )
        events: list[RemoteEvent] = []
        for record in records:
            meta = EventMetadata.from_properties(record.metadata)
            events.append(
                RemoteEvent(
                    event_id=record.id,
                    app_id=meta.app_id or None if meta else None,
                    content_hash=meta.content_hash if meta else None,
                    owned=meta is not None,
                )
            )
        return events

    # --- plan / render --------------------------------------------------------
    def _plan(self, subscription: Subscription, mode: SyncMode, state: _State) -> SyncPlan:
        return plan_sync(
            subscription_id=subscription.id,
            mode=mode,
            fixtures=state.snapshots,
            mappings=state.mappings,
            in_scope_fixture_ids=state.in_scope,
            previous_snapshots=state.previous_snapshots,
            remote_events=state.remote_events,
            blocked_fixture_ids=state.blocked,
            conflict_policy=ConflictPolicy(self._settings.sync_conflict_policy),
            cancelled_policy=CancelledPolicy(self._settings.sync_cancelled_policy),
            max_actions=self._settings.sync_max_actions,
        )

    def _render(
        self, subscription: Subscription, fixtures: dict[uuid.UUID, Fixture], plan: SyncPlan
    ) -> tuple[dict[uuid.UUID, CalendarEventInput], dict[uuid.UUID, CalendarEventInput]]:
        duration = timedelta(minutes=self._settings.sync_default_event_duration_minutes)
        bodies: dict[uuid.UUID, CalendarEventInput] = {}
        cancels: dict[uuid.UUID, CalendarEventInput] = {}

        for action in plan.actions:
            if action.fixture_id is None or action.fixture_id not in fixtures:
                continue
            snapshot = _to_snapshot(fixtures[action.fixture_id])
            if action.type in (SyncActionType.CREATE, SyncActionType.RECREATE):
                bodies[action.fixture_id] = render_event(
                    snapshot,
                    prefix=subscription.event_prefix,
                    duration=duration,
                    include_event_id=True,  # deterministic id => I2
                )
            elif action.type is SyncActionType.UPDATE:
                bodies[action.fixture_id] = render_event(
                    snapshot, prefix=subscription.event_prefix, duration=duration
                )
            elif action.type is SyncActionType.CANCEL:
                cancels[action.fixture_id] = render_event(
                    snapshot, prefix=subscription.event_prefix, duration=duration
                )
        return bodies, cancels

    # --- persist ---------------------------------------------------------------
    async def _persist(
        self,
        subscription: Subscription,
        run: SyncHistory,
        plan: SyncPlan,
        outcome: ExecutionOutcome,
        state: _State,
        report: SyncReport,
        started: datetime,
        advance_schedule: bool,
    ) -> None:
        now = datetime.now(UTC)
        inserts: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        operations: list[dict[str, Any]] = []

        for result in outcome.results:
            action = result.action
            operations.append(_operation_row(run.id, result))

            if not result.success:
                report.failed += 1
                continue

            if action.type in (SyncActionType.CREATE, SyncActionType.RECREATE):
                report.created += 1
                self._write_create(action, result, subscription, state, inserts, updates, now)
            elif action.type is SyncActionType.UPDATE:
                report.updated += 1
                updates.append(
                    _mapping_update(action, result, state, now, CalendarEventState.ACTIVE)
                )
            elif action.type is SyncActionType.CANCEL:
                report.updated += 1
                updates.append(
                    _mapping_update(action, result, state, now, CalendarEventState.CANCELLED)
                )
            elif action.type is SyncActionType.DELETE:
                report.deleted += 1
                if action.mapping_id:
                    updates.append(
                        {
                            "id": action.mapping_id,
                            "state": CalendarEventState.DELETED,
                            "deleted_at": now,
                            "last_pushed_at": now,
                        }
                    )

        report.api_calls = outcome.api_calls
        report.duplicates_prevented = outcome.duplicates_prevented

        await self._mappings.bulk_insert(inserts)
        await self._mappings.bulk_update(updates)
        await self._runs.add_operations(operations)

        report.status = _run_status(outcome, report)
        if outcome.aborted:
            report.error_summary = f"aborted: {outcome.abort_reason}"
            if outcome.abort_reason in ("calendar_reauth_required", "calendar_permission_denied"):
                # Stop burning quota on a calendar we can no longer write to.
                subscription.status = SubscriptionStatus.PAUSED
        elif report.failed:
            first = next((r for r in outcome.failed if r.error_code), None)
            report.error_summary = (
                f"{report.failed} failed; first: {first.error_code}" if first else None
            )

        run.status = report.status
        run.finished_at = now
        run.created_count = report.created
        run.updated_count = report.updated
        run.deleted_count = report.deleted
        run.skipped_count = report.skipped
        run.failed_count = report.failed
        run.error_summary = report.error_summary

        # The watermark only advances when the run was not fatally aborted; a
        # partial run is safe to advance because the repair query re-picks the
        # unfinished mappings regardless of fixture freshness.
        if advance_schedule and report.status is not SyncStatus.FAILED:
            await self._advance_schedule(subscription, started)

        await self._session.commit()

    def _write_create(
        self,
        action: SyncAction,
        result: ActionResult,
        subscription: Subscription,
        state: _State,
        inserts: list[dict[str, Any]],
        updates: list[dict[str, Any]],
        now: datetime,
    ) -> None:
        fixture = state.fixtures_by_id[action.fixture_id]  # type: ignore[index]
        if action.mapping_id:  # RECREATE: the mapping row already exists
            updates.append(
                {
                    "id": action.mapping_id,
                    "external_event_id": result.external_event_id,
                    "fixture_identity_key": fixture.identity_key,
                    "synced_content_hash": fixture.content_hash,
                    "state": CalendarEventState.ACTIVE,
                    "deleted_at": None,
                    "last_pushed_at": now,
                }
            )
            return
        inserts.append(
            {
                "id": uuid.uuid4(),
                "subscription_id": subscription.id,
                "fixture_id": fixture.id,
                "calendar_id": subscription.target_calendar_id,
                "external_event_id": result.external_event_id,
                "fixture_identity_key": fixture.identity_key,
                "synced_content_hash": fixture.content_hash,
                "state": CalendarEventState.ACTIVE,
                "last_pushed_at": now,
            }
        )

    async def _advance_schedule(self, subscription: Subscription, started: datetime) -> None:
        subscription.last_synced_at = started
        subscription.next_sync_at = started + timedelta(minutes=subscription.sync_frequency_minutes)

    # --- metrics ---------------------------------------------------------------
    async def metrics(self, user: User) -> dict[str, Any]:
        subscriptions = await self._subscriptions.list_active_for_user(user.id)
        ids = [s.id for s in subscriptions]
        totals = await self._runs.metrics(ids)
        statuses = await self._runs.status_counts(ids)
        writes = totals.get("created", 0) + totals.get("updated", 0) + totals.get("deleted", 0)
        considered = writes + totals.get("skipped", 0)
        runs = totals.get("runs", 0)
        failures = statuses.get("failed", 0) + statuses.get("partial", 0)
        return {
            "subscriptions": len(ids),
            "runs": runs,
            "by_status": statuses,
            "calendar_writes": writes,
            "skipped_operations": totals.get("skipped", 0),
            "failed_operations": totals.get("failed", 0),
            "no_op_percentage": (
                round(100 * totals.get("skipped", 0) / considered, 2) if considered else 100.0
            ),
            "failure_rate": round(failures / runs, 4) if runs else 0.0,
            **{k: v for k, v in totals.items() if k not in ("runs",)},
        }


# --- helpers -------------------------------------------------------------------
class _State:
    def __init__(
        self,
        fixtures_by_id: dict[uuid.UUID, Fixture],
        snapshots: list[FixtureSnapshot],
        mappings: list[EventMapping],
        mapping_rows: dict[uuid.UUID, CalendarEvent],
        previous_snapshots: dict[uuid.UUID, dict[str, Any]],
        remote_events: list[RemoteEvent] | None,
        blocked: frozenset[uuid.UUID],
        in_scope: frozenset[uuid.UUID],
    ) -> None:
        self.fixtures_by_id = fixtures_by_id
        self.snapshots = snapshots
        self.mappings = mappings
        self.mapping_rows = mapping_rows
        self.previous_snapshots = previous_snapshots
        self.remote_events = remote_events
        self.blocked = blocked
        self.in_scope = in_scope


def _as_utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _to_snapshot(fixture: Fixture) -> FixtureSnapshot:
    competition = fixture.competition
    return FixtureSnapshot(
        id=fixture.id,
        identity_key=fixture.identity_key,
        content_hash=fixture.content_hash,
        version=fixture.version,
        sport_key=competition.sport.key if competition and competition.sport else "",
        competition_name=competition.name if competition else "",
        scheduled_start=_as_utc(fixture.scheduled_start),
        scheduled_end=_as_utc(fixture.scheduled_end) if fixture.scheduled_end else None,
        status=fixture.status,
        venue=fixture.venue,
        round=fixture.round,
        stage=fixture.stage,
        home_name=fixture.home_team.name if fixture.home_team else None,
        away_name=fixture.away_team.name if fixture.away_team else None,
        is_deleted=fixture.deleted_at is not None,
    )


def _to_mapping(row: CalendarEvent) -> EventMapping:
    return EventMapping(
        id=row.id,
        fixture_id=row.fixture_id,
        fixture_identity_key=row.fixture_identity_key,
        state=row.state,
        external_event_id=row.external_event_id,
        synced_content_hash=row.synced_content_hash,
        is_deleted=row.deleted_at is not None,
    )


def _mapping_update(
    action: SyncAction,
    result: ActionResult,
    state: _State,
    now: datetime,
    new_state: CalendarEventState,
) -> dict[str, Any]:
    fixture = state.fixtures_by_id[action.fixture_id]  # type: ignore[index]
    return {
        "id": action.mapping_id,
        "external_event_id": result.external_event_id or action.external_event_id,
        "fixture_identity_key": fixture.identity_key,
        # I6: the hash is written only after the calendar call succeeded.
        "synced_content_hash": fixture.content_hash,
        "state": new_state,
        "last_pushed_at": now,
    }


def _operation_row(run_id: uuid.UUID, result: ActionResult) -> dict[str, Any]:
    action = result.action
    return {
        "id": uuid.uuid4(),
        "sync_history_id": run_id,
        "fixture_id": action.fixture_id,
        "calendar_event_id": action.mapping_id,
        "operation_type": _ACTION_TO_OPERATION[action.type],
        "status": OperationStatus.SUCCESS if result.success else OperationStatus.FAILED,
        "message": result.message or result.error_code or action.reason,
    }


def _run_status(outcome: ExecutionOutcome, report: SyncReport) -> SyncStatus:
    if outcome.aborted and not outcome.succeeded:
        return SyncStatus.FAILED
    if outcome.aborted or report.failed:
        return SyncStatus.PARTIAL
    return SyncStatus.SUCCESS


__all__ = ["AppError", "SubscriptionNotFoundError", "SyncReport", "SyncService"]
