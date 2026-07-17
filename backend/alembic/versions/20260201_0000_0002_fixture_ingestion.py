"""fixture ingestion: versions, import runs, absence tracking

Additive only. Migration 0001 is untouched and no existing column changes type.

Why each addition is required (see docs/fixtures.md):

- ``fixtures.version``       monotonic counter; nothing existing tracks how many
                             times a fixture changed.
- ``fixtures.missing_since`` Stage 1 forbids deleting a fixture on a single flaky
                             read; we must remember when it first went missing.
- ``fixture_versions``       append-only change history. ``sync_history`` /
                             ``sync_operations`` are subscription-scoped (Stage 8)
                             and cannot hold fixture-level history.
- ``import_runs``            durable import reports. Redis is a cache/broker in
                             this architecture, never a system of record, and
                             ``GET /fixtures/import/report/{id}`` must survive a
                             restart.

Revision ID: 0002_fixture_ingestion
Revises: 0001_initial_schema
Create Date: 2026-02-01 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_fixture_ingestion"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


fixture_change_type = postgresql.ENUM(
    "created", "updated", "postponed", "cancelled", "abandoned", "restored", "deleted",
    name="fixture_change_type", create_type=False,
)
import_status = postgresql.ENUM(
    "pending", "running", "success", "partial", "failed",
    name="import_status", create_type=False,
)

_NOW = sa.text("now()")


def upgrade() -> None:
    bind = op.get_bind()
    fixture_change_type.create(bind, checkfirst=True)
    import_status.create(bind, checkfirst=True)

    # --- fixtures: additive columns ----------------------------------------
    op.add_column(
        "fixtures",
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "fixtures", sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True)
    )

    # --- import_runs --------------------------------------------------------
    op.create_table(
        "import_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("sport_key", sa.String(64), nullable=True),
        sa.Column("status", import_status, server_default=sa.text("'pending'"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("fetched_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("unchanged_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("skipped_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("invalid_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("deleted_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("report", postgresql.JSONB(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_import_runs"),
    )
    op.create_index("ix_import_runs_provider_key", "import_runs", ["provider_key"])
    op.create_index("ix_import_runs_sport_key", "import_runs", ["sport_key"])
    op.create_index(
        "ix_import_runs_provider_created", "import_runs", ["provider_key", "created_at"]
    )

    # --- fixture_versions ---------------------------------------------------
    op.create_table(
        "fixture_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("change_type", fixture_change_type, nullable=False),
        sa.Column(
            "changed_fields", postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"), nullable=False,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("import_run_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(
            ["fixture_id"], ["fixtures.id"],
            name="fk_fixture_versions_fixture_id_fixtures", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_run_id"], ["import_runs.id"],
            name="fk_fixture_versions_import_run_id_import_runs", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_fixture_versions"),
        sa.UniqueConstraint("fixture_id", "version", name="uq_fixture_versions_fixture_id_version"),
    )
    op.create_index("ix_fixture_versions_fixture_id", "fixture_versions", ["fixture_id"])
    op.create_index("ix_fixture_versions_import_run_id", "fixture_versions", ["import_run_id"])
    op.create_index(
        "ix_fixture_versions_fixture_version", "fixture_versions", ["fixture_id", "version"]
    )

    # Backfill: every pre-existing fixture becomes version 1 with its own hash.
    op.execute(
        sa.text(
            """
            INSERT INTO fixture_versions
                (id, fixture_id, version, change_type, changed_fields,
                 content_hash, created_at, updated_at)
            SELECT gen_random_uuid(), f.id, 1, 'created', '[]'::jsonb,
                   f.content_hash, now(), now()
            FROM fixtures f
            """
        )
    )


def downgrade() -> None:
    op.drop_table("fixture_versions")
    op.drop_table("import_runs")
    op.drop_column("fixtures", "missing_since")
    op.drop_column("fixtures", "version")

    bind = op.get_bind()
    fixture_change_type.drop(bind, checkfirst=True)
    import_status.drop(bind, checkfirst=True)
