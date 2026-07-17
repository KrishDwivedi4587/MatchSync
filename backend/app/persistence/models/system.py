"""System/operational tables: ApplicationLog, SchedulerJob, ProviderMetadata.

These support observability and configuration, not user data.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.value_objects.enums import (
    JobStatus,
    LogLevel,
    ProviderStatus,
    ProviderType,
    SyncStatus,
)
from app.persistence.models.base import (
    Base,
    TimestampMixin,
    UUIDMixin,
    enum_column,
)


class ApplicationLog(UUIDMixin, TimestampMixin, Base):
    """Durable audit/application log.

    Stage 1: operational logs stream to a log sink; only durable audit-worthy
    events are persisted here (token issuance, scope/calendar changes, etc.).
    Append-only.
    """

    __tablename__ = "application_logs"
    __table_args__ = (Index("ix_application_logs_event_created", "event", "created_at"),)

    level: Mapped[LogLevel] = mapped_column(
        enum_column(LogLevel, "log_level"), default=LogLevel.INFO
    )
    # Machine-readable event name (e.g. "oauth.token.refreshed").
    event: Mapped[str] = mapped_column(String(128), index=True)
    message: Mapped[str | None] = mapped_column(Text, default=None)
    # SET NULL: keep the audit trail even after a user is deleted.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )
    request_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    context: Mapped[dict[str, Any] | None] = mapped_column(default=None)


class SchedulerJob(UUIDMixin, TimestampMixin, Base):
    """Registry of scheduled jobs.

    Celery Beat remains the executor; this table is metadata for observability
    and (future) dynamically-configurable schedules. It does not replace Beat.
    """

    __tablename__ = "scheduler_jobs"

    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    schedule: Mapped[str] = mapped_column(String(128))  # cron expression
    status: Mapped[JobStatus] = mapped_column(
        enum_column(JobStatus, "job_status"), default=JobStatus.ENABLED
    )
    last_run_at: Mapped[datetime | None] = mapped_column(default=None)
    last_run_status: Mapped[SyncStatus | None] = mapped_column(
        enum_column(SyncStatus, "sync_status"), default=None
    )
    next_run_at: Mapped[datetime | None] = mapped_column(default=None)
    config: Mapped[dict[str, Any] | None] = mapped_column(default=None)


class ProviderMetadata(UUIDMixin, TimestampMixin, Base):
    """Configuration + health for an external provider.

    No secrets/API keys are stored here — those belong in the secrets manager
    (Stage 1, §13). This table holds non-sensitive config and health state.
    """

    __tablename__ = "provider_metadata"

    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    provider_type: Mapped[ProviderType] = mapped_column(enum_column(ProviderType, "provider_type"))
    status: Mapped[ProviderStatus] = mapped_column(
        enum_column(ProviderStatus, "provider_status"), default=ProviderStatus.HEALTHY
    )
    base_url: Mapped[str | None] = mapped_column(String(512), default=None)
    config: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    last_health_check_at: Mapped[datetime | None] = mapped_column(default=None)
    last_success_at: Mapped[datetime | None] = mapped_column(default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
