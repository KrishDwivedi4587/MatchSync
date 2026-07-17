"""SyncService end-to-end tests against SQLite and a fake CalendarService.

The fake records every call, which lets us assert the headline guarantee
directly: **a second run on unchanged fixtures makes zero calendar API calls and
zero database writes.**
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.sync_service import SubscriptionNotFoundError, SyncService
from app.core.config import get_settings
from app.domain.calendar.metadata import EventMetadata, derive_event_id
from app.domain.hashing import stable_hash
from app.domain.ports.calendar_provider import (
    BatchResult,
    CalendarEventRecord,
    EventTime,
)
from app.domain.sync.models import SyncMode
from app.domain.value_objects.enums import (
    CalendarEventState,
    CalendarProvider,
    CompetitionType,
    FixtureStatus,
    SportCategory,
    SubscriptionStatus,
    SubscriptionType,
    SyncStatus,
)
from app.exceptions.calendar import QuotaExceededError
from app.persistence.models.account import GoogleAccount
from app.persistence.models.calendar import Calendar
from app.persistence.models.calendar_event import CalendarEvent
from app.persistence.models.catalog import Competition, Sport, Team
from app.persistence.models.fixture import Fixture
from app.persistence.models.subscription import Subscription
from app.persistence.models.sync import SyncHistory, SyncOperation
from app.persistence.models.user import User
from app.persistence.repositories.sync_engine import (
    SyncFixtureRepository,
    SyncMappingRepository,
    SyncRunRepository,
    SyncSubscriptionRepository,
)

START = datetime(2026, 8, 1, 15, 0, tzinfo=UTC)


class FakeCalendarService:
    """Records calls; the engine must talk to nothing else."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.created: list = []
        self.deleted: list[str] = []
        self.remote: list[CalendarEventRecord] = []
        self.create_conflicts: set[str] = set()  # event ids the provider rejects
        self.update_not_found: set[str] = set()
        self.fail_with: Exception | None = None
        self._counter = 0

    def _record(self, event_id, body, calendar_id="cal"):
        return CalendarEventRecord(
            id=event_id,
            calendar_id=str(calendar_id),
            title=body.title,
            when=EventTime(start=body.when.start, end=body.when.end),
            metadata=dict(body.metadata),
        )

    async def batch_create_events(self, user, calendar_id, events):
        self.calls.append("batch_create")
        if self.fail_with:
            raise self.fail_with
        results = []
        for i, body in enumerate(events):
            if body.event_id in self.create_conflicts:
                results.append(BatchResult(index=i, success=False, error_code="duplicate"))
                continue
            self._counter += 1
            event_id = body.event_id or f"evt-{self._counter}"
            self.created.append(body)
            results.append(BatchResult(index=i, success=True, event=self._record(event_id, body)))
        return results

    async def batch_update_events(self, user, calendar_id, items):
        self.calls.append("batch_update")
        if self.fail_with:
            raise self.fail_with
        results = []
        for i, (event_id, body) in enumerate(items):
            if event_id in self.update_not_found:
                results.append(BatchResult(index=i, success=False, error_code="notFound"))
                continue
            results.append(BatchResult(index=i, success=True, event=self._record(event_id, body)))
        return results

    async def batch_delete_events(self, user, calendar_id, event_ids):
        self.calls.append("batch_delete")
        if self.fail_with:
            raise self.fail_with
        self.deleted.extend(event_ids)
        return [BatchResult(index=i, success=True) for i in range(len(event_ids))]

    async def update_event(self, user, calendar_id, event_id, body):
        self.calls.append("update_event")
        return self._record(event_id, body)

    async def create_event(self, user, calendar_id, body):
        self.calls.append("create_event")
        self._counter += 1
        return self._record(body.event_id or f"evt-{self._counter}", body)

    async def list_events(self, user, calendar_id, query):
        self.calls.append("list_events")
        return list(self.remote)


def _hash(*parts) -> str:
    return stable_hash(*parts)


