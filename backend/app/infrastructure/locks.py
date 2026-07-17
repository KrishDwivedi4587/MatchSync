"""Distributed locking on Redis.

Guarantees **at-most-one active synchronization per subscription**, across any
number of worker processes and machines.

Design decisions, each with its reason:

- **`SET key token NX PX ttl`** — acquisition is a single atomic command. `NX`
  gives mutual exclusion; `PX` gives *automatic deadlock recovery*: a worker that
  crashes, is OOM-killed, or loses the network never holds the lock forever.

- **Ownership token.** The value is a random token unique to this acquisition.
  Release and renewal both compare-and-swap on it, so a worker whose lease
  already expired can never release or extend a lock that a *different* worker
  now owns. Without this, a slow worker would delete someone else's lock.

- **Compare-and-swap via `WATCH`/`MULTI`, not Lua.** Redis Lua would also be
  atomic, but scripting is not available on every Redis-compatible backend (and
  not in the in-memory fake used by the tests). `WATCH` + transaction gives the
  same atomicity with broader support.

- **Lease renewal.** The TTL is deliberately shorter than a task's time limit, and
  a background renewer extends it every `ttl/3`. Short TTL means fast deadlock
  recovery; renewal means long jobs don't lose their lock mid-run.

- **A lost lock is safe.** If renewal fails (Redis restart, GC pause), the run
  continues and we log it. The synchronization engine is idempotent and duplicate
  prevention is structural (unique constraint + deterministic event id), so two
  concurrent runs converge rather than corrupt. The lock is an *optimization for
  quota and latency*, not the correctness mechanism.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from dataclasses import dataclass
from types import TracebackType

import redis.asyncio as aioredis
from redis.exceptions import WatchError

from app.core.logging import get_logger

logger = get_logger(__name__)

LOCK_PREFIX = "lock:"


@dataclass(frozen=True)
class LockHandle:
    name: str
    token: str

    @property
    def key(self) -> str:
        return f"{LOCK_PREFIX}{self.name}"


class LockManager:
    """Acquire, renew, and release Redis leases."""

    def __init__(self, client: aioredis.Redis, *, ttl_seconds: int = 120) -> None:
        self._redis = client
        self._ttl = ttl_seconds

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    async def acquire(self, name: str, *, ttl_seconds: int | None = None) -> LockHandle | None:
        """Atomically take the lock, or return None if someone else holds it."""
        token = secrets.token_urlsafe(24)
        ttl = ttl_seconds or self._ttl
        acquired = await self._redis.set(f"{LOCK_PREFIX}{name}", token, nx=True, px=ttl * 1000)
        if not acquired:
            logger.info("lock.contended", lock=name)
            return None
        logger.debug("lock.acquired", lock=name)
        return LockHandle(name=name, token=token)

    async def _compare_and_swap(self, handle: LockHandle, *, renew_ms: int | None) -> bool:
        """CAS on the ownership token: renew when ``renew_ms``, else delete."""
        async with self._redis.pipeline() as pipe:
            while True:
                try:
                    await pipe.watch(handle.key)
                    current = await pipe.get(handle.key)
                    if current != handle.token:
                        # redis-py 6.4 leaves Pipeline.unwatch/multi unannotated
                        # (unlike watch/execute), so these two calls cannot be
                        # typed without forking the stubs.
                        await pipe.unwatch()  # type: ignore[no-untyped-call]
                        return False  # expired, or owned by someone else now
                    pipe.multi()  # type: ignore[no-untyped-call]
                    if renew_ms is None:
                        pipe.delete(handle.key)
                    else:
                        pipe.pexpire(handle.key, renew_ms)
                    await pipe.execute()
                    return True
                except WatchError:
                    continue  # someone touched the key; re-read and retry

    async def renew(self, handle: LockHandle, *, ttl_seconds: int | None = None) -> bool:
        ttl = ttl_seconds or self._ttl
        renewed = await self._compare_and_swap(handle, renew_ms=ttl * 1000)
        if not renewed:
            logger.warning("lock.lost", lock=handle.name)
        return renewed

    async def release(self, handle: LockHandle) -> bool:
        released = await self._compare_and_swap(handle, renew_ms=None)
        logger.debug("lock.released" if released else "lock.release_noop", lock=handle.name)
        return released

    async def is_held(self, name: str) -> bool:
        return bool(await self._redis.exists(f"{LOCK_PREFIX}{name}") == 1)

    def guard(self, name: str, *, ttl_seconds: int | None = None) -> LockGuard:
        return LockGuard(self, name, ttl_seconds or self._ttl)


class LockNotAcquiredError(Exception):
    """The lock is held elsewhere. Callers treat this as a correct no-op."""


class LockGuard:
    """Async context manager holding a lock and renewing its lease.

    ``async with manager.guard("sync:subscription:x"):`` raises ``LockNotAcquiredError``
    when a concurrent worker already owns the subscription — the caller then marks
    the job SKIPPED, which is the correct outcome for a duplicate delivery.
    """

    def __init__(self, manager: LockManager, name: str, ttl_seconds: int) -> None:
        self._manager = manager
        self._name = name
        self._ttl = ttl_seconds
        self._handle: LockHandle | None = None
        self._renewer: asyncio.Task[None] | None = None

    @property
    def handle(self) -> LockHandle | None:
        return self._handle

    async def __aenter__(self) -> LockHandle:
        handle = await self._manager.acquire(self._name, ttl_seconds=self._ttl)
        if handle is None:
            raise LockNotAcquiredError(self._name)
        self._handle = handle
        # Renew at a third of the TTL: two consecutive renewal failures still
        # leave a full third of the lease to recover in. The floor is a small
        # epsilon, NOT one second — a 1s lease renewed after 1s has already
        # expired, which silently loses the lock on every short-TTL lease.
        self._renewer = asyncio.create_task(self._renew_loop(max(self._ttl / 3, 0.05)))
        return handle

    async def _renew_loop(self, interval: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                assert self._handle is not None
                if not await self._manager.renew(self._handle, ttl_seconds=self._ttl):
                    return  # lease lost; the engine's idempotency keeps us safe
        except asyncio.CancelledError:  # pragma: no cover - normal shutdown
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._renewer:
            self._renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._renewer
        if self._handle:
            await self._manager.release(self._handle)
