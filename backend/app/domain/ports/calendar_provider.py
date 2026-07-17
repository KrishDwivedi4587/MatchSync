"""Calendar-provider port.

The contract every calendar backend implements (Google today; Apple, Outlook,
CalDAV, ICS later). Mirrors the ``IdentityProvider`` abstraction from Stage 4.

Nothing here mentions Google. The application layer depends only on these
dataclasses and this Protocol, so swapping providers changes no business logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.domain.value_objects.enums import CalendarAccessRole


@dataclass(frozen=True)
class CalendarInfo:
    """A calendar as reported by the provider."""

    external_id: str
    summary: str
    access_role: CalendarAccessRole
    is_primary: bool = False
    description: str | None = None
    time_zone: str | None = None


@dataclass(frozen=True)
class EventTime:
    """When an event occurs. Times are timezone-aware UTC unless all-day."""

    start: datetime
    end: datetime
    time_zone: str | None = None
    all_day: bool = False


@dataclass(frozen=True)
class CalendarEventInput:
    """A generic event to create or update. No sports concepts."""

    title: str
    when: EventTime
    description: str | None = None
    location: str | None = None
    # Opaque private key/value pairs stored with the event (see domain.calendar.metadata).
    metadata: dict[str, str] = field(default_factory=dict)
    # Optional deterministic id, enabling provider-side duplicate rejection.
    event_id: str | None = None
    # "confirmed" | "cancelled" (provider-neutral subset).
    status: str | None = None


@dataclass(frozen=True)
class CalendarEventRecord:
    """An event as it exists at the provider."""

    id: str
    calendar_id: str
    title: str
    when: EventTime
    description: str | None = None
    location: str | None = None
    status: str | None = None
    updated_at: datetime | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    ical_uid: str | None = None


@dataclass(frozen=True)
class EventQuery:
    """Filter for listing/searching events."""

    time_min: datetime | None = None
    time_max: datetime | None = None
    text: str | None = None
    # Match events carrying these private metadata key/values.
    metadata_filter: dict[str, str] = field(default_factory=dict)
    max_results: int = 250


@dataclass(frozen=True)
class BatchResult:
    """Outcome of one operation inside a batch. Never raises for item failures."""

    index: int
    success: bool
    event: CalendarEventRecord | None = None
    error_code: str | None = None
    error_message: str | None = None


class CalendarProvider(Protocol):
    """A calendar backend bound to one authenticated account."""

    key: str
    # Authorization scopes this provider needs. Declared by the implementation so
    # the application layer can report "needs reconnect" without knowing the
    # provider's scope vocabulary.
    required_scopes: tuple[str, ...]

    # --- discovery ---------------------------------------------------------
    async def list_calendars(self) -> list[CalendarInfo]: ...
    async def get_calendar(self, external_id: str) -> CalendarInfo: ...

    # --- single event CRUD -------------------------------------------------
    async def create_event(
        self, calendar_id: str, event: CalendarEventInput
    ) -> CalendarEventRecord: ...
    async def update_event(
        self, calendar_id: str, event_id: str, event: CalendarEventInput
    ) -> CalendarEventRecord: ...
    async def delete_event(self, calendar_id: str, event_id: str) -> None: ...
    async def get_event(self, calendar_id: str, event_id: str) -> CalendarEventRecord | None: ...

    # --- queries -----------------------------------------------------------
    async def list_events(
        self, calendar_id: str, query: EventQuery
    ) -> list[CalendarEventRecord]: ...
    async def search_events(
        self, calendar_id: str, query: EventQuery
    ) -> list[CalendarEventRecord]: ...

    # --- batch -------------------------------------------------------------
    async def batch_create(
        self, calendar_id: str, events: list[CalendarEventInput]
    ) -> list[BatchResult]: ...
    async def batch_update(
        self, calendar_id: str, items: list[tuple[str, CalendarEventInput]]
    ) -> list[BatchResult]: ...
    async def batch_delete(self, calendar_id: str, event_ids: list[str]) -> list[BatchResult]: ...
