"""The diff engine.

A **pure function** of `(fixture, mapping, previous_snapshot, remote_event)` that
returns exactly one verdict. Every rule below is total and mutually exclusive, so
the same inputs always yield the same verdict — this is the foundation of I4
(determinism) and, via NO_CHANGE being a fixed point, of idempotency (I5).

Rules, evaluated in order (first match wins):

1. **Fixture gone** (soft-deleted, or status DELETED)
   → `DELETE` if a mapping exists, else `NO_CHANGE`. We never create an event
   for a fixture that no longer exists.

2. **No mapping** → `CREATE`. (Unless the fixture is gone; see rule 1.)

3. **Mapping unconfirmed** (`external_event_id IS NULL`) → `RECREATE`. A previous
   create never landed. Retrying with the deterministic event id is safe: if the
   event actually exists, the provider rejects the duplicate and we repair.

4. **Remote event missing** (reconcile only: we hold an id, the provider doesn't)
   → `RECREATE`. Someone deleted it outside MatchSync.

5. **User edited the event** (remote `ms_hash` ≠ our `synced_content_hash`)
   → `CONFLICT` under `USER_WINS`; under `FIXTURE_WINS` it degrades to the normal
   rules below, so a user edit is only overwritten when the *fixture* actually
   changed. An unchanged fixture never clobbers a manual edit.

6. **Content hash equal** → `NO_CHANGE`. The O(1) fixed point. No API call.

7. **Fixture cancelled** → `CANCEL` (annotate) or `DELETE`, per policy.

8. **Otherwise** → `MINOR_UPDATE` or `MAJOR_UPDATE`, decided by *which* fields
   changed. Both issue one PATCH; the distinction drives observability and
   priority, not the API call count.

**Why we can name the changed fields without storing them.** The mapping records
only the hash we last pushed. Stage 7's `fixture_versions` table stores a snapshot
per content hash, so we look up the snapshot whose hash equals
`synced_content_hash` and diff it against the current fixture. If that version was
pruned we fall back to `MAJOR_UPDATE`, which is the safe over-approximation (a
full PATCH).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.sync.models import (
    CancelledPolicy,
    ChangeKind,
    ConflictPolicy,
    EventMapping,
    FixtureSnapshot,
    RemoteEvent,
)

# Fields that only affect the event's description text.
MINOR_FIELDS = frozenset({"venue", "round", "stage"})
# Fields that affect when the event happens or whether it is on.
MAJOR_FIELDS = frozenset({"scheduled_start", "scheduled_end", "status", "participants"})


@dataclass(frozen=True)
class Verdict:
    kind: ChangeKind
    reason: str
    changed_fields: tuple[str, ...] = ()


def _as_utc(moment: datetime | None) -> datetime | None:
    """Naive datetimes are UTC by storage contract, never server-local."""
    if moment is None:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def changed_fields(previous: dict[str, object] | None, fixture: FixtureSnapshot) -> tuple[str, ...]:
    """Diff a stored version snapshot against the current fixture.

    ``previous`` is a ``fixture_versions.snapshot`` payload. Returns () when the
    snapshot is unavailable — callers treat that as "assume everything changed".
    """
    if not previous:
        return ()

    changed: list[str] = []

    def _dt(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return _as_utc(datetime.fromisoformat(value))
        except ValueError:
            return None

    if _dt(previous.get("scheduled_start")) != _as_utc(fixture.scheduled_start):
        changed.append("scheduled_start")
    if _dt(previous.get("scheduled_end")) != _as_utc(fixture.scheduled_end):
        changed.append("scheduled_end")
    if previous.get("status") != fixture.status.value:
        changed.append("status")
    if (previous.get("venue") or None) != (fixture.venue or None):
        changed.append("venue")
    if (previous.get("round") or None) != (fixture.round or None):
        changed.append("round")
    if (previous.get("stage") or None) != (fixture.stage or None):
        changed.append("stage")

    return tuple(sorted(changed))


def _classify_update(fields: tuple[str, ...]) -> ChangeKind:
    if not fields:
        # Hash changed but we cannot name the fields (snapshot pruned, or a field
        # we don't diff, e.g. participants). Over-approximate: full PATCH.
        return ChangeKind.MAJOR_UPDATE
    if set(fields) & MAJOR_FIELDS:
        return ChangeKind.MAJOR_UPDATE
    if set(fields) <= MINOR_FIELDS:
        return ChangeKind.MINOR_UPDATE
    return ChangeKind.MAJOR_UPDATE


def diff(
    fixture: FixtureSnapshot,
    mapping: EventMapping | None,
    *,
    previous_snapshot: dict[str, object] | None = None,
    remote: RemoteEvent | None = None,
    remote_known: bool = False,
    conflict_policy: ConflictPolicy = ConflictPolicy.FIXTURE_WINS,
    cancelled_policy: CancelledPolicy = CancelledPolicy.ANNOTATE,
) -> Verdict:
    """Return the single verdict for one sync unit. Total and deterministic.

    ``remote_known`` distinguishes "we looked at the provider and found nothing"
    (reconcile) from "we did not look" (fast path). Only the former may conclude
    that a remote event is missing.
    """
    # 1. The fixture no longer exists.
    if fixture.is_gone:
        if mapping is not None and mapping.is_active and mapping.is_confirmed:
            return Verdict(ChangeKind.DELETE, "fixture_deleted")
        return Verdict(ChangeKind.NO_CHANGE, "fixture_deleted_no_event")

    # 2. Nothing has ever been created for this fixture.
    if mapping is None or mapping.is_deleted:
        return Verdict(ChangeKind.CREATE, "no_mapping")

    # 3. A previous create never confirmed.
    if not mapping.is_confirmed:
        return Verdict(ChangeKind.RECREATE, "mapping_unconfirmed")

    # 4. The event was deleted outside MatchSync (only knowable in reconcile).
    if remote_known and remote is None:
        return Verdict(ChangeKind.RECREATE, "remote_event_missing")

    # 5. The user edited the event by hand.
    user_edited = (
        remote is not None
        and remote.content_hash is not None
        and mapping.synced_content_hash is not None
        and remote.content_hash != mapping.synced_content_hash
    )
    if user_edited and conflict_policy is ConflictPolicy.USER_WINS:
        return Verdict(ChangeKind.CONFLICT, "user_modified_event")

    # 6. The fixed point: nothing changed. Zero API calls.
    if mapping.synced_content_hash == fixture.content_hash:
        # A cancelled fixture whose mapping is not yet marked cancelled still
        # needs its state reconciled, even though the hash matches.
        if fixture.is_cancelled and mapping.state.value != "cancelled":
            return Verdict(ChangeKind.CANCEL, "fixture_cancelled")
        return Verdict(ChangeKind.NO_CHANGE, "content_hash_match")

    fields = changed_fields(previous_snapshot, fixture)

    # 7. Cancellation.
    if fixture.is_cancelled:
        if cancelled_policy is CancelledPolicy.DELETE:
            return Verdict(ChangeKind.DELETE, "fixture_cancelled", fields)
        return Verdict(ChangeKind.CANCEL, "fixture_cancelled", fields)

    # 8. A real content change.
    reason = "user_modified_event_overwritten" if user_edited else "content_changed"
    return Verdict(_classify_update(fields), reason, fields)
