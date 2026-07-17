"""Cache abstraction for provider metadata.

A narrow ``Cache`` protocol with two backends:

- ``RedisCache``    — production. Shared across API and (future) worker
                      processes, survives restarts, so a cold start never
                      hammers a provider's rate limit.
- ``InMemoryCache`` — tests and single-process dev. Same semantics, no server.

**What is cached:** provider *metadata* only (sports, competitions, teams).
Fixtures are never cached — they are volatile and belong to Stage 7.

**TTL:** per-provider (``ProviderConfig.cache_ttl_seconds``). Reference data
changes rarely; a stale competition name for an hour is harmless, while an extra
API call against a 10-req/min quota is not.

**Invalidation:** ``delete_prefix`` clears a provider's namespace after a
metadata refresh, so the refresh result is immediately visible.

**Cold start:** a miss simply calls the provider and repopulates; there is no
thundering-herd lock because metadata reads are rare and idempotent.
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

import redis.asyncio as aioredis

from app.core.logging import get_logger

logger = get_logger(__name__)

CACHE_PREFIX = "sports"


class Cache(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def delete_prefix(self, prefix: str) -> None: ...


class InMemoryCache:
    """Process-local cache with TTL. Not shared across workers."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float]] = {}

    async def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at < time.monotonic():
            self._store.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._store[key] = (value, time.monotonic() + ttl_seconds)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def delete_prefix(self, prefix: str) -> None:
        for key in [k for k in self._store if k.startswith(prefix)]:
            self._store.pop(key, None)


class RedisCache:
    """``Cache`` backed by the Redis already in the stack (Stage 4)."""

    def __init__(self, client: aioredis.Redis) -> None:
        self._c = client

    async def get(self, key: str) -> str | None:
        return await self._c.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._c.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._c.delete(key)

    async def delete_prefix(self, prefix: str) -> None:
        async for key in self._c.scan_iter(match=f"{prefix}*", count=200):
            await self._c.delete(key)


def cache_key(*parts: str) -> str:
    return ":".join((CACHE_PREFIX, *parts))


async def cached_json(
    cache: Cache,
    key: str,
    ttl_seconds: int,
    loader,
    *,
    label: str = "",
) -> Any:
    """Read-through JSON cache. Logs hits and misses (never payload contents)."""
    raw = await cache.get(key)
    if raw is not None:
        logger.debug("sports.cache.hit", key=key, label=label)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("sports.cache.corrupt", key=key)
            await cache.delete(key)

    logger.debug("sports.cache.miss", key=key, label=label)
    value = await loader()
    await cache.set(key, json.dumps(value), ttl_seconds)
    return value
