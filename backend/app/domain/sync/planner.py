"""The synchronization planner.

Builds a deterministic, ordered ``SyncPlan``. **Nothing executes here.** The
planner is a pure function of its inputs, which is what makes the plan
previewable (`GET /sync/plan` performs zero writes and zero API calls) and what
makes property-based determinism testing possible.

Three passes:

1. **Fixtures** — one verdict per in-scope fixture (see ``diff``).
2. **Prune** — mappings whose fixture is no longer in scope/window get DELETE.
   This is how "fixture fell out of the sync window" is handled without ever
   scanning the whole database.
3. **Reconcile** (optional) — compare against the remote event list to repair
   drift: duplicate owned events, orphans, and corrupted metadata.

Ordering is total: `(action_rank, identity_key, action_type)`. Deletes run last
so a delete can never race a create for the same deterministic event id.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from app.domain.sync.diff import diff
from app.domain.sync.models import (
    CancelledPolicy,
    ChangeKind,
    ConflictPolicy,
    EventMapping,
    FixtureSnapshot,
    PlanStats,
    RemoteEvent,
    SyncAction,
    SyncActionType,
    SyncMode,
    SyncPlan,
)

_KIND_TO_ACTION: dict[ChangeKind, SyncActionType] = {
    ChangeKind.CREATE: SyncActionType.CREATE,
    ChangeKind.RECREATE: SyncActionType.RECREATE,
    ChangeKind.MINOR_UPDATE: SyncActionType.UPDATE,
    ChangeKind.MAJOR_UPDATE: SyncActionType.UPDATE,
    ChangeKind.CANCEL: SyncActionType.CANCEL,
    ChangeKind.DELETE: SyncActionType.DELETE,
    ChangeKind.CONFLICT: SyncActionType.CONFLICT,
    ChangeKind.NO_CHANGE: SyncActionType.NO_OP,
}


def _stats(actions: tuple[SyncAction, ...]) -> PlanStats:
    counts: dict[SyncActionType, int] = defaultdict(int)
    for action in actions:
        counts[action.type] += 1
    return PlanStats(
        create=counts[SyncActionType.CREATE],
        recreate=counts[SyncActionType.RECREATE],
        update=counts[SyncActionType.UPDATE],
        cancel=counts[SyncActionType.CANCEL],
        delete=counts[SyncActionType.DELETE],
        reconcile=counts[SyncActionType.RECONCILE],
        conflict=counts[SyncActionType.CONFLICT],
        no_op=counts[SyncActionType.NO_OP],
    )


def plan_sync(
    *,
    subscription_id: uuid.UUID,
    mode: SyncMode,
    fixtures: list[FixtureSnapshot],
    mappings: list[EventMapping],
    in_scope_fixture_ids: frozenset[uuid.UUID] | None = None,
    previous_snapshots: dict[uuid.UUID, dict[str, object]] | None = None,
    remote_events: list[RemoteEvent] | None = None,
    blocked_fixture_ids: frozenset[uuid.UUID] = frozenset(),
    conflict_policy: ConflictPolicy = ConflictPolicy.FIXTURE_WINS,
    cancelled_policy: CancelledPolicy = CancelledPolicy.ANNOTATE,
    max_actions: int | None = None,
) -> SyncPlan:
    """Produce a deterministic plan. Pure: no I/O, no clock, no randomness.

    ``blocked_fixture_ids`` are units that exceeded the retry budget; they are
    emitted as CONFLICT (a dead-letter marker) rather than retried forever.

    ``in_scope_fixture_ids`` is the set of fixtures that *still belong* to this
    subscription's scope and window. It is deliberately distinct from
    ``fixtures``: in INCREMENTAL mode only *changed* fixtures are loaded, so
    "absent from ``fixtures``" must never be read as "no longer in scope" — that
    would delete the calendar event of every unchanged fixture. Defaults to the
    loaded set, which is correct only when the caller loaded everything (FULL).
    """
    snapshots = previous_snapshots or {}
    remote_known = mode is SyncMode.RECONCILE
    by_fixture = {m.fixture_id: m for m in mappings}
    remote_by_app_id: dict[str, list[RemoteEvent]] = defaultdict(list)
    for event in remote_events or []:
        if event.app_id:
            remote_by_app_id[event.app_id].append(event)

    actions: list[SyncAction] = []
    seen_fixture_ids: set[uuid.UUID] = set()
    seen_identity_keys: set[str] = set()

    # --- pass 1: fixtures in scope ----------------------------------------
    for fixture in fixtures:
        seen_fixture_ids.add(fixture.id)
        seen_identity_keys.add(fixture.identity_key)
        mapping = by_fixture.get(fixture.id)

        if fixture.id in blocked_fixture_ids:
            actions.append(
                SyncAction(
                    type=SyncActionType.CONFLICT,
                    identity_key=fixture.identity_key,
                    reason="retry_budget_exhausted",
                    fixture_id=fixture.id,
                    mapping_id=mapping.id if mapping else None,
                    external_event_id=mapping.external_event_id if mapping else None,
                )
            )
            continue

        remote = None
        if remote_known and fixture.identity_key in remote_by_app_id:
            # The canonical remote event is the one our mapping points at, else
            # the first by id — deterministic either way.
            candidates = sorted(remote_by_app_id[fixture.identity_key], key=lambda e: e.event_id)
            remote = next(
                (e for e in candidates if mapping and e.event_id == mapping.external_event_id),
                candidates[0],
            )

        verdict = diff(
            fixture,
            mapping,
            previous_snapshot=snapshots.get(fixture.id),
            remote=remote,
            remote_known=remote_known and mapping is not None and mapping.is_confirmed,
            conflict_policy=conflict_policy,
            cancelled_policy=cancelled_policy,
        )

        actions.append(
            SyncAction(
                type=_KIND_TO_ACTION[verdict.kind],
                identity_key=fixture.identity_key,
                reason=verdict.reason,
                fixture_id=fixture.id,
                mapping_id=mapping.id if mapping else None,
                external_event_id=mapping.external_event_id if mapping else None,
                changed_fields=verdict.changed_fields,
                change_kind=verdict.kind,
            )
        )

    # --- pass 2: prune mappings whose fixture left the scope/window --------
    # Membership is decided by the authoritative scope set, never by whether the
    # fixture happened to be loaded this run.
    scope = (
        in_scope_fixture_ids if in_scope_fixture_ids is not None else frozenset(seen_fixture_ids)
    )
    for mapping in mappings:
        if mapping.fixture_id in scope or mapping.fixture_id in seen_fixture_ids:
            continue
        if not mapping.is_active or not mapping.is_confirmed:
            continue  # nothing exists remotely; nothing to delete
        actions.append(
            SyncAction(
                type=SyncActionType.DELETE,
                identity_key=mapping.fixture_identity_key,
                reason="fixture_out_of_scope",
                fixture_id=mapping.fixture_id,
                mapping_id=mapping.id,
                external_event_id=mapping.external_event_id,
            )
        )

    # --- pass 3: reconcile against the provider ----------------------------
    if remote_known:
        actions.extend(
            _reconcile(remote_by_app_id, by_fixture, seen_identity_keys, remote_events or [])
        )

    ordered = tuple(sorted(_dedupe(actions), key=lambda a: a.sort_key))
    if max_actions is not None and len(ordered) > max_actions:
        # Bounded work per run; the remainder is picked up next run. Because the
        # order is deterministic, progress is guaranteed (no starvation cycles).
        mutating = tuple(a for a in ordered if a.mutates_calendar)[:max_actions]
        ordered = tuple(sorted(mutating, key=lambda a: a.sort_key))

    return SyncPlan(
        subscription_id=subscription_id, mode=mode, actions=ordered, stats=_stats(ordered)
    )


def _dedupe(actions: list[SyncAction]) -> list[SyncAction]:
    """Collapse actions targeting the same thing.

    A mapping pruned in pass 2 is also seen as an orphan in pass 3, which would
    emit two DELETEs for one event. Deleting twice is harmless (the calendar
    platform treats 404 as success) but it would double-count metrics and waste
    an API call. First occurrence wins; passes run in a fixed order, so this is
    deterministic.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[SyncAction] = []
    for action in actions:
        # A remote event can only be acted on once, whatever led us there. Keying
        # on the event id (when known) is what collapses a pruned mapping and the
        # orphan scan finding the same event. Falling back to fixture_id covers
        # actions that have no remote event yet (CREATE).
        target = action.external_event_id or (
            str(action.fixture_id) if action.fixture_id else action.identity_key
        )
        key = (action.type.value, target)
        if key in seen:
            continue
        seen.add(key)
        unique.append(action)
    return unique


