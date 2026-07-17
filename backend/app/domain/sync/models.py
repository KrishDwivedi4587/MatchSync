"""Synchronization domain models (pure).

The engine's vocabulary. Nothing here knows about SQLAlchemy, Google, or HTTP —
these are the plain values the planner and diff engine reason over, so both are
trivially unit-testable and provably deterministic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.domain.value_objects.enums import CalendarEventState, FixtureStatus


class SyncMode(StrEnum):
    """How much work a run considers."""

    INCREMENTAL = "incremental"  # only fixtures changed since the watermark
    FULL = "full"  # every fixture in scope + window (no remote read)
    RECONCILE = "reconcile"  # FULL + read remote events to repair drift


class SyncActionType(StrEnum):
    """What the planner decided. Nothing here executes."""

    CREATE = "create"
    UPDATE = "update"
    CANCEL = "cancel"  # fixture cancelled; annotate rather than destroy
    DELETE = "delete"
    RECREATE = "recreate"  # mapping exists but the remote event is gone
    NO_OP = "no_op"
    RECONCILE = "reconcile"  # remote drift to repair (metadata, duplicates)
    CONFLICT = "conflict"  # user edited the event; policy decides


class ChangeKind(StrEnum):
    """Diff verdict for one sync unit."""

    NO_CHANGE = "no_change"
    MINOR_UPDATE = "minor_update"  # description-only fields (venue/round/stage)
    MAJOR_UPDATE = "major_update"  # time / status / participants
    CREATE = "create"
    CANCEL = "cancel"
    DELETE = "delete"
    RECREATE = "recreate"
    CONFLICT = "conflict"


class ConflictPolicy(StrEnum):
    """What to do when a user has manually edited a MatchSync-owned event."""

    FIXTURE_WINS = "fixture_wins"  # overwrite on the next real fixture change
    USER_WINS = "user_wins"  # never overwrite; record a conflict


class CancelledPolicy(StrEnum):
    """What to do when a fixture is cancelled."""

    ANNOTATE = "annotate"  # keep the event, mark it cancelled (users want to know)
    DELETE = "delete"  # remove the event entirely


# Deterministic ordering: actions are sorted by (rank, identity_key). Deletions
# run last so a delete never races a create for the same deterministic event id.
_ACTION_RANK: dict[SyncActionType, int] = {
    SyncActionType.CREATE: 0,
    SyncActionType.RECREATE: 1,
    SyncActionType.UPDATE: 2,
    SyncActionType.CANCEL: 3,
    SyncActionType.RECONCILE: 4,
    SyncActionType.CONFLICT: 5,
    SyncActionType.DELETE: 6,
    SyncActionType.NO_OP: 7,
}


def action_rank(action_type: SyncActionType) -> int:
    return _ACTION_RANK[action_type]


@dataclass(frozen=True)
class FixtureSnapshot:
    """A persisted fixture, reduced to what synchronization needs."""

    id: uuid.UUID
    identity_key: str
    content_hash: str
    version: int
    sport_key: str
    competition_name: str
    scheduled_start: datetime
    status: FixtureStatus
    scheduled_end: datetime | None = None
    venue: str | None = None
    round: str | None = None
    stage: str | None = None
    home_name: str | None = None
    away_name: str | None = None
    is_deleted: bool = False

    @property
    def is_gone(self) -> bool:
        """The fixture no longer exists as a real event."""
        return self.is_deleted or self.status is FixtureStatus.DELETED

    @property
    def is_cancelled(self) -> bool:
        return self.status is FixtureStatus.CANCELLED


@dataclass(frozen=True)
class EventMapping:
    """A ``calendar_events`` row, reduced to what synchronization needs."""

    id: uuid.UUID
    fixture_id: uuid.UUID
    fixture_identity_key: str
    state: CalendarEventState
    external_event_id: str | None = None
    synced_content_hash: str | None = None
    is_deleted: bool = False

    @property
    def is_active(self) -> bool:
        return self.state is CalendarEventState.ACTIVE and not self.is_deleted

    @property
    def is_confirmed(self) -> bool:
        """A create actually landed at the provider."""
        return self.external_event_id is not None


@dataclass(frozen=True)
class RemoteEvent:
    """A MatchSync-owned event as it currently exists at the provider."""

    event_id: str
    app_id: str | None  # ms_id == fixture identity key
    content_hash: str | None  # ms_hash == what we last pushed
    owned: bool = True


@dataclass(frozen=True)
class SyncAction:
    """One planned operation. Immutable, comparable, serializable."""

    type: SyncActionType
    identity_key: str
    reason: str
    fixture_id: uuid.UUID | None = None
    mapping_id: uuid.UUID | None = None
    external_event_id: str | None = None
    changed_fields: tuple[str, ...] = ()
    change_kind: ChangeKind | None = None

    @property
    def mutates_calendar(self) -> bool:
        return self.type not in (SyncActionType.NO_OP, SyncActionType.CONFLICT)

    @property
    def sort_key(self) -> tuple[int, str, str]:
        # identity_key breaks ties; the action type disambiguates the rest, so
        # the ordering is total (no two actions of the same type share a key).
        return (action_rank(self.type), self.identity_key, self.type.value)


@dataclass(frozen=True)
class PlanStats:
    create: int = 0
    recreate: int = 0
    update: int = 0
    cancel: int = 0
    delete: int = 0
    reconcile: int = 0
    conflict: int = 0
    no_op: int = 0

    @property
    def total(self) -> int:
        return (
            self.create
            + self.recreate
            + self.update
            + self.cancel
            + self.delete
            + self.reconcile
            + self.conflict
            + self.no_op
        )

    @property
    def mutations(self) -> int:
        return self.create + self.recreate + self.update + self.cancel + self.delete

    @property
    def no_op_ratio(self) -> float:
        return self.no_op / self.total if self.total else 1.0

    def as_dict(self) -> dict[str, int | float]:
        return {
            "create": self.create,
            "recreate": self.recreate,
            "update": self.update,
            "cancel": self.cancel,
            "delete": self.delete,
            "reconcile": self.reconcile,
            "conflict": self.conflict,
            "no_op": self.no_op,
            "total": self.total,
            "mutations": self.mutations,
            "no_op_ratio": round(self.no_op_ratio, 4),
        }


@dataclass(frozen=True)
class SyncPlan:
    """A deterministic, ordered set of actions. Executing it is a separate step."""

    subscription_id: uuid.UUID
    mode: SyncMode
    actions: tuple[SyncAction, ...] = ()
    stats: PlanStats = field(default_factory=PlanStats)

    @property
    def is_empty(self) -> bool:
        """No calendar mutation is required (no-ops and conflicts don't mutate)."""
        return self.stats.mutations == 0

    def of_type(self, *types: SyncActionType) -> tuple[SyncAction, ...]:
        return tuple(a for a in self.actions if a.type in types)
