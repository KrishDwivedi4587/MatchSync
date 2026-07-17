"""GoogleCalendarProvider tests against a mocked Google Calendar API.

Uses httpx.MockTransport so no network is touched, while still exercising the
real request building, pagination, retry policy, and error mapping.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.domain.calendar.metadata import EventMetadata
from app.domain.ports.calendar_provider import CalendarEventInput, EventQuery, EventTime
from app.domain.value_objects.enums import CalendarAccessRole
from app.exceptions.calendar import (
    CalendarNotFoundError,
    CalendarPermissionError,
    CalendarReauthRequiredError,
    ProviderUnavailableError,
    QuotaExceededError,
    RateLimitError,
)
from app.infrastructure.google.calendar_client import GoogleCalendarProvider
from app.infrastructure.http.resilient import ResilientHttpClient, RetryPolicy

ACCOUNT_ID = uuid.uuid4()
NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


class StubTokenManager:
    """Always yields a token; token-refresh itself is tested separately."""

    def __init__(self) -> None:
        self.calls = 0

    async def get_access_token(self, account_id: uuid.UUID) -> str:
        self.calls += 1
        return "access-token"


def _provider(handler, *, max_attempts: int = 3) -> GoogleCalendarProvider:
    transport = httpx.MockTransport(handler)
    http = ResilientHttpClient(
        client=httpx.AsyncClient(transport=transport),
        retry=RetryPolicy(max_attempts=max_attempts, base_delay=0.0, max_delay=0.0),
    )
    return GoogleCalendarProvider(ACCOUNT_ID, StubTokenManager(), http)


def _google_error(status: int, reason: str) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": status, "message": reason, "errors": [{"reason": reason}]}}
    )


# --- discovery -------------------------------------------------------------
async def test_list_calendars_paginates() -> None:
    pages = {
        None: {
            "items": [{"id": "c1", "summary": "One", "accessRole": "owner", "primary": True}],
            "nextPageToken": "p2",
        },
        "p2": {"items": [{"id": "c2", "summary": "Two", "accessRole": "reader"}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("pageToken")
        return httpx.Response(200, json=pages[token])

    calendars = await _provider(handler).list_calendars()
    assert [c.external_id for c in calendars] == ["c1", "c2"]
    assert calendars[0].access_role is CalendarAccessRole.OWNER
    assert calendars[0].is_primary is True
    assert calendars[1].access_role is CalendarAccessRole.READER


async def test_get_calendar_maps_unknown_role_to_none() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "c1", "summary": "X", "accessRole": "weird"})

    info = await _provider(handler).get_calendar("c1")
    assert info.access_role is CalendarAccessRole.NONE


# --- CRUD ------------------------------------------------------------------
def _event_input() -> CalendarEventInput:
    return CalendarEventInput(
        title="Match",
        when=EventTime(start=NOW, end=NOW + timedelta(hours=2)),
        metadata=EventMetadata(app_id="fx-1").to_properties(),
        event_id="abc12",
    )


async def test_create_event_sends_metadata_and_deterministic_id() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "abc12",
                "summary": "Match",
                "start": {"dateTime": "2026-08-01T12:00:00Z"},
                "end": {"dateTime": "2026-08-01T14:00:00Z"},
                "extendedProperties": {"private": {"ms_app": "1", "ms_id": "fx-1"}},
                "status": "confirmed",
            },
        )

    record = await _provider(handler).create_event("cal", _event_input())
    assert captured["id"] == "abc12"
    assert captured["extendedProperties"]["private"]["ms_id"] == "fx-1"
    assert captured["start"]["dateTime"] == "2026-08-01T12:00:00Z"
    assert record.id == "abc12"
    assert record.metadata["ms_id"] == "fx-1"


async def test_update_event_uses_patch() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(
            200,
            json={
                "id": "e1",
                "summary": "Updated",
                "start": {"dateTime": "2026-08-01T12:00:00Z"},
                "end": {"dateTime": "2026-08-01T14:00:00Z"},
            },
        )

    await _provider(handler).update_event("cal", "e1", _event_input())
    assert seen == ["PATCH"]  # PATCH preserves fields we don't manage


async def test_delete_event_treats_404_as_success() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _google_error(404, "notFound")

    await _provider(handler).delete_event("cal", "gone")  # must not raise


async def test_get_event_returns_none_when_missing() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _google_error(404, "notFound")

    assert await _provider(handler).get_event("cal", "missing") is None


async def test_list_events_builds_query_and_parses_all_day() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "e1",
                        "summary": "All day",
                        "start": {"date": "2026-08-01"},
                        "end": {"date": "2026-08-02"},
                    }
                ]
            },
        )

    events = await _provider(handler).list_events(
        "cal",
        EventQuery(
            time_min=NOW,
            time_max=NOW + timedelta(days=7),
            text="derby",
            metadata_filter={"ms_app": "1"},
        ),
    )
    assert captured["singleEvents"] == "true"
    assert captured["q"] == "derby"
    assert captured["privateExtendedProperty"] == "ms_app=1"
    assert events[0].when.all_day is True


# --- retries, rate limits, quota, outages ----------------------------------
async def test_retries_on_429_then_succeeds() -> None:
    attempts = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json={"items": []})

    assert await _provider(handler).list_calendars() == []
    assert attempts["n"] == 3


async def test_retries_on_5xx_then_raises_provider_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    with pytest.raises(ProviderUnavailableError):
        await _provider(handler).list_calendars()


async def test_rate_limit_403_is_retried() -> None:
    attempts = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _google_error(403, "rateLimitExceeded")
        return httpx.Response(200, json={"items": []})

    await _provider(handler).list_calendars()
    assert attempts["n"] == 2


async def test_quota_exceeded_is_not_retried() -> None:
    attempts = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return _google_error(403, "quotaExceeded")

    with pytest.raises(QuotaExceededError):
        await _provider(handler).list_calendars()
    assert attempts["n"] == 1  # terminal, no backoff loop


async def test_rate_limit_exhaustion_surfaces_rate_limit_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"}, json={})

    with pytest.raises(RateLimitError):
        await _provider(handler, max_attempts=2).list_calendars()


async def test_permission_denied_maps_to_permission_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _google_error(403, "insufficientPermissions")

    with pytest.raises(CalendarPermissionError):
        await _provider(handler).get_calendar("cal")


async def test_deleted_calendar_maps_to_not_found() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _google_error(404, "notFound")

    with pytest.raises(CalendarNotFoundError):
        await _provider(handler).get_calendar("gone")


async def test_401_maps_to_reauth_required() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _google_error(401, "authError")

    with pytest.raises(CalendarReauthRequiredError):
        await _provider(handler).list_calendars()


async def test_network_failure_raises_provider_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(ProviderUnavailableError):
        await _provider(handler).list_calendars()


# --- batch -----------------------------------------------------------------
async def test_batch_create_parses_mixed_success_and_failure() -> None:
    boundary = "resp_b"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/batch/calendarV3")
        assert "multipart/mixed" in request.headers["Content-Type"]
        content = (
            f"--{boundary}\r\nContent-ID: <response-item-0>\r\n\r\n"
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
            '{"id": "e1", "summary": "A", "start": {"dateTime": "2026-08-01T12:00:00Z"},'
            ' "end": {"dateTime": "2026-08-01T14:00:00Z"}}\r\n'
            f"--{boundary}\r\nContent-ID: <response-item-1>\r\n\r\n"
            "HTTP/1.1 409 Conflict\r\nContent-Type: application/json\r\n\r\n"
            '{"error": {"message": "dup", "errors": [{"reason": "duplicate"}]}}\r\n'
            f"--{boundary}--\r\n"
        ).encode()
        return httpx.Response(
            200,
            content=content,
            headers={"Content-Type": f"multipart/mixed; boundary={boundary}"},
        )

    results = await _provider(handler).batch_create("cal", [_event_input(), _event_input()])
    assert [r.index for r in results] == [0, 1]
    assert results[0].success is True and results[0].event.id == "e1"
    assert results[1].success is False and results[1].error_code == "duplicate"