def _reconcile(
    remote_by_app_id: dict[str, list[RemoteEvent]],
    mappings_by_fixture: dict[uuid.UUID, EventMapping],
    seen_identity_keys: set[str],
    remote_events: list[RemoteEvent],
) -> list[SyncAction]:
    """Repair remote drift: duplicates, orphans, corrupted metadata."""
    actions: list[SyncAction] = []
    kept_by_mapping = {
        m.external_event_id for m in mappings_by_fixture.values() if m.external_event_id
    }

    # Duplicate owned events sharing one app id: keep the mapped one (else the
    # lowest event id), delete the rest. This is invariant I2 being enforced.
    for app_id, events in remote_by_app_id.items():
        if len(events) < 2:
            continue
        ordered = sorted(events, key=lambda e: e.event_id)
        keeper = next((e for e in ordered if e.event_id in kept_by_mapping), ordered[0])
        for event in ordered:
            if event.event_id == keeper.event_id:
                continue
            actions.append(
                SyncAction(
                    type=SyncActionType.DELETE,
                    identity_key=app_id,
                    reason="duplicate_remote_event",
                    external_event_id=event.event_id,
                )
            )

    # Orphans: our event, but no fixture claims it (invariant I3).
    for event in remote_events:
        if not event.owned:
            continue
        if event.app_id is None:
            # Corrupted metadata: ours by marker, but unidentifiable.
            actions.append(
                SyncAction(
                    type=SyncActionType.RECONCILE,
                    identity_key=event.event_id,
                    reason="corrupted_metadata",
                    external_event_id=event.event_id,
                )
            )
        elif event.app_id not in seen_identity_keys:
            actions.append(
                SyncAction(
                    type=SyncActionType.DELETE,
                    identity_key=event.app_id,
                    reason="orphaned_event",
                    external_event_id=event.event_id,
                )
            )

    return actions
