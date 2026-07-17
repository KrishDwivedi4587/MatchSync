"""Worker and scheduler heartbeats, plus queue depth probes.

Liveness is expressed as a **TTL'd Redis key**, not a table row. A worker that
crashes stops refreshing its key and disappears from ``GET /workers`` within
``ttl`` seconds — no tombstone to clean up, no stale rows if the process is
SIGKILLed. This is the same reasoning as the lock's automatic expiry.

Queue depth is read straight from the Celery broker: with the Redis broker each
named queue is a Redis list whose key *is* the queue name, so ``LLEN`` is the
backlog. Cheap, exact, and requires no Celery inspect broadcast.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis

from app.domain.orchestration.models import Queue

WORKER_KEY = "orchestration:worker:{name}"
WORKER_PATTERN = "orchestration:worker:*"
SCHEDULER_KEY = "orchestration:scheduler"


class HeartbeatRegistry:
    def __init__(self, client: aioredis.Redis, *, ttl_seconds: int = 60) -> None:
        self._redis = client
        self._ttl = ttl_seconds

    # --- workers ----------------------------------------------------------
    async def worker_beat(self, name: str, **info: Any) -> None:
        payload = {"name": name, "seen_at": datetime.now(UTC).isoformat(), **info}
        await self._redis.set(WORKER_KEY.format(name=name), json.dumps(payload), ex=self._ttl)

    async def worker_gone(self, name: str) -> None:
        """Explicit deregistration on graceful shutdown (TTL covers crashes)."""
        await self._redis.delete(WORKER_KEY.format(name=name))

    async def list_workers(self) -> list[dict[str, Any]]:
        workers: list[dict[str, Any]] = []
        async for key in self._redis.scan_iter(match=WORKER_PATTERN, count=100):
            raw = await self._redis.get(key)
            if raw:
                workers.append(json.loads(raw))
        return sorted(workers, key=lambda w: w.get("name", ""))

    # --- scheduler --------------------------------------------------------
    async def scheduler_beat(self, **info: Any) -> None:
        payload = {"seen_at": datetime.now(UTC).isoformat(), **info}
        await self._redis.set(SCHEDULER_KEY, json.dumps(payload), ex=self._ttl)

    async def scheduler_status(self) -> dict[str, Any] | None:
        raw = await self._redis.get(SCHEDULER_KEY)
        return json.loads(raw) if raw else None

    # --- queues -----------------------------------------------------------
    async def queue_depths(self) -> dict[str, int]:
        """Backlog per queue. With the Redis broker, a queue is a list."""
        depths: dict[str, int] = {}
        for queue in Queue:
            depths[queue.value] = int(await self._redis.llen(queue.value) or 0)
        return depths

    async def redis_healthy(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False
