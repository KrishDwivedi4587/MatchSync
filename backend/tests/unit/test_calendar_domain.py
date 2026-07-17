"""Unit tests for pure calendar domain logic: metadata + duplicate detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.calendar.duplicates import (
    find_duplicates,
    index_by_app_id,
    is_duplicate,
    within_time_window,
)
from app.domain.calendar.metadata import (
    APP_MARKER_KEY,
    EventMetadata,
    content_hash,
    derive_event_id,
    is_owned_by_matchsync,
    ownership_filter,
)
from app.domain.ports.calendar_provider import CalendarEventRecord, EventTime

BASE = datetime(2026, 8, 1, 15, 0, tzinfo=UTC)


def _event(
    event_id: str, title: str, *, start: datetime = BASE, metadata: dict[str, str] | None = None
) -> CalendarEventRecord:
    return CalendarEventRecord(
        id=event_id,
        calendar_id="cal",
        title=title,
        when=EventTime(start=start, end=start + timedelta(hours=2)),
        metadata=metadata or {},
    )


# --- metadata --------------------------------------------------------------
def test_metadata_roundtrip() -> None:
    meta = EventMetadata(app_id="fx-1", source="football-api", source_id="99", content_hash="abc")
    props = meta.to_properties()
    assert props[APP_MARKER_KEY] == "1"
    parsed = EventMetadata.from_properties(props)
    assert parsed == meta


def test_metadata_rejects_foreign_events() -> None:
    # No ownership marker -> not ours -> None.
    assert EventMetadata.from_properties({"ms_id": "x"}) is None
    assert is_owned_by_matchsync({}) is False
    assert is_owned_by_matchsync(ownership_filter()) is True


def test_derive_event_id_is_deterministic_and_google_safe() -> None:
    a, b = derive_event_id("football:PL:ars-che:2026-08-01"), derive_event_id(
        "football:PL:ars-che:2026-08-01"
    )
    assert a == b
    assert derive_event_id("other") != a
    # Google requires base32hex: chars 0-9 and a-v, length 5..1024.
    assert 5 <= len(a) <= 1024
    assert set(a) <= set("0123456789abcdefghijklmnopqrstuv")


def test_content_hash_changes_with_content() -> None:
    assert content_hash("t", BASE) == content_hash("t", BASE)
    assert content_hash("t", BASE) != content_hash("t2", BASE)


# --- duplicates ------------------------------------------------------------
def test_duplicate_by_app_id_beats_title() -> None:
    meta = EventMetadata(app_id="same").to_properties()
    a = _event("g1", "Arsenal vs Chelsea", metadata=meta)
    b = _event("g2", "Totally different title", metadata=meta)
    assert is_duplicate(a, b) is True


def test_different_app_ids_are_not_duplicates() -> None:
    a = _event("g1", "Match", metadata=EventMetadata(app_id="one").to_properties())
    b = _event("g2", "Match", metadata=EventMetadata(app_id="two").to_properties())
    assert is_duplicate(a, b) is False


def test_duplicate_by_source_id() -> None:
    props = EventMetadata(app_id="a1", source="football-api", source_id="77").to_properties()
    other = EventMetadata(app_id="a2", source="football-api", source_id="77").to_properties()
    # app_ids differ, so the app-id rule short-circuits to "not duplicate".
    assert (
        is_duplicate(_event("g1", "X", metadata=props), _event("g2", "Y", metadata=other)) is False
    )
    # With no app ids, the source key decides.
    del props["ms_id"], other["ms_id"]
    a = CalendarEventRecord(
        id="g1", calendar_id="c", title="X", when=EventTime(BASE, BASE), metadata=props
    )
    b = CalendarEventRecord(
        id="g2", calendar_id="c", title="Y", when=EventTime(BASE, BASE), metadata=other
    )
    assert is_duplicate(a, b) is True


def test_fuzzy_duplicate_within_time_window() -> None:
    a = _event("g1", "Arsenal vs Chelsea")
    b = _event("g2", "  arsenal   VS   chelsea ", start=BASE + timedelta(minutes=20))
    assert is_duplicate(a, b) is True

    far = _event("g3", "Arsenal vs Chelsea", start=BASE + timedelta(hours=5))
    assert is_duplicate(a, far) is False
    assert within_time_window(a, far) is False


def test_same_provider_id_is_duplicate() -> None:
    assert is_duplicate(_event("same", "A"), _event("same", "B")) is True


def test_index_by_app_id_skips_foreign_events() -> None:
    ours = _event("g1", "Ours", metadata=EventMetadata(app_id="fx-1").to_properties())
    theirs = _event("g2", "User's own event")
    index = index_by_app_id([ours, theirs])
    assert index == {"fx-1": ours}


def test_find_duplicates_clusters_and_ignores_singletons() -> None:
    meta = EventMetadata(app_id="dup").to_properties()
    a, b = _event("g1", "A", metadata=meta), _event("g2", "B", metadata=meta)
    lone = _event("g3", "Unique", metadata=EventMetadata(app_id="solo").to_properties())
    fuzzy1, fuzzy2 = _event("g4", "Derby"), _event("g5", "derby", start=BASE + timedelta(minutes=5))

    clusters = find_duplicates([a, b, lone, fuzzy1, fuzzy2])
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [2, 2]  # the app-id pair and the fuzzy pair; `lone` excluded
