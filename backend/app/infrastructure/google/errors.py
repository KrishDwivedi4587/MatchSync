"""Google error parsing and mapping.

This is the ONLY place Google's error vocabulary is understood. Everything above
the infrastructure layer sees application exceptions.
"""

from __future__ import annotations

import httpx

from app.exceptions.calendar import (
    CalendarError,
    CalendarNotFoundError,
    CalendarPermissionError,
    CalendarReauthRequiredError,
    EventConflictError,
    ProviderUnavailableError,
    QuotaExceededError,
    RateLimitError,
)

# 403 reasons that are transient throttling (safe to retry with backoff).
RETRYABLE_403_REASONS = frozenset(
    {"rateLimitExceeded", "userRateLimitExceeded", "backendError", "internalError"}
)
# 403 reasons that mean the quota bucket is exhausted (retrying now won't help).
QUOTA_403_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})
# 403 reasons that mean insufficient permission / missing scope.
PERMISSION_403_REASONS = frozenset(
    {"forbidden", "insufficientPermissions", "requiredAccessLevel", "accessNotConfigured"}
)


def parse_error(response: httpx.Response) -> tuple[str | None, str]:
    """Return (reason, message) from a Google error payload. Never raises."""
    try:
        error = response.json().get("error", {})
    except Exception:
        return None, response.reason_phrase or "Unknown error"

    if isinstance(error, str):  # OAuth-style {"error": "invalid_grant"}
        return error, error

    message = error.get("message") or response.reason_phrase or "Unknown error"
    errors = error.get("errors") or []
    reason = errors[0].get("reason") if errors and isinstance(errors[0], dict) else None
    return reason, message


def is_retryable_response(response: httpx.Response) -> bool:
    """Predicate handed to ResilientHttpClient — Google-specific retry rules."""
    status = response.status_code
    if status == 429 or status >= 500:
        return True
    if status == 403:
        reason, _ = parse_error(response)
        return reason in RETRYABLE_403_REASONS
    return False


def map_error(response: httpx.Response) -> Exception:
    """Translate a failed Google response into an application exception."""
    status = response.status_code
    reason, message = parse_error(response)

    if status == 401:
        # The token was refreshed before this call, so a 401 means revoked.
        return CalendarReauthRequiredError()
    if status == 403:
        if reason in QUOTA_403_REASONS:
            return QuotaExceededError()
        if reason in RETRYABLE_403_REASONS:
            return RateLimitError()
        return CalendarPermissionError()
    if status == 404 or status == 410:
        return CalendarNotFoundError()
    if status == 409:
        return EventConflictError()
    if status == 429:
        return RateLimitError()
    if status >= 500:
        return ProviderUnavailableError()

    return CalendarError(f"Google Calendar error: {message}")