@pytest_asyncio.fixture
async def ctx(db_session: AsyncSession):
    user = User(email="sync@example.com")
    account = GoogleAccount(
        user=user,
        provider=CalendarProvider.GOOGLE,
        provider_subject="s1",
        email="sync@example.com",
        is_primary=True,
    )
    calendar = Calendar(
        google_account=account,
        provider=CalendarProvider.GOOGLE,
        external_calendar_id="cal-1",
        summary="Sports",
        access_role="owner",
        is_sync_target=True,
    )
    sport = Sport(
        key="football", name="Football", category=SportCategory.TEAM, provider_key="f-api"
    )
    competition = Competition(
        sport=sport,
        provider_competition_id="PL",
        name="Premier League",
        type=CompetitionType.LEAGUE,
    )
    arsenal = Team(sport=sport, provider_team_id="57", name="Arsenal")
    chelsea = Team(sport=sport, provider_team_id="61", name="Chelsea")
    subscription = Subscription(
        user=user,
        target_calendar=calendar,
        sport=sport,
        scope_type=SubscriptionType.COMPETITION,
        competition=competition,
        status=SubscriptionStatus.ACTIVE,
    )
    db_session.add_all(
        [user, account, calendar, sport, competition, arsenal, chelsea, subscription]
    )
    await db_session.commit()

    calendar_service = FakeCalendarService()
    engine = SyncService(
        db_session,
        calendar_service,
        SyncSubscriptionRepository(db_session),
        SyncFixtureRepository(db_session),
        SyncMappingRepository(db_session),
        SyncRunRepository(db_session),
        get_settings(),
    )
    return engine, calendar_service, db_session, user, subscription, competition, arsenal, chelsea


async def add_fixture(
    db,
    competition,
    home,
    away,
    *,
    ident="ident-1",
    start=START,
    status=FixtureStatus.SCHEDULED,
    venue=None,
    content=None,
) -> Fixture:
    fixture = Fixture(
        competition_id=competition.id,
        provider_fixture_id=ident,
        identity_key=ident,
        content_hash=content or _hash(ident, start.isoformat(), status.value),
        home_team_id=home.id,
        away_team_id=away.id,
        scheduled_start=start,
        status=status,
        venue=venue,
    )
    db.add(fixture)
    await db.commit()
    return fixture


async def _count(db, model) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


# --- happy path -----------------------------------------------------------
async def test_first_sync_creates_events_and_mappings(ctx) -> None:
    engine, cal, db, user, sub, comp, home, away = ctx
    await add_fixture(db, comp, home, away)

    report = await engine.synchronize(user, sub.id)

    assert report.status is SyncStatus.SUCCESS
    assert report.created == 1 and report.api_calls == 1
    assert cal.calls == ["batch_create"]
    assert await _count(db, CalendarEvent) == 1

    mapping = await db.scalar(select(CalendarEvent))
    assert mapping.state is CalendarEventState.ACTIVE
    assert mapping.external_event_id == derive_event_id("ident-1")
    assert mapping.synced_content_hash is not None

    # Every calendar mutation is traceable (invariant I7).
    assert await _count(db, SyncOperation) == 1
    assert await _count(db, SyncHistory) == 1


async def test_created_event_carries_matchsync_metadata(ctx) -> None:
    engine, cal, db, user, sub, comp, home, away = ctx
    await add_fixture(db, comp, home, away)
    await engine.synchronize(user, sub.id)

    body = cal.created[0]
    meta = EventMetadata.from_properties(body.metadata)
    assert meta is not None and meta.app_id == "ident-1"
    assert body.title == "Arsenal vs Chelsea"
    assert body.event_id == derive_event_id("ident-1")


