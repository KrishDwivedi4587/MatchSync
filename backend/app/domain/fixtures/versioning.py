"""Fixture version detection.

Every persisted change writes a ``fixture_versions`` row: the new version number,
*what* changed (``changed_fields``), *why* (``change_type``), and a snapshot of
the resulting state. History is append-only; fixtures themselves are never
destroyed.

Change classification is derived from the status transition, because no provider
reports "abandoned" and the frozen ``FixtureStatus`` enum has no such member:

    LIVE      -> CANCELLED            = ABANDONED (stopped mid-play)
    *         -> CANCELLED            = CANCELLED
    *         -> POSTPONED            = POSTPONED
    CANCELLED/POSTPONED/DELETED -> SCHEDULED/LIVE = RESTORED
    otherwise, with any field changed  = UPDATED

``FixtureState`` is deliberately shaped like the persisted row (resolved UUIDs,
not provider ids) so the diff compares apples to apples with what is stored.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from app.domain.value_objects.enums import FixtureChangeType, FixtureStatus


class FixtureField(StrEnum):
    """Fields whose change is worth versioning. Stored as JSON strings."""

    SCHEDULED_START = "scheduled_start"
    SCHEDULED_END = "scheduled_end"
    STATUS = "status"
    VENUE = "venue"
    COMPETITION = "competition"
    PARTICIPANTS = "participants"
    ROUND = "round"
    STAGE = "stage"


_ACTIVE = (FixtureStatus.SCHEDULED, FixtureStatus.LIVE)
_INACTIVE = (FixtureStatus.CANCELLED, FixtureStatus.POSTPONED, FixtureStatus.DELETED)


@dataclass(frozen=True)
class FixtureState:
    """A fixture's versionable state, in persistence terms."""

    competition_id: uuid.UUID
    scheduled_start: datetime
    status: FixtureStatus
    scheduled_end: datetime | None = None
    venue: str | None = None
    round: str | None = None
    stage: str | None = None
    home_team_id: uuid.UUID | None = None
    away_team_id: uuid.UUID | None = None

    @property
    def participant_ids(self) -> frozenset[uuid.UUID]:
        return frozenset(i for i in (self.home_team_id, self.away_team_id) if i is not None)

    def to_snapshot(self) -> dict[str, object]:
        """JSON-safe snapshot stored on the version row for audit/history."""
        return {
            "competition_id": str(self.competition_id),
            "scheduled_start": _as_utc(self.scheduled_start).isoformat(),
            "scheduled_end": (
                _as_utc(self.scheduled_end).isoformat() if self.scheduled_end else None
            ),
            "status": self.status.value,
            "venue": self.venue,
            "round": self.round,
            "stage": self.stage,
            "home_team_id": str(self.home_team_id) if self.home_team_id else None,
            "away_team_id": str(self.away_team_id) if self.away_team_id else None,
        }


def _as_utc(moment: datetime) -> datetime:
    """Coerce to aware UTC.

    Naive input is assumed UTC (the storage contract), NOT local time. Calling
    ``astimezone()`` on a naive datetime would silently apply the server's
    timezone and report phantom time changes on every SQLite round-trip.
    """
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _same_instant(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return a is b
    return _as_utc(a) == _as_utc(b)


def diff_states(old: FixtureState, new: FixtureState) -> set[FixtureField]:
    """Which versionable fields differ between two states."""
    changed: set[FixtureField] = set()

    if not _same_instant(old.scheduled_start, new.scheduled_start):
        changed.add(FixtureField.SCHEDULED_START)
    if not _same_instant(old.scheduled_end, new.scheduled_end):
        changed.add(FixtureField.SCHEDULED_END)
    if old.status is not new.status:
        changed.add(FixtureField.STATUS)
    if (old.venue or None) != (new.venue or None):
        changed.add(FixtureField.VENUE)
    if (old.round or None) != (new.round or None):
        changed.add(FixtureField.ROUND)
    if (old.stage or None) != (new.stage or None):
        changed.add(FixtureField.STAGE)
    if old.competition_id != new.competition_id:
        changed.add(FixtureField.COMPETITION)
    if old.participant_ids != new.participant_ids:
        changed.add(FixtureField.PARTICIPANTS)

    return changed


def classify_change(
    old_status: FixtureStatus,
    new_status: FixtureStatus,
    changed_fields: set[FixtureField],
    *,
    was_deleted: bool = False,
) -> FixtureChangeType:
    """Derive why the fixture changed. See the module docstring for the rules."""
    if new_status is FixtureStatus.CANCELLED:
        return (
            FixtureChangeType.ABANDONED
            if old_status is FixtureStatus.LIVE
            else FixtureChangeType.CANCELLED
        )
    if new_status is FixtureStatus.POSTPONED:
        return FixtureChangeType.POSTPONED
    if new_status in _ACTIVE and (was_deleted or old_status in _INACTIVE):
        return FixtureChangeType.RESTORED
    return FixtureChangeType.UPDATED
