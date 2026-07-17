"""Redis-backed key/value store used for auth sessions and OAuth state.

Stage 1 endorsed a Redis/DB-backed session for *instant revocation*; Stage 3's
schema is frozen (no sessions table), so we use the Redis already in the stack.
A narrow ``SessionStore`` protocol keeps the session service decoupled from
Redis and trivially fakeable in tests.
"""

from __future__ import annotations

import builtins
from collections.abc import Awaitable
from typing import Protocol, TypeVar

import redis.asyncio as aioredis

from app.core.config import get_settings

_T = TypeVar("_T")


async def resolve_response(value: Awaitable[_T] | _T) -> _T:
    """Await a redis-py command result.

    redis-py annotates every command as ``Awaitable[T] | T`` because one client
    class backs both its sync and async APIs; on ``redis.asyncio`` the value is
    always awaitable. The isinstance check narrows the union without a cast.
    """
    if isinstance(value, Awaitable):
        return await value
    return value


class SessionStore(Protocol):
    """Minimal async KV + set operations the session layer needs."""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def add_member(self, key: str, member: str) -> None: ...

    # ``builtins.set``: the ``set`` method above shadows the builtin in this
    # class body's annotation scope.
    async def members(self, key: str) -> builtins.set[str]: ...
    async def remove_member(self, key: str, member: str) -> None: ...


class RedisSessionStore:
    """``SessionStore`` backed by an async Redis client (decode_responses=True)."""

    def __init__(self, client: aioredis.Redis) -> None:
        self._c = client

    async def get(self, key: str) -> str | None:
        value = await resolve_response(self._c.get(key))
        return value if isinstance(value, str) else None

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._c.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._c.delete(key)

    async def add_member(self, key: str, member: str) -> None:
        await resolve_response(self._c.sadd(key, member))

    async def members(self, key: str) -> builtins.set[str]:
        return set(await resolve_response(self._c.smembers(key)))

    async def remove_member(self, key: str, member: str) -> None:
        await resolve_response(self._c.srem(key, member))


_client: aioredis.Redis | None = None


def get_redis_client() -> aioredis.Redis:
    """Process-wide async Redis client (lazy singleton)."""
    global _client
    if _client is None:
        # redis-py 6.4 leaves ``from_url`` unannotated; it is the documented
        # constructor, so the untyped call is unavoidable here.
        _client = aioredis.from_url(  # type: ignore[no-untyped-call]
            get_settings().redis_url, encoding="utf-8", decode_responses=True
        )
    return _client
