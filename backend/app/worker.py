"""Celery application, queues, routing, and the Beat schedule.

Stage 1 chose Celery + Beat + Redis; this stage makes that choice production-grade.
The scheduler decides *when* work runs. It never decides *how* synchronization
works — that is Stage 8's engine, invoked unchanged by the workers.

Run:
    celery -A app.worker.celery_app worker -Q sync.high,sync.default -c 4
    celery -A app.worker.celery_app worker -Q ingest,maintenance -c 2
    celery -A app.worker.celery_app beat

Reliability settings and why:

- ``task_acks_late`` + ``task_reject_on_worker_lost`` — a message is acknowledged
  only after the task finishes. If a worker is SIGKILLed, the broker redelivers.
  Combined with the idempotent engine, this gives *at-least-once delivery with
  exactly-once effect*.
- ``worker_prefetch_multiplier=1`` — a worker reserves one message at a time, so a
  slow sync never sits on a queue of unstarted work that another worker could run.
- ``worker_max_tasks_per_child`` — recycle processes to bound memory growth.
- ``task_soft_time_limit`` — raises inside the task so the job is recorded as
  failed and retried, rather than being hard-killed with no record.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from celery.signals import (
    worker_process_init,
    worker_process_shutdown,
    worker_ready,
    worker_shutdown,
)
from kombu import Queue as KombuQueue

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.domain.orchestration.models import Queue

configure_logging()
logger = get_logger(__name__)
settings = get_settings()

celery_app = Celery(
    "matchsync",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.sync_tasks", "app.tasks.maintenance_tasks"],
)

celery_app.conf.update(
    # --- serialization -------------------------------------------------------
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=settings.job_retention_seconds,
    timezone="UTC",
    enable_utc=True,
    # --- reliability ---------------------------------------------------------
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    # Redis visibility timeout must exceed the hard time limit, or the broker
    # redelivers a message that is still legitimately running. priority_steps
    # lives INSIDE broker_transport_options (a top-level key of that name is
    # silently ignored by Celery): 10 distinct levels so HIGH(9)/NORMAL(5)/LOW(1)
    # are not quantized onto the default 4-step scale.
    broker_transport_options={
        "visibility_timeout": settings.worker_time_limit_seconds + 60,
        "priority_steps": list(range(10)),
    },
    # --- resource limits -----------------------------------------------------
    worker_prefetch_multiplier=settings.worker_prefetch_multiplier,
    worker_max_tasks_per_child=settings.worker_max_tasks_per_child,
    task_soft_time_limit=settings.worker_soft_time_limit_seconds,
    task_time_limit=settings.worker_time_limit_seconds,
    # --- queues --------------------------------------------------------------
    task_default_queue=Queue.SYNC_DEFAULT.value,
    task_queues=[KombuQueue(q.value) for q in Queue if q is not Queue.DEAD_LETTER],
    task_routes={
        "orchestration.sync_subscription": {"queue": Queue.SYNC_DEFAULT.value},
        "orchestration.sync_subscription_priority": {"queue": Queue.SYNC_HIGH.value},
        "orchestration.sync_user": {"queue": Queue.SYNC_DEFAULT.value},
        "orchestration.reconcile": {"queue": Queue.SYNC_DEFAULT.value},
        "orchestration.fixture_import": {"queue": Queue.INGEST.value},
        "orchestration.metadata_refresh": {"queue": Queue.MAINTENANCE.value},
        "orchestration.cleanup": {"queue": Queue.MAINTENANCE.value},
        "orchestration.health_check": {"queue": Queue.MAINTENANCE.value},
        "orchestration.scan_due_subscriptions": {"queue": Queue.MAINTENANCE.value},
        "orchestration.detect_stuck_jobs": {"queue": Queue.MAINTENANCE.value},
    },
    task_annotations={
        "orchestration.sync_subscription": {"rate_limit": settings.sync_task_rate_limit},
        "orchestration.sync_subscription_priority": {"rate_limit": settings.sync_task_rate_limit},
    },
)

# --- Beat: the only place that decides *when* -------------------------------
celery_app.conf.beat_schedule = {
    # The core tick: find subscriptions whose next_sync_at has passed and enqueue
    # one job each. Beat never synchronizes anything itself.
    "scan-due-subscriptions": {
        "task": "orchestration.scan_due_subscriptions",
        "schedule": crontab(minute=f"*/{settings.scheduler_scan_interval_minutes}"),
    },
    # Reference data changes rarely; refresh nightly.
    "refresh-metadata": {
        "task": "orchestration.metadata_refresh",
        "schedule": crontab(hour=3, minute=0),
    },
    # Fixture ingestion: four times a day is well inside every provider's quota.
    "import-fixtures": {
        "task": "orchestration.fixture_import",
        "schedule": crontab(hour="*/6", minute=15),
    },
    # Liveness + stuck-job reaping.
    "health-check": {
        "task": "orchestration.health_check",
        "schedule": crontab(minute="*"),
    },
    "detect-stuck-jobs": {
        "task": "orchestration.detect_stuck_jobs",
        "schedule": crontab(minute="*/5"),
    },
    "cleanup": {
        "task": "orchestration.cleanup",
        "schedule": crontab(hour="*", minute=30),
    },
}


# --- process lifecycle -------------------------------------------------------
@worker_process_init.connect
def _reset_db_pool(**_: object) -> None:
    """Never inherit a parent's async DB connections across fork.

    asyncpg sockets are not fork-safe; a child that reuses them corrupts the
    protocol. Disposing forces each child to open its own connections lazily.
    """
    from app.persistence.session import engine

    engine.sync_engine.dispose(close=False)
    logger.info("worker.process_init.pool_reset")


@worker_process_shutdown.connect
def _dispose_db_pool(**_: object) -> None:
    from app.persistence.session import engine

    engine.sync_engine.dispose()


@worker_ready.connect
def _register_worker(sender: object | None = None, **_: object) -> None:
    from app.tasks.base import register_worker_heartbeat

    name = getattr(sender, "hostname", "unknown")
    register_worker_heartbeat(name, state="ready")
    logger.info("worker.ready", worker=name)


@worker_shutdown.connect
def _deregister_worker(sender: object | None = None, **_: object) -> None:
    """Graceful shutdown removes the heartbeat immediately.

    A crash is covered by the heartbeat key's TTL, so a dead worker always
    disappears from /workers — with or without a clean exit.
    """
    from app.tasks.base import deregister_worker_heartbeat

    name = getattr(sender, "hostname", "unknown")
    deregister_worker_heartbeat(name)
    logger.info("worker.shutdown", worker=name)
