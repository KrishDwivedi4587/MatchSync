"""Calendar-platform exceptions.

Provider-agnostic: every Google (or future Apple/Outlook/CalDAV) failure is
mapped into one of these before it leaves the infrastructure layer. Callers
branch on the category, never on an HTTP status or a Google error string.
"""

from __future__ import annotations

from app.exceptions.base import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
)

# Provider-generic failures live in exceptions/provider.py (shared with the
# sports platform and the resilient HTTP client). Re-exported here so existing
# calendar imports keep working.
from app.exceptions.provider import (  # noqa: F401
    ProviderUnavailableError,
    RateLimitError,
)


class CalendarError(AppError):
    """Base for calendar-platform failures."""

    code = "calendar_error"
    http_status = 502
    message = "The calendar provider returned an error."


class CalendarNotFoundError(NotFoundError):
    code = "calendar_not_found"
    message = "The calendar does not exist or is no longer accessible."


class EventNotFoundError(NotFoundError):
    code = "event_not_found"
    message = "The calendar event does not exist."


class CalendarPermissionError(AuthorizationError):
    """Authenticated, but lacking permission on this calendar (e.g. read-only)."""

    code = "calendar_permission_denied"
    message = "You do not have write access to this calendar."


class CalendarReauthRequiredError(AuthenticationError):
    """Token expired/revoked or missing scopes — the user must re-consent."""

    code = "calendar_reauth_required"
    message = "Calendar access needs to be reconnected."


class QuotaExceededError(CalendarError):
    """Daily/project quota exhausted. Retrying soon will not help."""

    code = "calendar_quota_exceeded"
    http_status = 429
    message = "Calendar API quota exceeded. Try again later."


class EventConflictError(ConflictError):
    """An event with the same (deterministic) id already exists."""

    code = "calendar_event_conflict"
    message = "An event with this identifier already exists."


class UnsupportedProviderError(CalendarError):
    code = "calendar_provider_unsupported"
    http_status = 400
    message = "This calendar provider is not supported."
