"""Unit tests for the pure sync domain: diff engine, planner, rendering.

Includes property-style determinism tests: the planner must be a pure function of
its inputs, invariant under input ordering.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.calendar.metadata import EventMetadata, derive_event_id
from app.domain.sync.diff import Verdict, changed_fields, diff
from app.domain.sync.models import (
    CancelledPolicy,
    ChangeKind,
    ConflictPolicy,
    EventMapping,
    FixtureSnapshot,
    RemoteEvent,
    SyncActionType,
    SyncMode,
)
from app.domain.sync.planner import plan_sync
from app.domain.sync.rendering import render_event, render_title
from app.domain.value_objects.enums import CalendarEventState, FixtureStatus

START = datetime(2026, 8, 1, 15, 0, tzinfo=UTC)
SUB = uuid.uuid4()


def snap(**overrides) -> FixtureSnapshot:
    defaults = {
        "id": uuid.uuid4(),
        "identity_key": "ident-1",
        "content_hash": "hash-1",
        "version": 1,
        "sport_key": "football",
        "competition_name": "Premier League",
        "scheduled_start": START,
        "status": FixtureStatus.SCHEDULED,
        "home_name": "Arsenal",
        "away_name": "Chelsea",
    }
    defaults.update(overrides)
    return FixtureSnapshot(**defaults)


def mapping(fixture_id, **overrides) -> EventMapping:
    defaults = {
        "id": uuid.uuid4(),
        "fixture_id": fixture_id,
        "fixture_identity_key": "ident-1",
        "state": CalendarEventState.ACTIVE,
        "external_event_id": "evt-1",
        "synced_content_hash": "hash-1",
    }
    defaults.update(overrides)
    return EventMapping(**defaults)


# --- diff engine -----------------------------------------------------------
def test_no_mapping_yields_create() -> None:
    assert diff(snap(), None).kind is ChangeKind.CREATE


def test_matching_hash_yields_no_change() -> None:
    f = snap()
    assert diff(f, mapping(f.id)).kind is ChangeKind.NO_CHANGE


def test_changed_hash_yields_update() -> None:
    f = snap(content_hash="hash-2")
    verdict = diff(f, mapping(f.id))
    assert verdict.kind is ChangeKind.MAJOR_UPDATE  # no snapshot -> safe over-approx


def test_minor_vs_major_update_classification() -> None:
    f = snap(content_hash="hash-2", venue="Wembley")
    previous = {"scheduled_start": START.isoformat(), "status": "scheduled", "venue": "Emirates"}
    assert diff(f, mapping(f.id), previous_snapshot=previous).kind is ChangeKind.MINOR_UPDATE

    moved = snap(content_hash="hash-3", scheduled_start=START + timedelta(hours=2))
    assert (
        diff(moved, mapping(moved.id), previous_snapshot=previous).kind is ChangeKind.MAJOR_UPDATE
    )


def test_deleted_fixture_yields_delete_only_when_event_exists() -> None:
    f = snap(is_deleted=True)
    assert diff(f, mapping(f.id)).kind is ChangeKind.DELETE
    assert diff(f, None).kind is ChangeKind.NO_CHANGE  # never create for a dead fixture


def test_status_deleted_is_treated_as_gone() -> None:
    f = snap(status=FixtureStatus.DELETED)
    assert diff(f, mapping(f.id)).kind is ChangeKind.DELETE


def test_cancelled_fixture_annotates_by_default() -> None:
    f = snap(status=FixtureStatus.CANCELLED, content_hash="hash-2")
    assert diff(f, mapping(f.id)).kind is ChangeKind.CANCEL


def test_cancelled_fixture_can_be_deleted_by_policy() -> None:
    f = snap(status=FixtureStatus.CANCELLED, content_hash="hash-2")
    verdict = diff(f, mapping(f.id), cancelled_policy=CancelledPolicy.DELETE)
    assert verdict.kind is ChangeKind.DELETE


def test_cancelled_fixture_with_matching_hash_still_reconciles_mapping_state() -> None:
    """The hash matches but the mapping was never marked cancelled."""
    f = snap(status=FixtureStatus.CANCELLED)
    assert diff(f, mapping(f.id)).kind is ChangeKind.CANCEL


def test_unconfirmed_mapping_yields_recreate() -> None:
    f = snap()
    assert diff(f, mapping(f.id, external_event_id=None)).kind is ChangeKind.RECREATE


def test_missing_remote_event_yields_recreate_only_in_reconcile() -> None:
    f = snap()
    m = mapping(f.id)
    assert diff(f, m, remote=None, remote_known=True).kind is ChangeKind.RECREATE
    # Fast path never concludes the event is missing.
    assert diff(f, m, remote=None, remote_known=False).kind is ChangeKind.NO_CHANGE


def test_user_edit_is_preserved_when_fixture_unchanged() -> None:
    """FIXTURE_WINS still never clobbers a manual edit if nothing changed."""
    f = snap()
    m = mapping(f.id)
    remote = RemoteEvent("evt-1", app_id="ident-1", content_hash="user-edited")
    assert diff(f, m, remote=remote, remote_known=True).kind is ChangeKind.NO_CHANGE


def test_user_edit_is_overwritten_when_fixture_changed_under_fixture_wins() -> None:
    f = snap(content_hash="hash-2")
    m = mapping(f.id)
    remote = RemoteEvent("evt-1", app_id="ident-1", content_hash="user-edited")
    verdict = diff(f, m, remote=remote, remote_known=True)
    assert verdict.kind is ChangeKind.MAJOR_UPDATE
    assert verdict.reason == "user_modified_event_overwritten"


def test_user_edit_blocks_update_under_user_wins() -> None:
    f = snap(content_hash="hash-2")
    m = mapping(f.id)
    remote = RemoteEvent("evt-1", app_id="ident-1", content_hash="user-edited")
    verdict = diff(f, m, remote=remote, remote_known=True, conflict_policy=ConflictPolicy.USER_WINS)
    assert verdict.kind is ChangeKind.CONFLICT


def test_changed_fields_diffs_a_stored_snapshot() -> None:
    previous = {"scheduled_start": START.isoformat(), "status": "scheduled", "venue": "Emirates"}
    f = snap(scheduled_start=START + timedelta(hours=1), venue="Wembley")
    assert changed_fields(previous, f) == ("scheduled_start", "venue")
    assert changed_fields(None, f) == ()

    # A field present in the snapshot but absent on the fixture also counts.
    assert "round" in changed_fields({**previous, "round": "1"}, f)


def test_changed_fields_tolerates_naive_snapshot_datetimes() -> None:
    previous = {"scheduled_start": "2026-08-01T15:00:00", "status": "scheduled"}
    assert "scheduled_start" not in changed_fields(previous, snap())


# --- planner ---------------------------------------------------------------
def test_plan_creates_updates_and_deletes() -> None:
    new = snap(identity_key="a", content_hash="h1")
    stale = snap(identity_key="b", content_hash="h2")
    same = snap(identity_key="c", content_hash="h3")
    gone = mapping(uuid.uuid4(), fixture_identity_key="z", external_event_id="evt-z")

    plan = plan_sync(
        subscription_id=SUB,
        mode=SyncMode.FULL,
        fixtures=[new, stale, same],
        mappings=[
            mapping(stale.id, fixture_identity_key="b", synced_content_hash="old"),
            mapping(same.id, fixture_identity_key="c", synced_content_hash="h3"),
            gone,
        ],
    )
    assert plan.stats.create == 1
    assert plan.stats.update == 1
    assert plan.stats.no_op == 1
    assert plan.stats.delete == 1  # the mapping whose fixture left the scope
    assert not plan.is_empty


def test_plan_is_empty_when_everything_matches() -> None:
    f = snap()
    plan = plan_sync(
        subscription_id=SUB, mode=SyncMode.INCREMENTAL, fixtures=[f], mappings=[mapping(f.id)]
    )
    assert plan.is_empty
    assert plan.stats.no_op == 1
    assert plan.stats.mutations == 0


def test_plan_of_nothing_is_empty() -> None:
    plan = plan_sync(subscription_id=SUB, mode=SyncMode.INCREMENTAL, fixtures=[], mappings=[])
    assert plan.is_empty and plan.actions == ()


def test_plan_is_deterministic_under_input_shuffling() -> None:
    """Property: plan(shuffle(F), shuffle(M)) == plan(F, M), always."""
    fixtures = [snap(identity_key=f"k{i}", content_hash=f"h{i}") for i in range(30)]
    mappings = [
        mapping(
            f.id,
            fixture_identity_key=f.identity_key,
            synced_content_hash="old",
            external_event_id=f"evt-{i}",
        )
        for i, f in enumerate(fixtures[:15])
    ]
    baseline = plan_sync(
        subscription_id=SUB, mode=SyncMode.FULL, fixtures=fixtures, mappings=mappings
    )

    rng = random.Random(1234)
    for _ in range(25):
        f_shuffled = fixtures[:]
        m_shuffled = mappings[:]
        rng.shuffle(f_shuffled)
        rng.shuffle(m_shuffled)
        candidate = plan_sync(
            subscription_id=SUB, mode=SyncMode.FULL, fixtures=f_shuffled, mappings=m_shuffled
        )
        assert candidate.actions == baseline.actions
        assert candidate.stats == baseline.stats


def test_plan_ordering_puts_deletes_last_and_creates_first() -> None:
    created = snap(identity_key="zzz", content_hash="h")
    doomed = mapping(uuid.uuid4(), fixture_identity_key="aaa", external_event_id="evt")
    plan = plan_sync(subscription_id=SUB, mode=SyncMode.FULL, fixtures=[created], mappings=[doomed])
    assert plan.actions[0].type is SyncActionType.CREATE
    assert plan.actions[-1].type is SyncActionType.DELETE


def test_blocked_fixtures_become_conflicts_not_retries() -> None:
    f = snap(content_hash="changed")
    plan = plan_sync(
        subscription_id=SUB,
        mode=SyncMode.FULL,
        fixtures=[f],
        mappings=[mapping(f.id, synced_content_hash="old")],
        blocked_fixture_ids=frozenset({f.id}),
    )
    assert plan.stats.conflict == 1 and plan.stats.update == 0
    assert plan.actions[0].reason == "retry_budget_exhausted"
    assert plan.is_empty  # conflicts do not mutate the calendar


def test_max_actions_bounds_work_and_keeps_only_mutations() -> None:
    fixtures = [snap(identity_key=f"k{i}", content_hash=f"h{i}") for i in range(10)]
    plan = plan_sync(
        subscription_id=SUB, mode=SyncMode.FULL, fixtures=fixtures, mappings=[], max_actions=4
    )
    assert len(plan.actions) == 4
    assert all(a.mutates_calendar for a in plan.actions)


# --- reconcile -------------------------------------------------------------
def test_reconcile_deletes_duplicate_remote_events() -> None:
    f = snap()
    m = mapping(f.id, external_event_id="evt-b")
    remote = [
        RemoteEvent("evt-a", app_id="ident-1", content_hash="hash-1"),
        RemoteEvent("evt-b", app_id="ident-1", content_hash="hash-1"),
    ]
    plan = plan_sync(
        subscription_id=SUB,
        mode=SyncMode.RECONCILE,
        fixtures=[f],
        mappings=[m],
        remote_events=remote,
    )
    deletes = plan.of_type(SyncActionType.DELETE)
    assert len(deletes) == 1
    assert deletes[0].external_event_id == "evt-a"  # the mapped one is kept
    assert deletes[0].reason == "duplicate_remote_event"


def test_reconcile_deletes_orphaned_events() -> None:
    remote = [RemoteEvent("evt-orphan", app_id="unknown-ident", content_hash="x")]
    plan = plan_sync(
        subscription_id=SUB, mode=SyncMode.RECONCILE, fixtures=[], mappings=[], remote_events=remote
    )
    assert plan.stats.delete == 1
    assert plan.actions[0].reason == "orphaned_event"


def test_reconcile_flags_corrupted_metadata() -> None:
    remote = [RemoteEvent("evt-x", app_id=None, content_hash=None, owned=True)]
    plan = plan_sync(
        subscription_id=SUB, mode=SyncMode.RECONCILE, fixtures=[], mappings=[], remote_events=remote
    )
    assert plan.stats.reconcile == 1
    assert plan.actions[0].reason == "corrupted_metadata"


def test_reconcile_does_not_double_delete_a_pruned_orphan() -> None:
    """Pass 2 (prune) and pass 3 (orphan) must not both emit a DELETE."""
    m = mapping(uuid.uuid4(), fixture_identity_key="gone", external_event_id="evt-g")
    remote = [RemoteEvent("evt-g", app_id="gone", content_hash="h")]
    plan = plan_sync(
        subscription_id=SUB,
        mode=SyncMode.RECONCILE,
        fixtures=[],
        mappings=[m],
        remote_events=remote,
    )
    assert plan.stats.delete == 1


# --- rendering -------------------------------------------------------------
def test_render_title_and_status_prefixes() -> None:
    assert render_title(snap()) == "Arsenal vs Chelsea"
    assert render_title(snap(), prefix="[MS]") == "[MS] Arsenal vs Chelsea"
    assert render_title(snap(status=FixtureStatus.CANCELLED)).startswith("CANCELLED:")
    assert render_title(snap(status=FixtureStatus.POSTPONED)).startswith("POSTPONED:")
    assert render_title(snap(home_name=None, away_name=None)) == "Premier League"


def test_render_event_is_deterministic_and_carries_metadata() -> None:
    f = snap()
    a = render_event(f, include_event_id=True)
    b = render_event(f, include_event_id=True)
    assert a == b  # byte-identical bodies => reliable no-op signal
    assert a.event_id == derive_event_id(f.identity_key)

    meta = EventMetadata.from_properties(a.metadata)
    assert meta is not None
    assert meta.app_id == f.identity_key
    assert meta.content_hash == f.content_hash
    assert meta.source == "football"


def test_render_event_id_only_on_create() -> None:
    assert render_event(snap()).event_id is None


def test_render_event_defaults_duration_when_no_end() -> None:
    event = render_event(snap())
    assert event.when.end - event.when.start == timedelta(hours=2)
    explicit = render_event(snap(scheduled_end=START + timedelta(minutes=90)))
    assert explicit.when.end - explicit.when.start == timedelta(minutes=90)


@pytest.mark.parametrize("kind", list(ChangeKind))
def test_every_change_kind_maps_to_an_action(kind) -> None:
    from app.domain.sync.planner import _KIND_TO_ACTION

    assert kind in _KIND_TO_ACTION


def test_verdict_is_a_value_object() -> None:
    assert Verdict(ChangeKind.NO_CHANGE, "x") == Verdict(ChangeKind.NO_CHANGE, "x")
