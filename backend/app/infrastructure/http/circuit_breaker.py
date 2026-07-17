"""Circuit breaker for outbound provider calls.

Complements (does not replace) the retry logic in ``ResilientHttpClient``:
retries handle a *transient blip*, the breaker handles a *sustained outage*. Once
a provider has failed ``failure_threshold`` times, the breaker opens and calls
fail fast for ``reset_timeout`` seconds instead of burning retry budget and
latency on a dead endpoint.

States: CLOSED -> (failures) -> OPEN -> (timeout) -> HALF_OPEN -> (success)
-> CLOSED, or (failure) -> OPEN.

One breaker per provider, so a broken football API never trips basketball.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from app.core.logging import get_logger
from app.exceptions.provider import ProviderUnavailableError

logger = get_logger(__name__)

T = TypeVar("T")


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class CircuitBreakerConfig:
    failure_threshold: int = 5
    reset_timeout_seconds: float = 30.0


class CircuitBreaker:
    def __init__(self, name: str, config: CircuitBreakerConfig | None = None) -> None:
        self._name = name
        self._config = config or CircuitBreakerConfig()
        self._failures = 0
        self._opened_at: float | None = None
        self._state = BreakerState.CLOSED

    @property
    def state(self) -> BreakerState:
        if (
            self._state is BreakerState.OPEN
            and self._opened_at is not None
            and time.monotonic() - self._opened_at >= self._config.reset_timeout_seconds
        ):
            self._state = BreakerState.HALF_OPEN
            logger.info("provider.breaker.half_open", provider=self._name)
        return self._state

    def record_success(self) -> None:
        if self._state is not BreakerState.CLOSED:
            logger.info("provider.breaker.closed", provider=self._name)
        self._failures = 0
        self._opened_at = None
        self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._config.failure_threshold:
            self._state = BreakerState.OPEN
            self._opened_at = time.monotonic()
            logger.warning("provider.breaker.open", provider=self._name, failures=self._failures)

    async def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run ``operation`` unless the circuit is open."""
        if self.state is BreakerState.OPEN:
            raise ProviderUnavailableError(
                f"Provider '{self._name}' is unavailable (circuit open)."
            )
        try:
            result = await operation()
        except ProviderUnavailableError:
            self.record_failure()
            raise
        self.record_success()
        return result
