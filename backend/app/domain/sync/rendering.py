"""Fixture -> calendar event rendering (pure).

Turns a persisted fixture into the provider-agnostic ``CalendarEventInput`` the
Calendar Platform accepts. This is the only place that decides what an event
*looks like*, and it is deliberately pure so the rendering is deterministic —
two runs on the same fixture produce byte-identical event bodies, which is what
lets the content hash be a reliable no-op signal.

Metadata (Stage 5's private extended properties) carries our ownership marker,
the fixture's identity key as ``ms_id``, and the fixture content hash as
``ms_hash``. The event id is derived deterministically from the identity key, so
a duplicate insert is rejected by the provider itself (invariant I2).
"""

from __future__ import annotations

from datetime import timedelta

from app.domain.calendar.metadata import EventMetadata, derive_event_id
from app.domain.ports.calendar_provider import CalendarEventInput, EventTime
from app.domain.sync.models import FixtureSnapshot
from app.domain.value_objects.enums import FixtureStatus

DEFAULT_DURATION = timedelta(hours=2)

_STATUS_PREFIX: dict[FixtureStatus, str] = {
    FixtureStatus.CANCELLED: "CANCELLED",
    FixtureStatus.POSTPONED: "POSTPONED",
}


def render_title(fixture: FixtureSnapshot, *, prefix: str | None = None) -> str:
    """ "[PREFIX] CANCELLED: Arsenal vs Chelsea"."""
    if fixture.home_name and fixture.away_name:
        core = f"{fixture.home_name} vs {fixture.away_name}"
    elif fixture.home_name or fixture.away_name:
        core = fixture.home_name or fixture.away_name or fixture.competition_name
    else:
        core = fixture.competition_name

    status_prefix = _STATUS_PREFIX.get(fixture.status)
    if status_prefix:
        core = f"{status_prefix}: {core}"
    if prefix:
        core = f"{prefix} {core}"
    return core


def render_description(fixture: FixtureSnapshot) -> str:
    lines = [fixture.competition_name]
    if fixture.stage:
        lines.append(f"Stage: {fixture.stage}")
    if fixture.round:
        lines.append(f"Round: {fixture.round}")
    if fixture.venue:
        lines.append(f"Venue: {fixture.venue}")
    if fixture.status is FixtureStatus.POSTPONED:
        lines.append("This fixture has been postponed.")
    elif fixture.status is FixtureStatus.CANCELLED:
        lines.append("This fixture has been cancelled.")
    lines.append("")
    lines.append("Synced by MatchSync")
    return "\n".join(lines)


def render_metadata(fixture: FixtureSnapshot) -> dict[str, str]:
    return EventMetadata(
        app_id=fixture.identity_key,
        source=fixture.sport_key,
        source_id=str(fixture.id),
        content_hash=fixture.content_hash,
    ).to_properties()


def render_event(
    fixture: FixtureSnapshot,
    *,
    prefix: str | None = None,
    duration: timedelta = DEFAULT_DURATION,
    include_event_id: bool = False,
) -> CalendarEventInput:
    """Build the event body. ``include_event_id`` only on CREATE (see I2)."""
    end = fixture.scheduled_end or (fixture.scheduled_start + duration)
    return CalendarEventInput(
        title=render_title(fixture, prefix=prefix),
        when=EventTime(start=fixture.scheduled_start, end=end),
        description=render_description(fixture),
        location=fixture.venue,
        metadata=render_metadata(fixture),
        event_id=derive_event_id(fixture.identity_key) if include_event_id else None,
    )
