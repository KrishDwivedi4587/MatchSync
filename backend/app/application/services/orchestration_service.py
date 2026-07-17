"""Orchestration health and metrics.

Read-only. Aggregates worker heartbeats, queue depth, job counters, the
scheduler's own heartbeat, and the synchronization *backlog* (subscriptions whose
``next_sync_at`` has passed — maintained by Stage 8, read here).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.domain.orchestration.models import JobState
from app.infrastructure.heartbeat import HeartbeatRegistry
from app.infrastructure.jobs import JobStore
from app.persistence.repositories.subscription import SubscriptionRepository
from app.persistence.repositories.system import SchedulerJobRepository

logger = get_logger(__name__)


class OrchestrationService:
    def __init__(
        self,
        store: JobStore,
        heartbeats: HeartbeatRegistry,
        subscriptions: SubscriptionRepository,
        scheduler_jobs: SchedulerJobRepository,
        *,
        stuck_after_seconds: int = 1800,
    ) -> None:
        self._store = store
        self._beats = heartbeats
        self._subscriptions = subscriptions
        self._scheduler_jobs = scheduler_jobs
        self._stuck_after = stuck_after_seconds

    async def workers(self) -> list[dict[str, Any]]:
        return await self._beats.list_workers()

    async def queues(self) -> dict[str, Any]:
        depths = await self._beats.queue_depths()
        return {"depths": depths, "total": sum(depths.values())}

    async def scheduler_status(self) -> dict[str, Any]:
        beat = await self._beats.scheduler_status()
        alive = beat is not None
        # Recurring schedule definitions live in the frozen `scheduler_jobs` table.
        definitions = await self._scheduler_jobs.list(limit=50)
        return {
            "alive": alive,
            "last_seen_at": beat.get("seen_at") if beat else None,
            "jobs": [
                {
                    "key": job.key,
                    "name": job.name,
                    "schedule": job.schedule,
                    "status": job.status.value,
                    "last_run_at": job.last_run_at.isoformat() if job.last_run_at else None,
                    "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
                }
                for job in definitions
            ],
        }

    async def backlog(self) -> dict[str, Any]:
        """Subscriptions due for synchronization but not yet processed."""
        due = await self._subscriptions.list_due(datetime.now(UTC), limit=1000)
        oldest = min((s.next_sync_at for s in due if s.next_sync_at), default=None)
        delay = (datetime.now(UTC) - _as_utc(oldest)).total_seconds() if oldest else 0.0
        return {
            "due_subscriptions": len(due),
            "oldest_due_at": oldest.isoformat() if oldest else None,
            "max_scheduling_delay_seconds": round(max(delay, 0.0), 2),
        }

    async def metrics(self) -> dict[str, Any]:
        counts = await self._store.count_by_state()
        queues = await self._beats.queue_depths()
        workers = await self._beats.list_workers()
        backlog = await self.backlog()

        succeeded = counts.get(JobState.SUCCEEDED.value, 0) + counts.get(JobState.SKIPPED.value, 0)
        failed = counts.get(JobState.FAILED.value, 0) + counts.get(JobState.DEAD_LETTER.value, 0)
        finished = succeeded + failed
        running = counts.get(JobState.RUNNING.value, 0)

        return {
            "jobs_by_state": counts,
            "queue_depth": queues,
            "queue_depth_total": sum(queues.values()),
            "workers_online": len(workers),
            "worker_utilization": round(running / len(workers), 3) if workers else 0.0,
            "success_rate": round(succeeded / finished, 4) if finished else 1.0,
            "failure_rate": round(failed / finished, 4) if finished else 0.0,
            "retrying": counts.get(JobState.RETRYING.value, 0),
            "dead_letter": counts.get(JobState.DEAD_LETTER.value, 0),
            "lock_contention": counts.get(JobState.SKIPPED.value, 0),
            "backlog": backlog,
            "redis_healthy": await self._beats.redis_healthy(),
        }

    async def health(self) -> dict[str, Any]:
        workers = await self._beats.list_workers()
        scheduler = await self._beats.scheduler_status()
        stuck = await self._store.list_stuck(older_than_seconds=self._stuck_after)
        redis_ok = await self._beats.redis_healthy()

        healthy = redis_ok and bool(workers) and scheduler is not None and not stuck
        return {
            "healthy": healthy,
            "redis": redis_ok,
            "workers_online": len(workers),
            "scheduler_alive": scheduler is not None,
            "stuck_jobs": len(stuck),
        }


def _as_utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
