"""Shared base for HTTP sports providers.

Reuses Stage 5's ``ResilientHttpClient`` (retries, backoff, jitter,
``Retry-After``, connection pooling) rather than duplicating it. Adds the parts
that are provider-specific:

- **API-key authentication** via a configurable header/scheme.
- **Per-provider retry policy** — each provider constructs its own
  ``ResilientHttpClient`` with its own ``RetryPolicy`` and its own pooled
  ``httpx.AsyncClient``, so a chatty provider's limits never affect another.
- **Circuit breaker** — one per provider.
- **Compression** — ``Accept-Encoding: gzip`` on every request.
- **Error mapping** — HTTP status -> application exception, once, here.
- **Latency logging** — never logs API keys or payload bodies.

Concrete providers only implement ``list_*``/``get_*`` and their normalization.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.logging import get_logger
from app.domain.ports.sports_provider import ProviderCapability, ProviderConfig, ProviderInfo
from app.exceptions.sports import (
    CapabilityNotSupportedError,
    MalformedResponseError,
    ProviderAuthenticationError,
    ProviderUnavailableError,
    RateLimitError,
    SportsProviderError,
)
from app.infrastructure.http.circuit_breaker import CircuitBreaker
from app.infrastructure.http.resilient import ResilientHttpClient, RetryPolicy

logger = get_logger(__name__)


def _is_retryable(response: httpx.Response) -> bool:
    """Retry throttling and server faults; never auth or client errors."""
    return response.status_code == 429 or response.status_code >= 500


def build_http_client(
    config: ProviderConfig, client: httpx.AsyncClient | None = None
) -> ResilientHttpClient:
    """One resilient client per provider, honouring its retry policy."""
    return ResilientHttpClient(
        timeout=config.timeout_seconds,
        retry=RetryPolicy(max_attempts=config.max_attempts),
        client=client,
    )


class BaseHttpSportsProvider:
    """Transport + error mapping. Subclasses add endpoints and normalization."""

    key: str = "base"
    name: str = "Base"
    version: str = "1.0"
    capabilities: frozenset[ProviderCapability] = frozenset()
    supported_sports: tuple[str, ...] = ()

    def __init__(
        self,
        config: ProviderConfig,
        http: ResilientHttpClient,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._config = config
        self._http = http
        self._breaker = breaker or CircuitBreaker(config.key)

    # --- introspection -----------------------------------------------------
    @property
    def config(self) -> ProviderConfig:
        return self._config

    def provider_info(self) -> ProviderInfo:
        return ProviderInfo(
            key=self.key,
            name=self.name,
            version=self.version,
            capabilities=self.capabilities,
            supported_sports=self.supported_sports,
            configured=self._config.configured,
        )

    def supports(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities

    def require_capability(self, capability: ProviderCapability) -> None:
        if not self.supports(capability):
            raise CapabilityNotSupportedError(
                f"Provider '{self.key}' does not support '{capability.value}'."
            )

    # --- transport ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            **self._config.extra_headers,
        }
        if self._config.api_key:
            headers[self._config.auth_header] = f"{self._config.auth_scheme}{self._config.api_key}"
        return headers

    def _map_error(self, response: httpx.Response) -> Exception:
        status = response.status_code
        if status in (401, 403):
            return ProviderAuthenticationError()
        if status == 404:
            return SportsProviderError("The requested provider resource was not found.")
        if status == 429:
            return RateLimitError()
        if status >= 500:
            return ProviderUnavailableError()
        return SportsProviderError(f"Provider '{self.key}' returned HTTP {status}.")

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a provider endpoint, returning parsed JSON. Never logs secrets."""
        if not self._config.configured:
            raise ProviderAuthenticationError(
                f"Provider '{self.key}' is not configured (missing base URL or API key)."
            )

        url = f"{self._config.base_url.rstrip('/')}/{path.lstrip('/')}"

        async def _operation() -> Any:
            started = time.perf_counter()
            response = await self._http.request(
                "GET", url, headers=self._headers(), params=params, is_retryable=_is_retryable
            )
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "sports.provider.call",
                provider=self.key,
                path=path,  # path only — never params (may carry keys) or bodies
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
            if response.status_code >= 400:
                raise self._map_error(response)
            try:
                return response.json()
            except ValueError as exc:
                raise MalformedResponseError(
                    f"Provider '{self.key}' returned non-JSON content."
                ) from exc

        return await self._breaker.call(_operation)

    # --- optional-capability default ---------------------------------------
    async def get_standings(self, competition_id: str) -> list:
        """Default: unsupported. Providers with STANDINGS override this."""
        self.require_capability(ProviderCapability.STANDINGS)
        raise NotImplementedError  # pragma: no cover - guarded above
