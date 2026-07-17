"""Sports-platform exceptions.

Every external sports API failure is mapped into one of these before it leaves
the infrastructure layer. Mirrors ``exceptions/calendar.py``: callers branch on
the category, never on an HTTP status or a provider's error string.

Provider-generic failures (``ProviderUnavailableError``, ``RateLimitError``)
come from ``exceptions/provider.py`` and are re-exported for convenience.
"""

from __future__ import annotations

from app.exceptions.base import AppError, NotFoundError, ValidationAppError

# The import-as-name aliases mark these as *explicit* re-exports for both mypy
# (strict no-implicit-reexport) and ruff.
from app.exceptions.provider import (
    ProviderError as ProviderError,
)
from app.exceptions.provider import (
    ProviderUnavailableError as ProviderUnavailableError,
)
from app.exceptions.provider import (
    RateLimitError as RateLimitError,
)


class SportsProviderError(ProviderError):
    """Base for sports-provider failures."""

    code = "sports_provider_error"
    message = "The sports provider returned an error."


class ProviderNotFoundError(NotFoundError):
    """No provider is registered for the requested provider/sport key."""

    code = "sports_provider_not_found"
    message = "No sports provider is registered for that key."


class CompetitionNotFoundError(NotFoundError):
    code = "competition_not_found"
    message = "The competition does not exist at the provider."


class TeamNotFoundError(NotFoundError):
    code = "team_not_found"
    message = "The team does not exist at the provider."


class CapabilityNotSupportedError(AppError):
    """The provider does not advertise the capability the caller requires."""

    code = "capability_not_supported"
    http_status = 400
    message = "This provider does not support the requested capability."


class ProviderAuthenticationError(SportsProviderError):
    """Our API key for the provider is missing, invalid, or expired.

    This is *our* misconfiguration, never the end user's — hence 502, not 401.
    """

    code = "sports_provider_auth_failed"
    http_status = 502
    message = "The sports provider rejected our credentials."


class MalformedResponseError(SportsProviderError):
    """The provider returned non-JSON, or JSON that isn't the expected shape."""

    code = "sports_provider_malformed_response"
    message = "The sports provider returned an unreadable response."


class NormalizationError(ValidationAppError):
    """A payload parsed, but a required field was missing or unmappable.

    Usually signals an upstream schema change. Surfaced per-item so one bad
    record never fails a whole refresh.
    """

    code = "sports_normalization_failed"
    http_status = 502
    message = "A provider record could not be normalized."
