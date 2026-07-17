"""Typed exception hierarchy and global handlers."""

from app.exceptions.base import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    PermanentError,
    RetryableError,
    ValidationAppError,
)

__all__ = [
    "AppError",
    "AuthenticationError",
    "AuthorizationError",
    "ConflictError",
    "NotFoundError",
    "PermanentError",
    "RetryableError",
    "ValidationAppError",
]
