"""Retry orchestration policy (pure).

Two decisions, both made here and nowhere else:

1. **Is this failure worth retrying?** Classified from the exception's *category*,
   never its message. The exception taxonomy was built for exactly this in
   Stage 2 (``RetryableError`` / ``PermanentError``) and extended by the calendar
   and sports platforms.

2. **How long should we wait?** Exponential backoff with **full jitter**:
   ``delay = uniform(0, min(cap, base * 2^(attempt-1)))``. Full jitter, rather than
   equal jitter or none, because a fleet of workers whose subscriptions all fail
   at the same moment (a Google outage) must not re-converge into a thundering
   herd. Rate-limited failures get a raised floor: retrying inside the throttle
   window is guaranteed to fail again.

**Why retries cannot create duplicate calendar events:** they don't need to be
careful. The synchronization engine is idempotent (Stage 8, invariants I5/I6) and
duplicate prevention is structural (unique constraint + deterministic event id).
A retry simply re-plans; an already-synced fixture yields NO_CHANGE.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

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
from app.exceptions.calendar import QuotaExceededError
from app.exceptions.provider import RateLimitError


class FailureKind(StrEnum):
    TRANSIENT = "transient"  # retry with backoff
    RATE_LIMITED = "rate_limited"  # retry, but not before the window clears
    PERMANENT = "permanent"  # never retry; dead-letter immediately


# Categories that will never succeed on retry. Note AuthenticationError covers
# CalendarReauthRequiredError: the user must reconnect; hammering Google will not
# fix it (Stage 8 already pauses the subscription).
_PERMANENT = (
    PermanentError,
    AuthenticationError,
    AuthorizationError,
    ValidationAppError,
    NotFoundError,
    ConflictError,
)


def classify(exc: BaseException) -> FailureKind:
    """Decide a failure's category. Order matters: specific before general."""
    if isinstance(exc, (RateLimitError, QuotaExceededError)):
        return FailureKind.RATE_LIMITED
    if isinstance(exc, _PERMANENT):
        return FailureKind.PERMANENT
    if isinstance(exc, RetryableError):
        return FailureKind.TRANSIENT
    if isinstance(exc, AppError):
        # A typed-but-unclassified application error: treat as permanent rather
        # than looping. Unknown *non*-AppError exceptions are treated as
        # transient, because they are usually infrastructure blips.
        return FailureKind.PERMANENT
    return FailureKind.TRANSIENT


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay_seconds: float = 30.0
    max_delay_seconds: float = 3600.0
    # Retrying inside a provider's throttle window cannot succeed.
    rate_limit_floor_seconds: float = 60.0

    def should_retry(self, kind: FailureKind, attempts: int) -> bool:
        if kind is FailureKind.PERMANENT:
            return False
        return attempts < self.max_attempts

    def delay_for(
        self, kind: FailureKind, attempts: int, *, rng: random.Random | None = None
    ) -> float:
        """Seconds to wait before the next attempt. Full jitter."""
        generator = rng or random
        ceiling = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** max(0, attempts - 1)))
        delay = generator.uniform(0.0, ceiling)
        if kind is FailureKind.RATE_LIMITED:
            delay = max(delay, self.rate_limit_floor_seconds)
        return round(min(delay, self.max_delay_seconds), 3)


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    kind: FailureKind
    delay_seconds: float = 0.0
    dead_letter: bool = False

    @property
    def next_retry_at(self) -> datetime | None:
        if not self.retry:
            return None
        return datetime.now(UTC) + timedelta(seconds=self.delay_seconds)


def decide(
    exc: BaseException, attempts: int, policy: RetryPolicy, *, rng: random.Random | None = None
) -> RetryDecision:
    """The single retry decision point for every worker."""
    kind = classify(exc)
    if policy.should_retry(kind, attempts):
        return RetryDecision(True, kind, policy.delay_for(kind, attempts, rng=rng))
    return RetryDecision(False, kind, dead_letter=True)
