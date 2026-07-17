"""Exception hierarchy.

Stage 1 specified a typed exception hierarchy so that HTTP middleware and Celery
retry logic can branch on *category* rather than parsing messages. Only the base
taxonomy is defined here; concrete domain exceptions are added in later stages.

Categories:
- ``AppError``       — base for every expected, handled error.
- ``NotFoundError``  — a requested resource does not exist (-> HTTP 404).
- ``ConflictError``  — a uniqueness/state conflict (-> HTTP 409).
- ``ValidationAppError`` — semantic validation failure (-> HTTP 422).
- ``RetryableError`` — transient failure; background tasks may retry.
- ``PermanentError`` — non-transient failure; do not retry.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all expected application errors.

    ``code`` is a stable, machine-readable string returned to clients so the
    frontend can branch on it without parsing human text.
    """

    code: str = "app_error"
    http_status: int = 500
    message: str = "An unexpected application error occurred."

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code
        super().__init__(self.message)


class NotFoundError(AppError):
    code = "not_found"
    http_status = 404
    message = "The requested resource was not found."


class ConflictError(AppError):
    code = "conflict"
    http_status = 409
    message = "The request conflicts with the current state."


class ValidationAppError(AppError):
    code = "validation_error"
    http_status = 422
    message = "The request failed validation."


class AuthenticationError(AppError):
    """The caller is not authenticated (missing/invalid/expired credentials)."""

    code = "not_authenticated"
    http_status = 401
    message = "Authentication required."


class AuthorizationError(AppError):
    """The caller is authenticated but not permitted to perform the action."""

    code = "forbidden"
    http_status = 403
    message = "You do not have permission to perform this action."


class RetryableError(AppError):
    """Transient failure (e.g. upstream 5xx/429). Safe for background retry."""

    code = "retryable_error"
    http_status = 503
    message = "A temporary error occurred. Please retry."


class PermanentError(AppError):
    """Non-transient failure. Retrying will not help."""

    code = "permanent_error"
    http_status = 400
    message = "The request cannot be completed."
