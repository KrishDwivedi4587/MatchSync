"""Sports provider registry + factory.

Expands the ``CalendarProviderFactory`` pattern from Stage 5: pure lookup, no
per-provider conditionals anywhere above this module.

Two indexes:
- ``provider_key -> provider`` (e.g. "football-api")
- ``sport_key -> provider``    (e.g. "football"), because a Sport row's
  ``provider_key`` column decides who serves it (Stage 1/3).

A provider may serve several sports (a multi-sport vendor), so the sport index
is many-to-one.

**Adding Formula 1** is exactly two steps:
    1. implement ``SportsProvider`` in ``providers/formula1/``
    2. ``registry.register(Formula1Provider(...))``
No service, schema, or engine change.

Third-party plugins can register via the ``matchsync.sports_providers`` entry
point group without living in this repository.
"""

from __future__ import annotations

from importlib.metadata import entry_points

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.ports.sports_provider import (
    ProviderCapability,
    ProviderConfig,
    ProviderInfo,
    SportsProvider,
)
from app.exceptions.sports import ProviderNotFoundError
from app.infrastructure.http.circuit_breaker import CircuitBreaker
from app.infrastructure.providers.base import build_http_client
from app.infrastructure.providers.basketball import BasketballProvider
from app.infrastructure.providers.football import FootballProvider
from app.infrastructure.providers.valorant import ValorantProvider

logger = get_logger(__name__)

ENTRY_POINT_GROUP = "matchsync.sports_providers"


class SportsProviderRegistry:
    def __init__(self) -> None:
        self._by_key: dict[str, SportsProvider] = {}
        self._by_sport: dict[str, SportsProvider] = {}

    # --- registration ------------------------------------------------------
    def register(self, provider: SportsProvider) -> None:
        self._by_key[provider.key] = provider
        for sport_key in provider.supported_sports:
            self._by_sport[sport_key] = provider
        logger.info(
            "sports.provider.registered",
            provider=provider.key,
            version=provider.version,
            sports=list(provider.supported_sports),
        )

    def load_plugins(self) -> int:
        """Discover and register third-party providers via entry points."""
        registered = 0
        try:
            discovered = entry_points(group=ENTRY_POINT_GROUP)
        except Exception:
            logger.warning("sports.provider.plugin_discovery_failed")
            return 0

        for entry in discovered:
            try:
                self.register(entry.load()())
                registered += 1
            except Exception as exc:
                logger.warning("sports.provider.plugin_failed", plugin=entry.name, reason=str(exc))
        return registered

    # --- lookup ------------------------------------------------------------
    def get(self, provider_key: str) -> SportsProvider:
        provider = self._by_key.get(provider_key)
        if provider is None:
            raise ProviderNotFoundError(f"No provider registered with key '{provider_key}'.")
        return provider

    def get_for_sport(self, sport_key: str) -> SportsProvider:
        provider = self._by_sport.get(sport_key)
        if provider is None:
            raise ProviderNotFoundError(f"No provider registered for sport '{sport_key}'.")
        return provider

    def all(self) -> list[SportsProvider]:
        return list(self._by_key.values())

    def sport_keys(self) -> list[str]:
        return sorted(self._by_sport)

    # --- capabilities ------------------------------------------------------
    def supports(self, provider_key: str, capability: ProviderCapability) -> bool:
        return capability in self.get(provider_key).capabilities

    def capabilities(self) -> dict[str, list[str]]:
        return {
            provider.key: sorted(c.value for c in provider.capabilities) for provider in self.all()
        }

    def provider_infos(self) -> list[ProviderInfo]:
        return [provider.provider_info() for provider in self.all()]


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------
def _config(
    settings: Settings, key: str, name: str, base_url: str, api_key: str, **overrides
) -> ProviderConfig:
    return ProviderConfig(
        key=key,
        name=name,
        base_url=base_url,
        api_key=api_key or None,
        cache_ttl_seconds=settings.sports_cache_ttl_seconds,
        **overrides,
    )


def build_registry(settings: Settings) -> SportsProviderRegistry:
    """Construct the default registry from configuration.

    Each provider gets its own resilient HTTP client (own retry policy, own
    connection pool) and its own circuit breaker, so one failing vendor cannot
    degrade another.
    """
    registry = SportsProviderRegistry()

    specs = [
        (
            FootballProvider,
            _config(
                settings,
                FootballProvider.key,
                FootballProvider.name,
                settings.football_api_base_url,
                settings.football_api_key.get_secret_value(),
                auth_header="X-Auth-Token",
                max_attempts=4,
            ),
        ),
        (
            ValorantProvider,
            _config(
                settings,
                ValorantProvider.key,
                ValorantProvider.name,
                settings.valorant_api_base_url,
                settings.valorant_api_key.get_secret_value(),
                auth_header="x-api-key",
                # Community API: fewer retries, shorter timeout.
                max_attempts=3,
                timeout_seconds=8.0,
            ),
        ),
        (
            BasketballProvider,
            _config(
                settings,
                BasketballProvider.key,
                BasketballProvider.name,
                settings.basketball_api_base_url,
                settings.basketball_api_key.get_secret_value(),
                auth_header="Authorization",
                max_attempts=4,
            ),
        ),
    ]

    for provider_cls, config in specs:
        registry.register(
            provider_cls(config, build_http_client(config), CircuitBreaker(config.key))
        )

    registry.load_plugins()
    return registry


_registry: SportsProviderRegistry | None = None


def get_sports_registry(settings: Settings) -> SportsProviderRegistry:
    """Process-wide registry (lazy singleton)."""
    global _registry
    if _registry is None:
        _registry = build_registry(settings)
    return _registry