# --- THE headline guarantee -------------------------------------------------
async def test_second_sync_is_a_total_no_op(ctx) -> None:
    """Idempotency: zero API calls, zero writes, zero duplicates."""
    engine, cal, db, user, sub, comp, home, away = ctx
    await add_fixture(db, comp, home, away)
    await engine.synchronize(user, sub.id)

    cal.calls.clear()
    runs_before = await _count(db, SyncHistory)
    ops_before = await _count(db, SyncOperation)

    report = await engine.synchronize(user, sub.id, advance_schedule=False)

    assert cal.calls == []  # zero calendar API calls
    assert report.api_calls == 0
    assert report.plan.is_empty
    assert report.skipped == 0  # incremental: the fixture wasn't even loaded
    assert await _count(db, CalendarEvent) == 1  # zero duplicate events
    assert await _count(db, SyncHistory) == runs_before  # zero sync_history writes
    assert await _count(db, SyncOperation) == ops_before


async def test_incremental_run_never_deletes_unchanged_fixtures_events(ctx) -> None:
    """Regression: incremental mode loads only *changed* fixtures.

    "Absent from the loaded set" must never be read as "no longer in scope", or
    every unchanged fixture's event would be deleted on the second run.
    """
    engine, cal, db, user, sub, comp, home, away = ctx
    for i in range(3):
        await add_fixture(db, comp, home, away, ident=f"m{i}", start=START + timedelta(days=i))
    await engine.synchronize(user, sub.id)
    assert await _count(db, CalendarEvent) == 3

    cal.calls.clear()
    plan = await engine.build_plan(user, sub.id, mode=SyncMode.INCREMENTAL)

    assert plan.stats.delete == 0
    assert plan.is_empty
    assert cal.deleted == []


async def test_full_mode_second_run_is_also_a_no_op(ctx) -> None:
    """Even scanning every fixture, an unchanged one produces no mutation."""
    engine, cal, db, user, sub, comp, home, away = ctx
    await add_fixture(db, comp, home, away)
    await engine.synchronize(user, sub.id, mode=SyncMode.FULL)

    cal.calls.clear()
    report = await engine.synchronize(user, sub.id, mode=SyncMode.FULL, advance_schedule=False)

    assert cal.calls == []
    assert report.plan.stats.no_op == 1
    assert report.plan.is_empty


async def test_ten_consecutive_syncs_create_exactly_one_event(ctx) -> None:
    engine, cal, db, user, sub, comp, home, away = ctx
    await add_fixture(db, comp, home, away)
    for _ in range(10):
        await engine.synchronize(user, sub.id, mode=SyncMode.FULL)
    assert await _count(db, CalendarEvent) == 1
    assert cal.calls.count("batch_create") == 1


# --- change detection --------------------------------------------------------
async def test_time_change_updates_the_event(ctx) -> None:
    engine, cal, db, user, sub, comp, home, away = ctx
    fixture = await add_fixture(db, comp, home, away)
    await engine.synchronize(user, sub.id)

    fixture.scheduled_start = START + timedelta(hours=2)
    fixture.content_hash = _hash("changed")
    await db.commit()

    cal.calls.clear()
    report = await engine.synchronize(user, sub.id, mode=SyncMode.FULL)

    assert report.updated == 1
    assert cal.calls == ["batch_update"]
    mapping = await db.scalar(select(CalendarEvent))
    assert mapping.synced_content_hash == fixture.content_hash  # invariant I6


async def test_cancelled_fixture_annotates_rather_than_deletes(ctx) -> None:
    engine, cal, db, user, sub, comp, home, away = ctx
    fixture = await add_fixture(db, comp, home, away)
    await engine.synchronize(user, sub.id)

    fixture.status = FixtureStatus.CANCELLED
    fixture.content_hash = _hash("cancelled")
    await db.commit()
    cal.calls.clear()

    report = await engine.synchronize(user, sub.id, mode=SyncMode.FULL)
    assert report.updated == 1
    assert cal.calls == ["batch_update"]
    assert cal.deleted == []  # the event survives; users want to see cancellations

    mapping = await db.scalar(select(CalendarEvent))
    assert mapping.state is CalendarEventState.CANCELLED


