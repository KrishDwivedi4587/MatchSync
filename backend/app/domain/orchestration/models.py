"""Job model and state machine (pure).

A **Job** is one unit of orchestrated work. It is *not* the work itself: a sync
job merely says "run the synchronization engine for subscription X". The engine
remains the sole owner of synchronization logic.

State machine
-------------

    PENDING ──► QUEUED ──► RUNNING ──┬──► SUCCEEDED        (terminal)
       │          │          │       ├──► SKIPPED          (terminal: lock held)
       │          │          │       ├──► FAILED ──┐
       │          │          │       └──► RETRYING ┤
       │          │          │                     │
       │          │          └──► CANCELLED        │       (terminal)
       │          └──► CANCELLED                   │
       └──► CANCELLED                              │
                                                   ▼
                              RETRYING ──► QUEUED (backoff elapsed)
                              RETRYING ──► DEAD_LETTER  (attempts exhausted)
                              DEAD_LETTER ──► QUEUED    (manual retry only)

``SKIPPED`` is a *success* in orchestration terms: another worker already holds
the subscription's lock, so this duplicate delivery correctly did nothing. It is
never retried.

Transitions are validated, so an illegal move (e.g. SUCCEEDED → RUNNING) raises
rather than silently corrupting a job's history.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any


class JobType(StrEnum):
    SYNC_SUBSCRIPTION = "sync_subscription"
    SYNC_USER = "sync_user"
    RECONCILE = "reconcile"
    FIXTURE_IMPORT = "fixture_import"
    METADATA_REFRESH = "metadata_refresh"
    CLEANUP = "cleanup"
    HEALTH_CHECK = "health_check"


class JobState(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"  # a concurrent worker held the lock; correct no-op
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


class JobPriority(IntEnum):
    """Higher number = higher priority. Manual work preempts scheduled work."""

    LOW = 1  # maintenance, cleanup
    NORMAL = 5  # scheduled syncs
    HIGH = 9  # user-triggered "sync now"


class Queue(StrEnum):
    """Named queues. Separation lets a slow ingest never starve a manual sync."""

    SYNC_HIGH = "sync.high"
    SYNC_DEFAULT = "sync.default"
    INGEST = "ingest"
    MAINTENANCE = "maintenance"
    DEAD_LETTER = "dead_letter"


TERMINAL_STATES = frozenset({JobState.SUCCEEDED, JobState.SKIPPED, JobState.CANCELLED})

# The only legal moves. Everything else raises.
_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.PENDING: frozenset({JobState.QUEUED, JobState.CANCELLED}),
    # QUEUED -> FAILED covers jobs that die before they start: no registered
    # handler, an unreadable payload, a vanished owner. They never ran, so they
    # must not be recorded as an attempt.
    JobState.QUEUED: frozenset(
        {JobState.RUNNING, JobState.CANCELLED, JobState.SKIPPED, JobState.FAILED}
    ),
    JobState.RUNNING: frozenset(
        {
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.RETRYING,
            JobState.SKIPPED,
            JobState.CANCELLED,
        }
    ),
    JobState.RETRYING: frozenset({JobState.QUEUED, JobState.DEAD_LETTER, JobState.CANCELLED}),
    JobState.FAILED: frozenset({JobState.RETRYING, JobState.DEAD_LETTER, JobState.QUEUED}),
    JobState.DEAD_LETTER: frozenset({JobState.QUEUED}),  # manual retry only
    JobState.SUCCEEDED: frozenset(),
    JobState.SKIPPED: frozenset(),
    JobState.CANCELLED: frozenset(),
}


class InvalidTransitionError(ValueError):
    def __init__(self, current: JobState, target: JobState) -> None:
        super().__init__(f"Illegal job transition {current.value} -> {target.value}")
        self.current = current
        self.target = target


def can_transition(current: JobState, target: JobState) -> bool:
    return target in _TRANSITIONS[current]


def assert_transition(current: JobState, target: JobState) -> None:
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)


@dataclass
class Job:
    """One orchestrated unit of work."""

    type: JobType
    payload: dict[str, Any] = field(default_factory=dict)
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    priority: JobPriority = JobPriority.NORMAL
    state: JobState = JobState.PENDING
    attempts: int = 0
    max_attempts: int = 5
    user_id: uuid.UUID | None = None
    celery_task_id: str | None = None
    error: str | None = None
    error_code: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    next_retry_at: datetime | None = None

    # --- derived ---------------------------------------------------------
    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES or self.state is JobState.DEAD_LETTER

    @property
    def queue(self) -> Queue:
        """Which queue this job belongs on."""
        if self.type in (JobType.SYNC_SUBSCRIPTION, JobType.SYNC_USER, JobType.RECONCILE):
            return Queue.SYNC_HIGH if self.priority >= JobPriority.HIGH else Queue.SYNC_DEFAULT
        if self.type is JobType.FIXTURE_IMPORT:
            return Queue.INGEST
        return Queue.MAINTENANCE

    @property
    def lock_key(self) -> str | None:
        """The mutual-exclusion key, or None when concurrency is harmless.

        SYNC_SUBSCRIPTION and RECONCILE deliberately share a key: they both mutate
        the same subscription's calendar events, so at most one may run.
        """
        if self.type in (JobType.SYNC_SUBSCRIPTION, JobType.RECONCILE):
            return f"sync:subscription:{self.payload.get('subscription_id')}"
        if self.type is JobType.SYNC_USER:
            return f"sync:user:{self.user_id}"
        if self.type is JobType.METADATA_REFRESH:
            return "maintenance:metadata"
        if self.type is JobType.FIXTURE_IMPORT:
            return f"ingest:{self.payload.get('sport') or 'all'}"
        return None  # cleanup / health check: concurrent runs are harmless

    @property
    def queue_latency_seconds(self) -> float | None:
        if self.queued_at and self.started_at:
            return (self.started_at - self.queued_at).total_seconds()
        return None

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    # --- transitions -----------------------------------------------------
    def transition(self, target: JobState, *, now: datetime | None = None) -> None:
        """Move to ``target``, stamping the relevant timestamp. Validates."""
        assert_transition(self.state, target)
        moment = now or datetime.now(UTC)

        if target is JobState.QUEUED:
            self.queued_at = moment
            self.next_retry_at = None
        elif target is JobState.RUNNING:
            self.started_at = moment
            self.attempts += 1
        elif target in (
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.SKIPPED,
            JobState.CANCELLED,
            JobState.DEAD_LETTER,
        ):
            self.finished_at = moment

        self.state = target

    def to_dict(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "id": str(self.id),
            "type": self.type.value,
            "state": self.state.value,
            "priority": int(self.priority),
            "payload": self.payload,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "user_id": str(self.user_id) if self.user_id else None,
            "celery_task_id": self.celery_task_id,
            "error": self.error,
            "error_code": self.error_code,
            "created_at": iso(self.created_at),
            "queued_at": iso(self.queued_at),
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "next_retry_at": iso(self.next_retry_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Job:
        def dt(value: str | None) -> datetime | None:
            return datetime.fromisoformat(value) if value else None

        return cls(
            id=uuid.UUID(data["id"]),
            type=JobType(data["type"]),
            state=JobState(data["state"]),
            priority=JobPriority(int(data["priority"])),
            payload=data.get("payload") or {},
            attempts=int(data.get("attempts", 0)),
            max_attempts=int(data.get("max_attempts", 5)),
            user_id=uuid.UUID(data["user_id"]) if data.get("user_id") else None,
            celery_task_id=data.get("celery_task_id"),
            error=data.get("error"),
            error_code=data.get("error_code"),
            created_at=dt(data.get("created_at")) or datetime.now(UTC),
            queued_at=dt(data.get("queued_at")),
            started_at=dt(data.get("started_at")),
            finished_at=dt(data.get("finished_at")),
            next_retry_at=dt(data.get("next_retry_at")),
        )
