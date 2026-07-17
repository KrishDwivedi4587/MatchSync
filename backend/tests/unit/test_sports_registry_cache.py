"""Unit tests for the provider registry, capabilities, and the cache layer."""

from __future__ import annotations

import json

import pytest

from app.domain.ports.sports_provider import ProviderCapability, ProviderInfo
from app.exceptions.sports import ProviderNotFoundError
from app.infrastructure.cache import InMemoryCache, cache_key, cached_json
from app.infrastructure.providers.registry import SportsProviderRegistry


class StubProvider:
    def __init__(self, key: str, sports: tuple[str, ...], caps: set[ProviderCapability]) -> None:
        self.key = key
        self.name = key.title()
        self.version = "1.0"
        self.capabilities = frozenset(caps)
        self.supported_sports = sports

    def provider_info(self) -> ProviderInfo:
        return ProviderInfo(
            key=self.key,
            name=self.name,
            version=self.version,
            capabilities=self.capabilities,
            supported_sports=self.supported_sports,
        )


# --- registry --------------------------------------------------------------
def test_register_indexes_by_provider_and_sport() -> None:
    registry = SportsProviderRegistry()
    football = StubProvider("football-api", ("football",), {ProviderCapability.STANDINGS})
    registry.register(football)

    assert registry.get("football-api") is football
    assert registry.get_for_sport("football") is football
    assert registry.sport_keys() == ["football"]


def test_one_provider_can_serve_multiple_sports() -> None:
    registry = SportsProviderRegistry()
    multi = StubProvider("multi-api", ("cricket", "tennis"), set())
    registry.register(multi)
    assert registry.get_for_sport("cricket") is multi
    assert registry.get_for_sport("tennis") is multi


def test_unknown_keys_raise_provider_not_found() -> None:
    registry = SportsProviderRegistry()
    with pytest.raises(ProviderNotFoundError):
        registry.get("nope")
    with pytest.raises(ProviderNotFoundError):
        registry.get_for_sport("formula1")


def test_adding_formula1_requires_only_implement_and_register() -> None:
    """The Stage 6 contract: two steps, no service/schema/engine change."""
    registry = SportsProviderRegistry()
    registry.register(StubProvider("f1-api", ("formula1",), {ProviderCapability.SEASONS}))

    assert registry.get_for_sport("formula1").key == "f1-api"
    assert registry.capabilities()["f1-api"] == ["seasons"]


def test_capability_matrix_and_supports() -> None:
    registry = SportsProviderRegistry()
    registry.register(StubProvider("a", ("x",), {ProviderCapability.STANDINGS}))
    registry.register(StubProvider("b", ("y",), {ProviderCapability.BRACKETS}))

    assert registry.capabilities() == {"a": ["standings"], "b": ["brackets"]}
    assert registry.supports("a", ProviderCapability.STANDINGS) is True
    assert registry.supports("b", ProviderCapability.STANDINGS) is False


def test_plugin_discovery_never_breaks_boot() -> None:
    # No plugins installed -> returns 0, does not raise.
    assert SportsProviderRegistry().load_plugins() == 0


# --- cache -----------------------------------------------------------------
async def test_in_memory_cache_roundtrip_and_expiry() -> None:
    cache = InMemoryCache()
    await cache.set("k", "v", 60)
    assert await cache.get("k") == "v"

    await cache.set("expired", "v", -1)  # already in the past
    assert await cache.get("expired") is None


async def test_delete_prefix_clears_a_provider_namespace() -> None:
    cache = InMemoryCache()
    await cache.set(cache_key("football-api", "competitions"), "a", 60)
    await cache.set(cache_key("football-api", "teams"), "b", 60)
    await cache.set(cache_key("valorant", "teams"), "c", 60)

    await cache.delete_prefix(cache_key("football-api"))
    assert await cache.get(cache_key("football-api", "teams")) is None
    assert await cache.get(cache_key("valorant", "teams")) == "c"


async def test_cached_json_is_read_through_and_calls_loader_once() -> None:
    cache = InMemoryCache()
    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        return [{"a": 1}]

    first = await cached_json(cache, "k", 60, loader)
    second = await cached_json(cache, "k", 60, loader)  # served from cache
    assert first == second == [{"a": 1}]
    assert calls["n"] == 1


async def test_cached_json_recovers_from_corrupt_entries() -> None:
    cache = InMemoryCache()
    await cache.set("k", "{not json", 60)

    async def loader():
        return {"ok": True}

    assert await cached_json(cache, "k", 60, loader) == {"ok": True}
    assert json.loads(await cache.get("k")) == {"ok": True}  # repopulated