async def test_deleted_fixture_removes_the_event(ctx) -> None:
    engine, cal, db, user, sub, comp, home, away = ctx
    fixture = await add_fixture(db, comp, home, away)
    await engine.synchronize(user, sub.id)
    event_id = (await db.scalar(select(CalendarEvent))).external_event_id

    fixture.deleted_at = datetime.now(UTC)
    fixture.status = FixtureStatus.DELETED
    await db.commit()
    cal.calls.clear()

    report = await engine.synchronize(user, sub.id, mode=SyncMode.FULL)
    assert report.deleted == 1
    assert cal.deleted == [event_id]

    mapping = await db.scalar(select(CalendarEvent))
    assert mapping.state is CalendarEventState.DELETED
    assert mapping.deleted_at is not None


async def test_fixture_leaving_the_window_deletes_its_event(ctx) -> None:
    engine, cal, db, user, sub, comp, home, away = ctx
    fixture = await add_fixture(db, comp, home, away)
    await engine.synchronize(user, sub.id)

    # Move it far outside the sync window.
    fixture.scheduled_start = datetime.now(UTC) + timedelta(days=900)
    await db.commit()
    cal.calls.clear()

    report = await engine.synchronize(user, sub.id, mode=SyncMode.FULL)
    assert report.deleted == 1
    assert cal.calls == ["batch_delete"]


# --- duplicate prevention -----------------------------------------------------
async def test_provider_rejects_duplicate_insert_and_engine_repairs(ctx) -> None:
    """The event already exists under our deterministic id (invariant I2)."""
    engine, cal, db, user, sub, comp, home, away = ctx
    await add_fixture(db, comp, home, away)
    cal.create_conflicts.add(derive_event_id("ident-1"))

    report = await engine.synchronize(user, sub.id)

    assert report.created == 1
    assert report.duplicates_prevented == 1
    assert "update_event" in cal.calls  # repaired by patching the existing event
    assert await _count(db, CalendarEvent) == 1


async def test_unconfirmed_mapping_is_recreated(ctx) -> None:
    """A previous run's create never landed (external_event_id is NULL)."""
    engine, _cal, db, user, sub, comp, home, away = ctx
    fixture = await add_fixture(db, comp, home, away)
    db.add(
        CalendarEvent(
            subscription_id=sub.id,
            fixture_id=fixture.id,
            calendar_id=sub.target_calendar_id,
            fixture_identity_key=fixture.identity_key,
            external_event_id=None,
        )
    )
    await db.commit()

    report = await engine.synchronize(user, sub.id)
    assert report.created == 1
    assert await _count(db, CalendarEvent) == 1  # repaired, not duplicated

    mapping = await db.scalar(select(CalendarEvent))
    assert mapping.external_event_id is not None


async def test_missing_remote_event_on_update_is_recreated(ctx) -> None:
    engine, cal, db, user, sub, comp, home, away = ctx
    fixture = await add_fixture(db, comp, home, away)
    await engine.synchronize(user, sub.id)
    mapping = await db.scalar(select(CalendarEvent))

    cal.update_not_found.add(mapping.external_event_id)
    fixture.content_hash = _hash("changed")
    await db.commit()
    cal.calls.clear()

    report = await engine.synchronize(user, sub.id, mode=SyncMode.FULL)
    assert report.updated == 1
    assert "create_event" in cal.calls  # recreated in-line
    assert await _count(db, CalendarEvent) == 1


