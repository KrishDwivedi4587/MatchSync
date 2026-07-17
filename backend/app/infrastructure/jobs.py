"""Redis-backed job store.

**Why Redis and not Postgres.** Stage 1: *"Redis holds only ephemeral/derived
state; losing Redis loses in-flight jobs and caches, never durable truth."* A job
record is in-flight orchestration state. The durable business outcome of a sync
already lives in ``sync_history``/``sync_operations`` (Stage 8) and of an import
in ``import_runs`` (Stage 7). Losing Redis loses the job *log*, never a calendar
event or a fixture — and the scheduler simply re-enqueues from
``subscriptions.next_sync_at``. Hence **no schema change**.

Keys:
    job:{id}            JSON document
    jobs:index          ZSET (score = created_at epoch) for listing
    jobs:user:{uid}     ZSET, per-user listing
    jobs:dead           ZSET, the dead-letter queue
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import redis.asyncio as aioredis

from app.core.logging import get_logger
from app.domain.orchestration.models import Job, JobState, JobType

logger = get_logger(__name__)

JOB_KEY = "job:{id}"
INDEX_KEY = "jobs:index"
USER_INDEX_KEY = "jobs:user:{user_id}"
DEAD_LETTER_KEY = "jobs:dead"


class JobStore:
    def __init__(self, client: aioredis.Redis, *, retention_seconds: int = 604_800) -> None:
        self._redis = client
        self._retention = retention_seconds

    # --- writes -----------------------------------------------------------
    async def save(self, job: Job) -> Job:
        """Persist a job document and keep the indexes in step."""
        score = job.created_at.timestamp()
        key = JOB_KEY.format(id=job.id)

        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(key, json.dumps(job.to_dict()), ex=self._retention)
            pipe.zadd(INDEX_KEY, {str(job.id): score})
            if job.user_id:
                pipe.zadd(USER_INDEX_KEY.format(user_id=job.user_id), {str(job.id): score})
            if job.state is JobState.DEAD_LETTER:
                pipe.zadd(DEAD_LETTER_KEY, {str(job.id): score})
            elif job.state is JobState.QUEUED:
                pipe.zrem(DEAD_LETTER_KEY, str(job.id))
            await pipe.execute()
        return job

    async def delete(self, job_id: uuid.UUID) -> None:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(JOB_KEY.format(id=job_id))
            pipe.zrem(INDEX_KEY, str(job_id))
            pipe.zrem(DEAD_LETTER_KEY, str(job_id))
            await pipe.execute()

    # --- reads ------------------------------------------------------------
    async def get(self, job_id: uuid.UUID) -> Job | None:
        raw = await self._redis.get(JOB_KEY.format(id=job_id))
        if raw is None:
            return None
        return Job.from_dict(json.loads(raw))

    async def _load_many(self, ids: Sequence[str]) -> list[Job]:
        if not ids:
            return []
        raw_docs = await self._redis.mget([JOB_KEY.format(id=i) for i in ids])
        jobs: list[Job] = []
        for raw in raw_docs:
            if raw:
                jobs.append(Job.from_dict(json.loads(raw)))
        return jobs

    async def list(
        self,
        *,
        user_id: uuid.UUID | None = None,
        states: set[JobState] | None = None,
        types: set[JobType] | None = None,
        limit: int = 50,
    ) -> list[Job]:
        """Newest-first listing. Filters are applied after a bounded index scan."""
        index = USER_INDEX_KEY.format(user_id=user_id) if user_id else INDEX_KEY
        # Over-fetch a little so post-filtering still fills the page.
        window = limit * 5 if (states or types) else limit
        ids = await self._redis.zrevrange(index, 0, max(window - 1, 0))
        jobs = await self._load_many(ids)

        if states:
            jobs = [j for j in jobs if j.state in states]
        if types:
            jobs = [j for j in jobs if j.type in types]
        return jobs[:limit]

    async def list_dead_letter(self, *, limit: int = 50) -> list[Job]:
        ids = await self._redis.zrevrange(DEAD_LETTER_KEY, 0, max(limit - 1, 0))
        return await self._load_many(ids)

    async def count_by_state(self) -> dict[str, int]:
        ids = await self._redis.zrevrange(INDEX_KEY, 0, 999)
        counts: dict[str, int] = {}
        for job in await self._load_many(ids):
            counts[job.state.value] = counts.get(job.state.value, 0) + 1
        return counts

    async def list_stuck(self, *, older_than_seconds: int) -> list[Job]:
        """RUNNING jobs whose worker died without transitioning them."""
        cutoff = datetime.now(UTC).timestamp() - older_than_seconds
        ids = await self._redis.zrevrange(INDEX_KEY, 0, 999)
        stuck: list[Job] = []
        for job in await self._load_many(ids):
            if (
                job.state is JobState.RUNNING
                and job.started_at
                and job.started_at.timestamp() < cutoff
            ):
                stuck.append(job)
        return stuck

    async def prune(self, *, older_than_seconds: int) -> int:
        """Drop index entries whose documents have already expired."""
        cutoff = datetime.now(UTC).timestamp() - older_than_seconds
        removed = await self._redis.zremrangebyscore(INDEX_KEY, 0, cutoff)
        return int(removed or 0)
