"""Calendar API schemas (public request/response DTOs)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from app.domain.value_objects.enums import CalendarAccessRole, CalendarProvider


class CalendarOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: CalendarProvider
    external_calendar_id: str
    summary: str
    description: str | None
    time_zone: str | None
    is_primary: bool
    is_sync_target: bool
    access_role: str | None


class CalendarListResponse(BaseModel):
    calendars: list[CalendarOut]
    default_calendar_id: uuid.UUID | None = None


class SetDefaultCalendarRequest(BaseModel):
    calendar_id: uuid.UUID


class CalendarStatusResponse(BaseModel):
    connected: bool
    account_email: str | None
    has_calendar_scope: bool
    needs_reauth: bool
    calendar_count: int
    default_calendar_id: uuid.UUID | None
    default_calendar_summary: str | None


class ValidateCalendarRequest(BaseModel):
    calendar_id: uuid.UUID


class ValidateCalendarResponse(BaseModel):
    valid: bool
    writable: bool
    access_role: CalendarAccessRole | None = None
    reason: str | None = None
