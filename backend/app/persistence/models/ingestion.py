"""Fixture-ingestion models: version history and import runs (Stage 7).

Both are append-only audit tables — no soft delete, no updates except the
terminal write on an import run.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.value_objects.enums import FixtureChangeType, ImportStatus
from app.persistence.models.base import Base, TimestampMixin, UUIDMixin, enum_column


class ImportRun(UUIDMixin, TimestampMixin, Base):
    """One execution of the fixture ingestion pipeline.

    Persisted (not cached) because import history is durable audit truth, and
    ``GET /fixtures/import/report/{id}`` must survive a restart.
    """

    __tablename__ = "import_runs"
    __table_args__ = (Index("ix_import_runs_provider_created", "provider_key", "created_at"),)

    provider_key: Mapped[str] = mapped_column(String(64), index=True)
    sport_key: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    status: Mapped[ImportStatus] = mapped_column(
        enum_column(ImportStatus, "import_status"), default=ImportStatus.PENDING
    )
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    # Denormalized counters for cheap list/status queries without parsing JSON.
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    deleted_count: Mapped[int] = mapped_column(Integer, default=0)

    # The full structured report (per-competition stats + issues).
    report: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    error_summary: Mapped[str | None] = mapped_column(Text, default=None)


class FixtureVersion(UUIDMixin, TimestampMixin, Base):
    """An immutable record of one change to a fixture.

    Version 1 is written when the fixture is created; every subsequent persisted
    change appends a row. The ``snapshot`` preserves the resulting state so
    history is readable without replaying diffs.
    """

    __tablename__ = "fixture_versions"
    __table_args__ = (
        UniqueConstraint("fixture_id", "version"),
        Index("ix_fixture_versions_fixture_version", "fixture_id", "version"),
    )

    fixture_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fixtures.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    change_type: Mapped[FixtureChangeType] = mapped_column(
        enum_column(FixtureChangeType, "fixture_change_type")
    )
    # List of FixtureField values that differed from the previous version.
    changed_fields: Mapped[list[str]] = mapped_column(default=list)
    content_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    provider_updated_at: Mapped[datetime | None] = mapped_column(default=None)
    # SET NULL: version history outlives the import run that produced it.
    import_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_runs.id", ondelete="SET NULL"), default=None, index=True
    )

    import_run: Mapped[ImportRun | None] = relationship()