# --- reconcile / conflicts -----------------------------------------------------
async def test_reconcile_deletes_orphans_and_duplicates(ctx) -> None:
    engine, cal, db, user, sub, comp, home, away = ctx
    await add_fixture(db, comp, home, away)
    await engine.synchronize(user, sub.id)
    kept = (await db.scalar(select(CalendarEvent))).external_event_id

    cal.remote = [
        CalendarEventRecord(
            id=kept,
            calendar_id="c",
            title="t",
            when=EventTime(START, START),
            metadata=EventMetadata(
                app_id="ident-1",
                content_hash=(await db.scalar(select(CalendarEvent))).synced_content_hash,
            ).to_properties(),
        ),
        CalendarEventRecord(  # a stray duplicate of the same fixture
            id="evt-dup",
            calendar_id="c",
            title="t",
            when=EventTime(START, START),
            metadata=EventMetadata(app_id="ident-1").to_properties(),
        ),
        CalendarEventRecord(  # an orphan: no fixture claims it
            id="evt-orphan",
            calendar_id="c",
            title="t",
            when=EventTime(START, START),
            metadata=EventMetadata(app_id="long-gone").to_properties(),
        ),
    ]
    cal.calls.clear()

    report = await engine.synchronize(user, sub.id, mode=SyncMode.RECONCILE)
    assert report.deleted == 2
    assert set(cal.deleted) == {"evt-dup", "evt-orphan"}
    assert kept not in cal.deleted


async def test_manual_edit_survives_when_the_fixture_is_unchanged(ctx) -> None:
    engine, cal, db, user, sub, comp, home, away = ctx
    await add_fixture(db, comp, home, away)
    await engine.synchronize(user, sub.id)
    mapping = await db.scalar(select(CalendarEvent))

    cal.remote = [
        CalendarEventRecord(
            id=mapping.external_event_id,
            calendar_id="c",
            title="User renamed this",
            when=EventTime(START, START),
            metadata=EventMetadata(app_id="ident-1", content_hash="user-edited").to_properties(),
        )
    ]
    cal.calls.clear()

    report = await engine.synchronize(user, sub.id, mode=SyncMode.RECONCILE)
    assert report.plan.is_empty
    assert "batch_update" not in cal.calls  # we do not clobber the user's edit


# --- failure and recovery --------------------------------------------------------
async def test_quota_exceeded_aborts_and_leaves_state_replayable(ctx) -> None:
    engine, cal, db, user, sub, comp, home, away = ctx
    await add_fixture(db, comp, home, away)
    cal.fail_with = QuotaExceededError()

    report = await engine.synchronize(user, sub.id)

    assert report.status is SyncStatus.FAILED
    assert report.error_summary and "quota" in report.error_summary
    # Nothing was marked synced, so the next run re-plans the same create.
    assert await _count(db, CalendarEvent) == 0

    cal.fail_with = None
    cal.calls.clear()
    retry = await engine.synchronize(user, sub.id, mode=SyncMode.FULL)
    assert retry.created == 1
    assert await _count(db, CalendarEvent) == 1


async def test_partial_failure_isolates_the_bad_item(ctx) -> None:
    engine, cal, db, user, sub, comp, home, away = ctx
    good = await add_fixture(db, comp, home, away, ident="good")
    await add_fixture(db, comp, home, away, ident="bad", start=START + timedelta(days=1))

    # Make one create fail (not a conflict -> a genuine item error).
    original = cal.batch_create_events

    async def failing(user_, calendar_id, events):
        results = await original(user_, calendar_id, events)
        return [
            (
                BatchResult(index=r.index, success=False, error_code="itemError")
                if events[r.index].event_id == derive_event_id("bad")
                else r
            )
            for r in results
        ]

    cal.batch_create_events = failing  # type: ignore[method-assign]
    report = await engine.synchronize(user, sub.id)

    assert report.status is SyncStatus.PARTIAL
    assert report.created == 1 and report.failed == 1
    assert await _count(db, CalendarEvent) == 1  # only the good one persisted

    # The failure is traceable.
    failed_ops = await db.scalar(
        select(func.count()).select_from(SyncOperation).where(SyncOperation.status == "failed")
    )
    assert failed_ops == 1
    _ = good


async def test_revoked_access_pauses_the_subscription(ctx) -> None:
    from app.exceptions.calendar import CalendarReauthRequiredError

    engine, cal, db, user, sub, comp, home, away = ctx
    await add_fixture(db, comp, home, away)
    cal.fail_with = CalendarReauthRequiredError()

    await engine.synchronize(user, sub.id)
    await db.refresh(sub)
    assert sub.status is SubscriptionStatus.PAUSED  # stop burning quota


