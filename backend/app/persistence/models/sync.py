"""Synchronization history: SyncHistory (a run) and SyncOperation (an item).

These power the "view synchronization history" feature and debugging. Both are
append-only (no soft delete) and time-ordered; candidates for time-based
partitioning as volume grows (see Performance Considerations).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.value_objects.enums import (
    OperationStatus,
    OperationType,
    SyncStatus,
    SyncTrigger,
)
from app.persistence.models.base import (
    Base,
    TimestampMixin,
    UUIDMixin,
    enum_column,
)

if TYPE_CHECKING:
    from app.persistence.models.subscription import Subscription


class SyncHistory(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "sync_history"
    __table_args__ = (
        # List a subscription's runs newest-first.
        Index("ix_sync_history_subscription_created", "subscription_id", "created_at"),
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), index=True
    )
    trigger: Mapped[SyncTrigger] = mapped_column(enum_column(SyncTrigger, "sync_trigger"))
    status: Mapped[SyncStatus] = mapped_column(
        enum_column(SyncStatus, "sync_status"), default=SyncStatus.PENDING
    )
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)

    created_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    deleted_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text, default=None)

    subscription: Mapped[Subscription] = relationship(back_populates="sync_history")
    operations: Mapped[list[SyncOperation]] = relationship(
        back_populates="sync_history",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class SyncOperation(UUIDMixin, TimestampMixin, Base):
    """Per-fixture outcome within a run — granular history/debugging."""

    __tablename__ = "sync_operations"

    sync_history_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sync_history.id", ondelete="CASCADE"), index=True
    )
    # SET NULL: keep the operation record even if the fixture/event is removed.
    fixture_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fixtures.id", ondelete="SET NULL"), default=None
    )
    calendar_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("calendar_events.id", ondelete="SET NULL"), default=None
    )
    operation_type: Mapped[OperationType] = mapped_column(
        enum_column(OperationType, "operation_type")
    )
    status: Mapped[OperationStatus] = mapped_column(
        enum_column(OperationStatus, "operation_status")
    )
    message: Mapped[str | None] = mapped_column(Text, default=None)

    sync_history: Mapped[SyncHistory] = relationship(back_populates="operations")
