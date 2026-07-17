"""Provider-agnostic external-integration exceptions.

These describe failures of *any* outbound provider (calendar, sports, future
notification backends). They live here — not under a specific platform — because
the shared ``ResilientHttpClient`` raises them and must not depend on one
platform's module.

Platform-specific exceptions (``exceptions/calendar.py``, ``exceptions/sports.py``)
build on top of these.
"""

from __future__ import annotations

from app.exceptions.base import AppError, RetryableError


class ProviderError(AppError):
    """Base for any external provider failure."""

    code = "provider_error"
    http_status = 502
    message = "An external provider returned an error."


class ProviderUnavailableError(RetryableError):
    """Provider outage or network failure (5xx / timeout / DNS / circuit open)."""

    code = "provider_unavailable"
    http_status = 503
    message = "The external provider is temporarily unavailable."


class RateLimitError(RetryableError):
    """Provider throttled us. Safe to retry after a backoff."""

    code = "provider_rate_limited"
    http_status = 429
    message = "The external provider is rate limiting requests."
