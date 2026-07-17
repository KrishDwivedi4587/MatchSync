"""Redis-backed key/value store used for auth sessions and OAuth state.

Stage 1 endorsed a Redis/DB-backed session for *instant revocation*; Stage 3's
schema is frozen (no sessions table), so we use the Redis already in the stack.
A narrow ``SessionStore`` protocol keeps the session service decoupled from
Redis and trivially fakeable in tests.
"""

from __future__ import annotations

from typing import Protocol

import redis.asyncio as aioredis

from app.core.config import get_settings


class SessionStore(Protocol):
    """Minimal async KV + set operations the session layer needs."""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def add_member(self, key: str, member: str) -> None: ...
    async def members(self, key: str) -> set[str]: ...
    async def remove_member(self, key: str, member: str) -> None: ...


class RedisSessionStore:
    """``SessionStore`` backed by an async Redis client (decode_responses=True)."""

    def __init__(self, client: aioredis.Redis) -> None:
        self._c = client

    async def get(self, key: str) -> str | None:
        return await self._c.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._c.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._c.delete(key)

    async def add_member(self, key: str, member: str) -> None:
        await self._c.sadd(key, member)

    async def members(self, key: str) -> set[str]:
        return set(await self._c.smembers(key))

    async def remove_member(self, key: str, member: str) -> None:
        await self._c.srem(key, member)


_client: aioredis.Redis | None = None


def get_redis_client() -> aioredis.Redis:
    """Process-wide async Redis client (lazy singleton)."""
    global _client
    if _client is None:
        _client = aioredis.from_url(
            get_settings().redis_url, encoding="utf-8", decode_responses=True
        )
    return _client
