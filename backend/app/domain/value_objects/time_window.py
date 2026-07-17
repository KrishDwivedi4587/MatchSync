"""TimeWindow value object (Stage 1, domain/value_objects).

A half-open UTC interval ``[start, end)`` used to bound fixture queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("TimeWindow end must be after start.")

    @classmethod
    def next_days(cls, days: int, *, now: datetime | None = None) -> TimeWindow:
        anchor = now or datetime.now(UTC)
        return cls(start=anchor, end=anchor + timedelta(days=days))

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end
