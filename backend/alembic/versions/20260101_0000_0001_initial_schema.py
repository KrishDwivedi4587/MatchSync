"""initial schema

Creates the full MatchSync persistence schema: enums, tables, constraints, and
indexes for the entities defined in ``app.persistence.models``.

This migration was authored to mirror the ORM models. Once the environment is
running you may regenerate the canonical version with:

    alembic revision --autogenerate -m "initial schema"

and diff it against this file. Enum types are created explicitly up front (and
dropped last) so types shared by multiple tables are created exactly once.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-01-01 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- Enum type definitions (create_type=False -> created explicitly below) ---
user_status = postgresql.ENUM(
    "active", "suspended", "deleted", name="user_status", create_type=False
)
calendar_provider = postgresql.ENUM(
    "google", "apple", "microsoft", name="calendar_provider", create_type=False
)
sport_category = postgresql.ENUM(
    "team", "individual", "esports", name="sport_category", create_type=False
)
competition_type = postgresql.ENUM(
    "league", "tournament", "cup", "season", "other",
    name="competition_type", create_type=False,
)
fixture_status = postgresql.ENUM(
    "scheduled", "live", "finished", "postponed", "cancelled", "deleted",
    name="fixture_status", create_type=False,
)
subscription_type = postgresql.ENUM(
    "sport", "competition", "team", name="subscription_type", create_type=False
)
subscription_status = postgresql.ENUM(
    "active", "paused", "disabled", name="subscription_status", create_type=False
)
calendar_event_state = postgresql.ENUM(
    "active", "cancelled", "deleted", name="calendar_event_state", create_type=False
)
sync_status = postgresql.ENUM(
    "pending", "running", "success", "partial", "failed",
    name="sync_status", create_type=False,
)
sync_trigger = postgresql.ENUM(
    "scheduled", "manual", "initial", name="sync_trigger", create_type=False
)
operation_type = postgresql.ENUM(
    "create", "update", "delete", "cancel", "skip",
    name="operation_type", create_type=False,
)
operation_status = postgresql.ENUM(
    "success", "failed", "skipped", name="operation_status", create_type=False
)
provider_type = postgresql.ENUM(
    "sports", "calendar", "identity", name="provider_type", create_type=False
)
provider_status = postgresql.ENUM(
    "healthy", "degraded", "down", "disabled", name="provider_status", create_type=False
)
job_status = postgresql.ENUM(
    "enabled", "paused", "disabled", name="job_status", create_type=False
)
log_level = postgresql.ENUM(
    "debug", "info", "warning", "error", "critical", name="log_level", create_type=False
)

_ALL_ENUMS = [
    user_status, calendar_provider, sport_category, competition_type, fixture_status,
    subscription_type, subscription_status, calendar_event_state, sync_status,
    sync_trigger, operation_type, operation_status, provider_type, provider_status,
    job_status, log_level,
]

_NOW = sa.text("now()")
_TRUE = sa.text("true")
_FALSE = sa.text("false")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    ]


def _deleted_at() -> sa.Column:
    return sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in _ALL_ENUMS:
        enum.create(bind, checkfirst=True)

    # --- users -------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("timezone", sa.String(64), server_default=sa.text("'UTC'"), nullable=False),
        sa.Column("locale", sa.String(16), nullable=True),
        sa.Column("status", user_status, server_default=sa.text("'active'"), nullable=False),
        *_timestamps(),
        _deleted_at(),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # --- sports ------------------------------------------------------------
    op.create_table(
        "sports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("category", sport_category, nullable=False),
        sa.Column("provider_key", sa.String(64), nullable=False),
        sa.Column("icon", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=_TRUE, nullable=False),
        sa.Column("display_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_sports"),
    )
    op.create_index("ix_sports_key", "sports", ["key"], unique=True)

    # --- google_accounts ---------------------------------------------------
    op.create_table(
        "google_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", calendar_provider, server_default=sa.text("'google'"), nullable=False),
        sa.Column("provider_subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=_FALSE, nullable=False),
        *_timestamps(),
        _deleted_at(),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_google_accounts_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_google_accounts"),
        sa.UniqueConstraint(
            "provider", "provider_subject", name="uq_google_accounts_provider_provider_subject"
        ),
    )
    op.create_index("ix_google_accounts_user_id", "google_accounts", ["user_id"])
    op.create_index("ix_google_accounts_email", "google_accounts", ["email"])

    # --- oauth_tokens ------------------------------------------------------
    op.create_table(
        "oauth_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("google_account_id", sa.Uuid(), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_type", sa.String(32), server_default=sa.text("'Bearer'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("token_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["google_account_id"], ["google_accounts.id"],
            name="fk_oauth_tokens_google_account_id_google_accounts", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_oauth_tokens"),
        sa.UniqueConstraint("google_account_id", name="uq_oauth_tokens_google_account_id"),
    )

    # --- calendars ---------------------------------------------------------
    op.create_table(
        "calendars",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("google_account_id", sa.Uuid(), nullable=False),
        sa.Column("provider", calendar_provider, server_default=sa.text("'google'"), nullable=False),
        sa.Column("external_calendar_id", sa.String(255), nullable=False),
        sa.Column("summary", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("time_zone", sa.String(64), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default=_FALSE, nullable=False),
        sa.Column("is_sync_target", sa.Boolean(), server_default=_FALSE, nullable=False),
        sa.Column("access_role", sa.String(32), nullable=True),
        *_timestamps(),
        _deleted_at(),
        sa.ForeignKeyConstraint(
            ["google_account_id"], ["google_accounts.id"],
            name="fk_calendars_google_account_id_google_accounts", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_calendars"),
        sa.UniqueConstraint(
            "google_account_id", "external_calendar_id",
            name="uq_calendars_google_account_id_external_calendar_id",
        ),
    )
    op.create_index("ix_calendars_google_account_id", "calendars", ["google_account_id"])

    # --- competitions ------------------------------------------------------
    op.create_table(
        "competitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sport_id", sa.Uuid(), nullable=False),
        sa.Column("provider_competition_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", competition_type, server_default=sa.text("'league'"), nullable=False),
        sa.Column("country", sa.String(128), nullable=True),
        sa.Column("season", sa.String(32), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=_TRUE, nullable=False),
        *_timestamps(),
        _deleted_at(),
        sa.ForeignKeyConstraint(
            ["sport_id"], ["sports.id"], name="fk_competitions_sport_id_sports", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_competitions"),
        sa.UniqueConstraint(
            "sport_id", "provider_competition_id",
            name="uq_competitions_sport_id_provider_competition_id",
        ),
    )
    op.create_index("ix_competitions_sport_id", "competitions", ["sport_id"])

    # --- teams -------------------------------------------------------------
    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sport_id", sa.Uuid(), nullable=False),
        sa.Column("provider_team_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("short_name", sa.String(64), nullable=True),
        sa.Column("country", sa.String(128), nullable=True),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=_TRUE, nullable=False),
        *_timestamps(),
        _deleted_at(),
        sa.ForeignKeyConstraint(
            ["sport_id"], ["sports.id"], name="fk_teams_sport_id_sports", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_teams"),
        sa.UniqueConstraint(
            "sport_id", "provider_team_id", name="uq_teams_sport_id_provider_team_id"
        ),
    )
    op.create_index("ix_teams_sport_id", "teams", ["sport_id"])

    # --- team_competition (association) ------------------------------------
    op.create_table(
        "team_competition",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("competition_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_team_competition_team_id_teams", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["competition_id"], ["competitions.id"],
            name="fk_team_competition_competition_id_competitions", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("team_id", "competition_id", name="pk_team_competition"),
    )

    # --- fixtures ----------------------------------------------------------
    op.create_table(
        "fixtures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("competition_id", sa.Uuid(), nullable=False),
        sa.Column("provider_fixture_id", sa.String(128), nullable=False),
        sa.Column("identity_key", sa.String(255), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("home_team_id", sa.Uuid(), nullable=True),
        sa.Column("away_team_id", sa.Uuid(), nullable=True),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", fixture_status, server_default=sa.text("'scheduled'"), nullable=False),
        sa.Column("round", sa.String(64), nullable=True),
        sa.Column("stage", sa.String(64), nullable=True),
        sa.Column("venue", sa.String(255), nullable=True),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        _deleted_at(),
        sa.ForeignKeyConstraint(
            ["competition_id"], ["competitions.id"],
            name="fk_fixtures_competition_id_competitions", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["home_team_id"], ["teams.id"], name="fk_fixtures_home_team_id_teams", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["away_team_id"], ["teams.id"], name="fk_fixtures_away_team_id_teams", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_fixtures"),
        sa.UniqueConstraint(
            "competition_id", "provider_fixture_id",
            name="uq_fixtures_competition_id_provider_fixture_id",
        ),
    )
    op.create_index("ix_fixtures_identity_key", "fixtures", ["identity_key"], unique=True)
    op.create_index("ix_fixtures_competition_id", "fixtures", ["competition_id"])
    op.create_index("ix_fixtures_home_team_id", "fixtures", ["home_team_id"])
    op.create_index("ix_fixtures_away_team_id", "fixtures", ["away_team_id"])
    op.create_index("ix_fixtures_scheduled_start", "fixtures", ["scheduled_start"])
    op.create_index("ix_fixtures_competition_start", "fixtures", ["competition_id", "scheduled_start"])
    op.create_index("ix_fixtures_status_start", "fixtures", ["status", "scheduled_start"])

    # --- subscriptions -----------------------------------------------------
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("target_calendar_id", sa.Uuid(), nullable=False),
        sa.Column("sport_id", sa.Uuid(), nullable=False),
        sa.Column("scope_type", subscription_type, nullable=False),
        sa.Column("competition_id", sa.Uuid(), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("status", subscription_status, server_default=sa.text("'active'"), nullable=False),
        sa.Column("sync_frequency_minutes", sa.Integer(), server_default=sa.text("360"), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_prefix", sa.String(64), nullable=True),
        *_timestamps(),
        _deleted_at(),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_subscriptions_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_calendar_id"], ["calendars.id"],
            name="fk_subscriptions_target_calendar_id_calendars", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sport_id"], ["sports.id"], name="fk_subscriptions_sport_id_sports", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["competition_id"], ["competitions.id"],
            name="fk_subscriptions_competition_id_competitions", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_subscriptions_team_id_teams", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_subscriptions"),
        sa.UniqueConstraint(
            "user_id", "target_calendar_id", "scope_type", "competition_id", "team_id",
            name="uq_subscriptions_user_scope",
        ),
        sa.CheckConstraint(
            "(scope_type = 'sport' AND competition_id IS NULL AND team_id IS NULL) "
            "OR (scope_type = 'competition' AND competition_id IS NOT NULL AND team_id IS NULL) "
            "OR (scope_type = 'team' AND team_id IS NOT NULL AND competition_id IS NULL)",
            name="ck_subscriptions_scope_reference_consistency",
        ),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index("ix_subscriptions_target_calendar_id", "subscriptions", ["target_calendar_id"])
    op.create_index("ix_subscriptions_sport_id", "subscriptions", ["sport_id"])
    op.create_index("ix_subscriptions_competition_id", "subscriptions", ["competition_id"])
    op.create_index("ix_subscriptions_team_id", "subscriptions", ["team_id"])
    op.create_index("ix_subscriptions_next_sync", "subscriptions", ["status", "next_sync_at"])

    # --- calendar_events ---------------------------------------------------
    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("calendar_id", sa.Uuid(), nullable=False),
        sa.Column("external_event_id", sa.String(255), nullable=True),
        sa.Column("fixture_identity_key", sa.String(255), nullable=False),
        sa.Column("synced_content_hash", sa.String(64), nullable=True),
        sa.Column("state", calendar_event_state, server_default=sa.text("'active'"), nullable=False),
        sa.Column("last_pushed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        _deleted_at(),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["subscriptions.id"],
            name="fk_calendar_events_subscription_id_subscriptions", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["fixture_id"], ["fixtures.id"],
            name="fk_calendar_events_fixture_id_fixtures", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["calendar_id"], ["calendars.id"],
            name="fk_calendar_events_calendar_id_calendars", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_calendar_events"),
        sa.UniqueConstraint(
            "subscription_id", "fixture_id",
            name="uq_calendar_events_subscription_id_fixture_id",
        ),
    )
    op.create_index("ix_calendar_events_subscription_id", "calendar_events", ["subscription_id"])
    op.create_index("ix_calendar_events_fixture_id", "calendar_events", ["fixture_id"])
    op.create_index("ix_calendar_events_calendar_id", "calendar_events", ["calendar_id"])
    op.create_index("ix_calendar_events_external_event_id", "calendar_events", ["external_event_id"])

    # --- sync_history ------------------------------------------------------
    op.create_table(
        "sync_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sync_trigger, nullable=False),
        sa.Column("status", sync_status, server_default=sa.text("'pending'"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("deleted_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("skipped_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["subscriptions.id"],
            name="fk_sync_history_subscription_id_subscriptions", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sync_history"),
    )
    op.create_index("ix_sync_history_subscription_id", "sync_history", ["subscription_id"])
    op.create_index(
        "ix_sync_history_subscription_created", "sync_history", ["subscription_id", "created_at"]
    )

    # --- sync_operations ---------------------------------------------------
    op.create_table(
        "sync_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sync_history_id", sa.Uuid(), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), nullable=True),
        sa.Column("calendar_event_id", sa.Uuid(), nullable=True),
        sa.Column("operation_type", operation_type, nullable=False),
        sa.Column("status", operation_status, nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["sync_history_id"], ["sync_history.id"],
            name="fk_sync_operations_sync_history_id_sync_history", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["fixture_id"], ["fixtures.id"],
            name="fk_sync_operations_fixture_id_fixtures", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["calendar_event_id"], ["calendar_events.id"],
            name="fk_sync_operations_calendar_event_id_calendar_events", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sync_operations"),
    )
    op.create_index("ix_sync_operations_sync_history_id", "sync_operations", ["sync_history_id"])

    # --- application_logs --------------------------------------------------
    op.create_table(
        "application_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("level", log_level, server_default=sa.text("'info'"), nullable=False),
        sa.Column("event", sa.String(128), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("context", postgresql.JSONB(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_application_logs_user_id_users", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_application_logs"),
    )
    op.create_index("ix_application_logs_event", "application_logs", ["event"])
    op.create_index("ix_application_logs_user_id", "application_logs", ["user_id"])
    op.create_index("ix_application_logs_request_id", "application_logs", ["request_id"])
    op.create_index("ix_application_logs_event_created", "application_logs", ["event", "created_at"])

    # --- scheduler_jobs ----------------------------------------------------
    op.create_table(
        "scheduler_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("schedule", sa.String(128), nullable=False),
        sa.Column("status", job_status, server_default=sa.text("'enabled'"), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sync_status, nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("config", postgresql.JSONB(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_scheduler_jobs"),
    )
    op.create_index("ix_scheduler_jobs_key", "scheduler_jobs", ["key"], unique=True)

    # --- provider_metadata -------------------------------------------------
    op.create_table(
        "provider_metadata",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("provider_type", provider_type, nullable=False),
        sa.Column("status", provider_status, server_default=sa.text("'healthy'"), nullable=False),
        sa.Column("base_url", sa.String(512), nullable=True),
        sa.Column("config", postgresql.JSONB(), nullable=True),
        sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_provider_metadata"),
    )
    op.create_index("ix_provider_metadata_key", "provider_metadata", ["key"], unique=True)


def downgrade() -> None:
    # Drop in reverse dependency order.
    for table in (
        "provider_metadata",
        "scheduler_jobs",
        "application_logs",
        "sync_operations",
        "sync_history",
        "calendar_events",
        "subscriptions",
        "fixtures",
        "team_competition",
        "teams",
        "competitions",
        "calendars",
        "oauth_tokens",
        "google_accounts",
        "sports",
        "users",
    ):
        op.drop_table(table)

    bind = op.get_bind()
    for enum in reversed(_ALL_ENUMS):
        enum.drop(bind, checkfirst=True)
