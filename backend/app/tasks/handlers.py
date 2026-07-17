"""Job handlers: the thin bridge from a Job to an engine call.

Each handler does exactly three things:
  1. resolve the job's payload into domain objects (via existing repositories),
  2. call **one** engine method,
  3. return a small, log-safe summary.

No handler contains synchronization logic, calendar logic, or provider logic.
"""

from __future__ import annotations

import uuid
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.domain.orchestration.models import Job
from app.domain.sync.models import SyncMode
from app.domain.value_objects.enums import SyncTrigger
from app.exceptions.base import NotFoundError
from app.persistence.models.user import User
from app.persistence.repositories.user import UserRepository
from app.tasks.container import (
    build_ingestion_service,
    build_metadata_service,
    build_sync_service,
)

logger = get_logger(__name__)


class JobOwnerMissingError(NotFoundError):
    code = "job_owner_missing"
    message = "The job's user no longer exists."


async def _load_user(session: AsyncSession, user_id: uuid.UUID | None) -> User:
    # ``user_id`` is None only for system jobs, which never call this; the
    # guard makes that contract explicit instead of hiding it behind an ignore.
    if user_id is None:
        raise JobOwnerMissingError()
    user = await UserRepository(session).get(user_id)
    if user is None or user.deleted_at is not None:
        raise JobOwnerMissingError()
    return user


async def handle_sync_subscription(session: AsyncSession, job: Job) -> dict[str, Any]:
    """Invoke the Stage 8 engine for one subscription. Unmodified, uninspected."""
    subscription_id = uuid.UUID(job.payload["subscription_id"])
    mode = SyncMode(job.payload.get("mode", SyncMode.INCREMENTAL.value))
    trigger = SyncTrigger(job.payload.get("trigger", SyncTrigger.SCHEDULED.value))

    user = await _load_user(session, job.user_id)
    engine = build_sync_service(session)
    report = await engine.synchronize(user, subscription_id, mode=mode, trigger=trigger)

    return {
        "run_id": str(report.run_id) if report.run_id else None,
        "status": report.status.value,
        "created": report.created,
        "updated": report.updated,
        "deleted": report.deleted,
        "skipped": report.skipped,
        "failed": report.failed,
        "api_calls": report.api_calls,
    }


async def handle_reconcile(session: AsyncSession, job: Job) -> dict[str, Any]:
    job.payload["mode"] = SyncMode.RECONCILE.value
    return await handle_sync_subscription(session, job)


async def handle_sync_user(session: AsyncSession, job: Job) -> dict[str, Any]:
    user = await _load_user(session, job.user_id)
    mode = SyncMode(job.payload.get("mode", SyncMode.INCREMENTAL.value))
    engine = build_sync_service(session)
    reports = await engine.synchronize_user(user, mode=mode, trigger=SyncTrigger.SCHEDULED)
    return {
        "subscriptions": len(reports),
        "created": sum(r.created for r in reports),
        "updated": sum(r.updated for r in reports),
        "deleted": sum(r.deleted for r in reports),
        "api_calls": sum(r.api_calls for r in reports),
    }


async def handle_metadata_refresh(session: AsyncSession, job: Job) -> dict[str, Any]:
    sports = job.payload.get("sport_keys")
    report = await build_metadata_service(session).refresh(sport_keys=sports)
    return {
        "ok": report.ok,
        "providers": [
            {
                "provider": p.provider_key,
                "success": p.success,
                "competitions": p.competitions,
                "teams": p.teams,
            }
            for p in report.providers
        ],
    }


async def handle_fixture_import(
    session: AsyncSession, job: Job, redis: aioredis.Redis
) -> dict[str, Any]:
    service = build_ingestion_service(session, redis)
    sport = job.payload.get("sport")
    if sport:
        report = await service.import_sport(sport)
    else:
        provider = job.payload.get("provider")
        if not provider:
            raise ValueError("fixture_import requires 'sport' or 'provider'")
        report = await service.import_provider(provider)
    return {
        "run_id": str(report.id),
        "status": report.status.value,
        "created": report.stats.created,
        "updated": report.stats.updated,
        "unchanged": report.stats.unchanged,
        "failed": report.stats.failed,
    }
