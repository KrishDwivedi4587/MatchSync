"""Google Calendar provider — implements the ``CalendarProvider`` port.

All Google specifics live here: URL shapes, JSON serialization, pagination,
extendedProperties, batch encoding, and error mapping. Everything it returns is
a provider-agnostic domain dataclass.

Bound to a single authenticated account: credentials come from the existing
encrypted ``oauth_tokens`` row via ``GoogleTokenManager``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.domain.ports.calendar_provider import (
    BatchResult,
    CalendarEventInput,
    CalendarEventRecord,
    CalendarInfo,
    EventQuery,
    EventTime,
)
from app.domain.value_objects.enums import CalendarAccessRole
from app.exceptions.calendar import CalendarNotFoundError, EventNotFoundError
from app.infrastructure.google import errors as google_errors
from app.infrastructure.google.batch import (
    BatchRequest,
    build_batch_body,
    chunk,
    parse_batch_response,
)
from app.infrastructure.google.endpoints import (
    GOOGLE_CALENDAR_BASE,
    GOOGLE_CALENDAR_BATCH_ENDPOINT,
)
from app.infrastructure.google.token_manager import GoogleTokenManager
from app.infrastructure.http.resilient import ResilientHttpClient

logger = get_logger(__name__)

_PAGE_SIZE = 250
_MAX_PAGES = 40  # safety valve against a pathological pagination loop


def _to_rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class GoogleCalendarProvider:
    """``CalendarProvider`` implementation for Google Calendar v3."""

    key = "google"
    # Least privilege: enumerate calendars + manage events. Deliberately NOT the
    # full `auth/calendar` scope, which also permits creating/deleting calendars.
    required_scopes = (
        "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    )

    def __init__(
        self,
        account_id: uuid.UUID,
        token_manager: GoogleTokenManager,
        http: ResilientHttpClient,
    ) -> None:
        self._account_id = account_id
        self._tokens = token_manager
        self._http = http

    # --- transport ---------------------------------------------------------
    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        token = await self._tokens.get_access_token(self._account_id)
        headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
        response = await self._http.request(
            method,
            url,
            headers=headers,
            is_retryable=google_errors.is_retryable_response,
            **kwargs,
        )
        if response.status_code >= 400:
            raise google_errors.map_error(response)
        return response

    async def _paginate(self, url: str, params: dict[str, Any]) -> list[dict]:
        items: list[dict] = []
        page_token: str | None = None
        for _ in range(_MAX_PAGES):
            query = {**params, "maxResults": _PAGE_SIZE}
            if page_token:
                query["pageToken"] = page_token
            payload = (await self._request("GET", url, params=query)).json()
            items.extend(payload.get("items", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return items

    # --- serialization -----------------------------------------------------
    @staticmethod
    def _calendar_from_json(item: dict) -> CalendarInfo:
        try:
            role = CalendarAccessRole(item.get("accessRole", "none"))
        except ValueError:
            role = CalendarAccessRole.NONE
        return CalendarInfo(
            external_id=item["id"],
            summary=item.get("summary", ""),
            access_role=role,
            is_primary=bool(item.get("primary", False)),
            description=item.get("description"),
            time_zone=item.get("timeZone"),
        )

    @staticmethod
    def _event_body(event: CalendarEventInput) -> dict[str, Any]:
        when = event.when
        if when.all_day:
            start = {"date": when.start.date().isoformat()}
            end = {"date": when.end.date().isoformat()}
        else:
            start = {"dateTime": _to_rfc3339(when.start)}
            end = {"dateTime": _to_rfc3339(when.end)}
            if when.time_zone:
                start["timeZone"] = when.time_zone
                end["timeZone"] = when.time_zone

        body: dict[str, Any] = {"summary": event.title, "start": start, "end": end}
        if event.description:
            body["description"] = event.description
        if event.location:
            body["location"] = event.location
        if event.status:
            body["status"] = event.status
        if event.event_id:
            body["id"] = event.event_id
        if event.metadata:
            body["extendedProperties"] = {"private": dict(event.metadata)}
        return body

    @staticmethod
    def _event_from_json(calendar_id: str, item: dict) -> CalendarEventRecord:
        start_raw = item.get("start", {})
        end_raw = item.get("end", {})
        all_day = "date" in start_raw

        if all_day:
            start = _parse_dt(start_raw["date"])
            end = _parse_dt(end_raw.get("date", start_raw["date"]))
        else:
            start = _parse_dt(start_raw["dateTime"])
            end = _parse_dt(end_raw.get("dateTime", start_raw["dateTime"]))

        updated = item.get("updated")
        return CalendarEventRecord(
            id=item["id"],
            calendar_id=calendar_id,
            title=item.get("summary", ""),
            when=EventTime(
                start=start, end=end, time_zone=start_raw.get("timeZone"), all_day=all_day
            ),
            description=item.get("description"),
            location=item.get("location"),
            status=item.get("status"),
            updated_at=_parse_dt(updated) if updated else None,
            metadata=dict((item.get("extendedProperties") or {}).get("private") or {}),
            ical_uid=item.get("iCalUID"),
        )

    # --- discovery ---------------------------------------------------------
    async def list_calendars(self) -> list[CalendarInfo]:
        items = await self._paginate(f"{GOOGLE_CALENDAR_BASE}/users/me/calendarList", {})
        logger.info("calendar.discovery.completed", count=len(items))
        return [self._calendar_from_json(item) for item in items]

    async def get_calendar(self, external_id: str) -> CalendarInfo:
        # calendarList (not calendars) is used because only it carries accessRole.
        response = await self._request(
            "GET", f"{GOOGLE_CALENDAR_BASE}/users/me/calendarList/{external_id}"
        )
        return self._calendar_from_json(response.json())

    # --- single event CRUD -------------------------------------------------
    async def create_event(
        self, calendar_id: str, event: CalendarEventInput
    ) -> CalendarEventRecord:
        response = await self._request(
            "POST",
            f"{GOOGLE_CALENDAR_BASE}/calendars/{calendar_id}/events",
            json=self._event_body(event),
        )
        return self._event_from_json(calendar_id, response.json())

    async def update_event(
        self, calendar_id: str, event_id: str, event: CalendarEventInput
    ) -> CalendarEventRecord:
        # PATCH so unspecified fields (attendees, reminders) are preserved.
        response = await self._request(
            "PATCH",
            f"{GOOGLE_CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}",
            json=self._event_body(event),
        )
        return self._event_from_json(calendar_id, response.json())

    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        try:
            await self._request(
                "DELETE", f"{GOOGLE_CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}"
            )
        except (CalendarNotFoundError, EventNotFoundError):
            # Already gone (e.g. the user deleted it) == the desired end state.
            logger.info("calendar.event.delete_noop", calendar_id=calendar_id)

    async def get_event(self, calendar_id: str, event_id: str) -> CalendarEventRecord | None:
        try:
            response = await self._request(
                "GET", f"{GOOGLE_CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}"
            )
        except (CalendarNotFoundError, EventNotFoundError):
            return None
        return self._event_from_json(calendar_id, response.json())

    # --- queries -----------------------------------------------------------
    def _query_params(self, query: EventQuery) -> dict[str, Any]:
        params: dict[str, Any] = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "showDeleted": "false",
        }
        if query.time_min:
            params["timeMin"] = _to_rfc3339(query.time_min)
        if query.time_max:
            params["timeMax"] = _to_rfc3339(query.time_max)
        if query.text:
            params["q"] = query.text
        if query.metadata_filter:
            params["privateExtendedProperty"] = [
                f"{k}={v}" for k, v in query.metadata_filter.items()
            ]
        return params

    async def list_events(self, calendar_id: str, query: EventQuery) -> list[CalendarEventRecord]:
        items = await self._paginate(
            f"{GOOGLE_CALENDAR_BASE}/calendars/{calendar_id}/events",
            self._query_params(query),
        )
        return [self._event_from_json(calendar_id, item) for item in items[: query.max_results]]

    async def search_events(self, calendar_id: str, query: EventQuery) -> list[CalendarEventRecord]:
        # Google exposes text search and metadata filters through the same list
        # endpoint; the distinction is preserved at the port for other providers.
        return await self.list_events(calendar_id, query)

    # --- batch -------------------------------------------------------------
    async def _execute_batch(
        self, calendar_id: str, requests: list[BatchRequest], offset: int
    ) -> list[BatchResult]:
        boundary = f"batch_{uuid.uuid4().hex}"
        body = build_batch_body(requests, boundary)
        response = await self._request(
            "POST",
            GOOGLE_CALENDAR_BATCH_ENDPOINT,
            content=body,
            headers={"Content-Type": f"multipart/mixed; boundary={boundary}"},
        )

        parsed = parse_batch_response(response.content, response.headers.get("Content-Type", ""))
        results: list[BatchResult] = []
        for item in parsed:
            ok = 200 <= item.status_code < 300
            event = None
            error_code = error_message = None
            if ok and item.body:
                event = self._event_from_json(calendar_id, item.body)
            elif not ok:
                error = (item.body or {}).get("error", {})
                errs = error.get("errors") or []
                error_code = errs[0].get("reason") if errs else str(item.status_code)
                error_message = error.get("message", "batch item failed")
            results.append(
                BatchResult(
                    index=offset + item.index,
                    success=ok,
                    event=event,
                    error_code=error_code,
                    error_message=error_message,
                )
            )
        return results

    async def _run_batches(
        self, calendar_id: str, requests: list[BatchRequest]
    ) -> list[BatchResult]:
        results: list[BatchResult] = []
        offset = 0
        for group in chunk(requests):
            results.extend(await self._execute_batch(calendar_id, group, offset))
            offset += len(group)
        return results

    async def batch_create(
        self, calendar_id: str, events: list[CalendarEventInput]
    ) -> list[BatchResult]:
        requests = [
            BatchRequest(
                "POST", f"/calendar/v3/calendars/{calendar_id}/events", self._event_body(e)
            )
            for e in events
        ]
        return await self._run_batches(calendar_id, requests)

    async def batch_update(
        self, calendar_id: str, items: list[tuple[str, CalendarEventInput]]
    ) -> list[BatchResult]:
        requests = [
            BatchRequest(
                "PATCH",
                f"/calendar/v3/calendars/{calendar_id}/events/{event_id}",
                self._event_body(event),
            )
            for event_id, event in items
        ]
        return await self._run_batches(calendar_id, requests)

    async def batch_delete(self, calendar_id: str, event_ids: list[str]) -> list[BatchResult]:
        requests = [
            BatchRequest("DELETE", f"/calendar/v3/calendars/{calendar_id}/events/{eid}")
            for eid in event_ids
        ]
        return await self._run_batches(calendar_id, requests)
