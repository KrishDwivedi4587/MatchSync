"""Duplicate detection utilities.

Pure functions over ``CalendarEventRecord``s. The synchronization stage will
build its reconcile engine on top of these; nothing here knows about sports,
fixtures, or Google.

Detection ladder, strongest signal first:
1. **Application id** (``ms_id``) — authoritative when both events are ours.
2. **Source id** (``ms_src`` + ``ms_src_id``) — same upstream object.
3. **Provider event id** — literally the same remote row.
4. **Fuzzy fingerprint** — same normalized title starting within a tolerance
   window. The only heuristic; used to spot pre-existing user-created events
   that mirror one of ours.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from app.domain.calendar.metadata import EventMetadata
from app.domain.ports.calendar_provider import CalendarEventRecord

DEFAULT_TOLERANCE = timedelta(minutes=30)


def _normalize_title(title: str) -> str:
    return " ".join(title.strip().lower().split())


def event_fingerprint(event: CalendarEventRecord) -> str:
    """Coarse identity: normalized title + start time rounded to the hour."""
    start = event.when.start.replace(minute=0, second=0, microsecond=0)
    return f"{_normalize_title(event.title)}|{start.isoformat()}"


def app_id_of(event: CalendarEventRecord) -> str | None:
    meta = EventMetadata.from_properties(event.metadata)
    return meta.app_id if meta and meta.app_id else None


def source_key_of(event: CalendarEventRecord) -> tuple[str, str] | None:
    meta = EventMetadata.from_properties(event.metadata)
    if meta and meta.source and meta.source_id:
        return (meta.source, meta.source_id)
    return None


def within_time_window(
    a: CalendarEventRecord, b: CalendarEventRecord, tolerance: timedelta = DEFAULT_TOLERANCE
) -> bool:
    return abs(a.when.start - b.when.start) <= tolerance


def is_duplicate(
    a: CalendarEventRecord,
    b: CalendarEventRecord,
    *,
    tolerance: timedelta = DEFAULT_TOLERANCE,
) -> bool:
    """True if two events represent the same real-world occurrence."""
    if a.id and b.id and a.id == b.id:
        return True

    a_app, b_app = app_id_of(a), app_id_of(b)
    if a_app and b_app:
        return a_app == b_app

    a_src, b_src = source_key_of(a), source_key_of(b)
    if a_src and b_src:
        return a_src == b_src

    # Fuzzy fallback: same title, starting close enough together.
    return _normalize_title(a.title) == _normalize_title(b.title) and within_time_window(
        a, b, tolerance
    )


def index_by_app_id(events: list[CalendarEventRecord]) -> dict[str, CalendarEventRecord]:
    """Map application id -> event, for O(1) lookup during reconciliation."""
    index: dict[str, CalendarEventRecord] = {}
    for event in events:
        app_id = app_id_of(event)
        if app_id:
            index[app_id] = event
    return index


def find_by_metadata(
    events: list[CalendarEventRecord], key: str, value: str
) -> list[CalendarEventRecord]:
    return [e for e in events if e.metadata.get(key) == value]


def find_duplicates(
    events: list[CalendarEventRecord], *, tolerance: timedelta = DEFAULT_TOLERANCE
) -> list[list[CalendarEventRecord]]:
    """Group events into clusters of duplicates. Singletons are omitted."""
    # Fast path: group by the strongest available key.
    keyed: dict[str, list[CalendarEventRecord]] = defaultdict(list)
    unkeyed: list[CalendarEventRecord] = []
    for event in events:
        app_id = app_id_of(event)
        source = source_key_of(event)
        if app_id:
            keyed[f"app:{app_id}"].append(event)
        elif source:
            keyed[f"src:{source[0]}:{source[1]}"].append(event)
        else:
            unkeyed.append(event)

    clusters = [group for group in keyed.values() if len(group) > 1]

    # Slow path for events with no metadata: pairwise fuzzy comparison.
    remaining = list(unkeyed)
    while remaining:
        head = remaining.pop(0)
        group = [head]
        rest: list[CalendarEventRecord] = []
        for other in remaining:
            if is_duplicate(head, other, tolerance=tolerance):
                group.append(other)
            else:
                rest.append(other)
        remaining = rest
        if len(group) > 1:
            clusters.append(group)

    return clusters
