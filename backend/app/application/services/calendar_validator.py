"""Calendar validation.

Answers "is this calendar usable by this user, right now?" — ownership (does the
row belong to one of the user's linked accounts), liveness (not soft-deleted),
and remote reachability + writability (ask the provider).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.application.services.calendar_permissions import CalendarPermissions
from app.core.logging import get_logger
from app.domain.ports.calendar_provider import CalendarProvider
from app.domain.value_objects.enums import CalendarAccessRole
from app.exceptions.calendar import (
    CalendarNotFoundError,
    CalendarPermissionError,
    CalendarReauthRequiredError,
)
from app.persistence.models.calendar import Calendar

logger = get_logger(__name__)


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    writable: bool
    access_role: CalendarAccessRole | None = None
    reason: str | None = None


class CalendarValidator:
    @staticmethod
    def validate_ownership(calendar: Calendar, account_ids: set[uuid.UUID]) -> None:
        """The calendar row must belong to one of the caller's linked accounts."""
        if calendar.google_account_id not in account_ids:
            logger.warning("calendar.validation.ownership_denied", calendar_id=str(calendar.id))
            # Do not disclose existence of another user's calendar.
            raise CalendarNotFoundError()

    @staticmethod
    def validate_not_deleted(calendar: Calendar) -> None:
        if calendar.deleted_at is not None:
            raise CalendarNotFoundError("This calendar has been removed.")

    @staticmethod
    async def validate_remote(provider: CalendarProvider, external_id: str) -> ValidationResult:
        """Confirm the calendar still exists remotely and we may write to it."""
        try:
            info = await provider.get_calendar(external_id)
        except CalendarNotFoundError:
            return ValidationResult(False, False, reason="Calendar no longer exists.")
        except CalendarPermissionError:
            return ValidationResult(False, False, reason="Access to this calendar was revoked.")
        except CalendarReauthRequiredError:
            return ValidationResult(False, False, reason="Calendar access must be reconnected.")

        writable = CalendarPermissions.can_write(info.access_role)
        return ValidationResult(
            valid=True,
            writable=writable,
            access_role=info.access_role,
            reason=None if writable else "Calendar is read-only.",
        )