async def test_retry_budget_exhaustion_blocks_the_unit(ctx) -> None:
    engine, cal, db, user, sub, comp, home, away = ctx
    fixture = await add_fixture(db, comp, home, away)

    # Fabricate three prior failures for this fixture.
    run = SyncHistory(subscription_id=sub.id, trigger="manual", status=SyncStatus.FAILED)
    db.add(run)
    await db.flush()
    for _ in range(get_settings().sync_max_item_retries):
        db.add(
            SyncOperation(
                sync_history_id=run.id,
                fixture_id=fixture.id,
                operation_type="create",
                status="failed",
            )
        )
    await db.commit()
    cal.calls.clear()

    plan = await engine.build_plan(user, sub.id, mode=SyncMode.FULL)
    assert plan.stats.conflict == 1 and plan.stats.create == 0
    assert plan.is_empty  # dead-lettered, not retried forever


# --- scope, ownership, plan preview -----------------------------------------------
async def test_plan_preview_performs_no_writes_and_no_api_calls(ctx) -> None:
    engine, cal, db, user, sub, comp, home, away = ctx
    await add_fixture(db, comp, home, away)

    plan = await engine.build_plan(user, sub.id, mode=SyncMode.FULL)

    assert plan.stats.create == 1
    assert cal.calls == []
    assert await _count(db, CalendarEvent) == 0
    assert await _count(db, SyncHistory) == 0


async def test_subscription_of_another_user_is_not_reachable(ctx) -> None:
    engine, _, db, _, sub, _, _, _ = ctx
    other = User(email="other@example.com")
    db.add(other)
    await db.commit()

    with pytest.raises(SubscriptionNotFoundError):
        await engine.synchronize(other, sub.id)


async def test_unknown_subscription_raises(ctx) -> None:
    engine, _, _, user, _, _, _, _ = ctx
    with pytest.raises(SubscriptionNotFoundError):
        await engine.synchronize(user, uuid.uuid4())


async def test_team_scope_only_matches_that_team(ctx) -> None:
    engine, _cal, db, user, sub, comp, home, away = ctx
    other = Team(sport_id=comp.sport_id, provider_team_id="99", name="Spurs")
    db.add(other)
    await db.commit()

    sub.scope_type = SubscriptionType.TEAM
    sub.competition_id = None
    sub.team_id = home.id
    await db.commit()

    await add_fixture(db, comp, home, away, ident="with-arsenal")
    await add_fixture(db, comp, other, away, ident="without", start=START + timedelta(days=1))

    report = await engine.synchronize(user, sub.id, mode=SyncMode.FULL)
    assert report.created == 1


# --- scale ------------------------------------------------------------------------
async def test_bulk_sync_batches_calendar_calls(ctx) -> None:
    engine, cal, db, user, sub, comp, home, away = ctx
    for i in range(120):
        await add_fixture(db, comp, home, away, ident=f"m{i}", start=START + timedelta(hours=i))

    report = await engine.synchronize(user, sub.id, mode=SyncMode.FULL)

    assert report.created == 120
    # 120 creates at batch size 50 -> 3 batch calls, not 120.
    assert cal.calls.count("batch_create") == 3
    assert report.api_calls == 3
    assert await _count(db, CalendarEvent) == 120

    cal.calls.clear()
    again = await engine.synchronize(user, sub.id, mode=SyncMode.FULL, advance_schedule=False)
    assert cal.calls == [] and again.plan.is_empty


async def test_metrics_aggregate_across_runs(ctx) -> None:
    engine, _cal, db, user, sub, comp, home, away = ctx
    await add_fixture(db, comp, home, away)
    await engine.synchronize(user, sub.id)

    metrics = await engine.metrics(user)
    assert metrics["runs"] == 1
    assert metrics["calendar_writes"] == 1
    assert metrics["subscriptions"] == 1
    assert "no_op_percentage" in metrics and "failure_rate" in metrics
