"""Shared resilient outbound HTTP client.

One pooled ``httpx.AsyncClient`` per process (connection reuse), with:
- hard per-request timeouts,
- bounded retries with exponential backoff + full jitter,
- ``Retry-After`` honoured when the server supplies it,
- a caller-supplied ``is_retryable`` predicate so each provider decides which
  4xx responses are transient (e.g. Google's rateLimitExceeded) and which are
  terminal (quotaExceeded).

Retry exhaustion raises ``ProviderUnavailableError``; the caller never sees a
raw transport exception.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger
from app.exceptions.provider import ProviderUnavailableError

logger = get_logger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 8.0


def _default_is_retryable(response: httpx.Response) -> bool:
    return response.status_code == 429 or response.status_code >= 500


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None  # HTTP-date form: fall back to computed backoff


class ResilientHttpClient:
    """Retrying HTTP client. Inject a custom ``client`` in tests (MockTransport)."""

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        retry: RetryPolicy | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._retry = retry or RetryPolicy()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    def _backoff(self, attempt: int) -> float:
        # Exponential with full jitter: random in [0, min(max, base * 2^n)].
        ceiling = min(self._retry.max_delay, self._retry.base_delay * (2**attempt))
        return random.uniform(0, ceiling)

    async def request(
        self,
        method: str,
        url: str,
        *,
        is_retryable: Callable[[httpx.Response], bool] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        retryable = is_retryable or _default_is_retryable
        last_error: Exception | None = None

        for attempt in range(self._retry.max_attempts):
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:  # timeout, DNS, connection reset
                last_error = exc
                if attempt == self._retry.max_attempts - 1:
                    break
                await asyncio.sleep(self._backoff(attempt))
                continue

            if not retryable(response) or attempt == self._retry.max_attempts - 1:
                return response

            delay = _retry_after_seconds(response) or self._backoff(attempt)
            logger.warning(
                "http_retry",
                method=method,
                status_code=response.status_code,
                attempt=attempt + 1,
                delay_seconds=round(delay, 3),
            )
            await asyncio.sleep(delay)

        logger.error("http_retries_exhausted", method=method, error=str(last_error))
        raise ProviderUnavailableError("The provider did not respond successfully after retries.")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


_shared: ResilientHttpClient | None = None


def get_http_client() -> ResilientHttpClient:
    """Process-wide pooled client (lazy singleton)."""
    global _shared
    if _shared is None:
        _shared = ResilientHttpClient()
    return _shared
