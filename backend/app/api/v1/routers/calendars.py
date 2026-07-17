"""Calendar endpoints.

    GET  /calendars           -> locally-known calendars + current default
    POST /calendars/refresh   -> re-discover calendars from the provider
    GET  /calendars/default   -> the selected sync-target calendar
    PUT  /calendars/default   -> select a sync-target calendar
    GET  /calendars/status    -> connection / scope / permission status
    POST /calendars/validate  -> check a calendar is reachable and writable

Plural resource names per Stage 1, Section 11 (the brief's `/calendar` examples
are singular; plural is the project convention). No synchronization endpoints.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.v1.deps import CurrentUser, get_calendar_service
from app.application.services.calendar_service import CalendarService
from app.exceptions.calendar import CalendarNotFoundError
from app.schemas.calendar import (
    CalendarListResponse,
    CalendarOut,
    CalendarStatusResponse,
    SetDefaultCalendarRequest,
    ValidateCalendarRequest,
    ValidateCalendarResponse,
)

router = APIRouter(prefix="/calendars", tags=["calendars"])

Service = Annotated[CalendarService, Depends(get_calendar_service)]


async def _list_response(service: CalendarService, user: CurrentUser) -> CalendarListResponse:
    calendars = await service.list_calendars(user)
    default = next((c for c in calendars if c.is_sync_target), None)
    return CalendarListResponse(
        calendars=[CalendarOut.model_validate(c) for c in calendars],
        default_calendar_id=default.id if default else None,
    )


@router.get("", response_model=CalendarListResponse, summary="List known calendars")
async def list_calendars(user: CurrentUser, service: Service) -> CalendarListResponse:
    return await _list_response(service, user)


@router.post(
    "/refresh",
    response_model=CalendarListResponse,
    summary="Re-discover calendars from the provider",
)
async def refresh_calendars(user: CurrentUser, service: Service) -> CalendarListResponse:
    await service.discover_calendars(user)
    return await _list_response(service, user)


@router.get("/default", response_model=CalendarOut, summary="Get the sync-target calendar")
async def get_default_calendar(user: CurrentUser, service: Service) -> CalendarOut:
    calendar = await service.get_default_calendar(user)
    if calendar is None:
        raise CalendarNotFoundError("No default calendar has been selected.")
    return CalendarOut.model_validate(calendar)


@router.put("/default", response_model=CalendarOut, summary="Select the sync-target calendar")
async def set_default_calendar(
    payload: SetDefaultCalendarRequest, user: CurrentUser, service: Service
) -> CalendarOut:
    calendar = await service.set_default_calendar(user, payload.calendar_id)
    return CalendarOut.model_validate(calendar)


@router.get("/status", response_model=CalendarStatusResponse, summary="Calendar connection status")
async def calendar_status(user: CurrentUser, service: Service) -> CalendarStatusResponse:
    result = await service.get_status(user)
    return CalendarStatusResponse(**result.__dict__)


@router.post(
    "/validate",
    response_model=ValidateCalendarResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate a calendar is reachable and writable",
)
async def validate_calendar(
    payload: ValidateCalendarRequest, user: CurrentUser, service: Service
) -> ValidateCalendarResponse:
    result = await service.validate_calendar(user, payload.calendar_id)
    return ValidateCalendarResponse(**result.__dict__)
