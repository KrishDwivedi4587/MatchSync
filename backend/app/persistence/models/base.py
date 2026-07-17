"""Declarative base, shared type mapping, reusable mixins, and enum helper.

Centralizes everything every model needs so individual model files stay focused
on their columns and relationships:

- ``Base``            — the declarative base with a deterministic naming
                        convention (critical for stable Alembic autogenerate)
                        and a type-annotation map so ``datetime``/``dict``/
                        ``uuid.UUID`` render as the right column types.
- ``UUIDMixin``       — UUID surrogate primary key (Stage 1: avoids hotspotting,
                        enables future sharding).
- ``TimestampMixin``  — timezone-aware created/updated timestamps.
- ``SoftDeleteMixin`` — ``deleted_at`` for non-destructive deletion where the
                        record must be retained/auditable.
- ``AuditMixin``      — optional actor attribution (created_by/updated_by).
- ``enum_column``     — builds a native PG enum column that persists the enum's
                        string *value* (not its Python name).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

# Deterministic constraint/index names. Without this, Alembic autogenerate
# produces unstable, database-assigned names and noisy diffs. (SQLAlchemy docs
# recommend fixing this at project start — exactly what we are doing.)
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Portable JSON: native JSONB on Postgres (indexable), generic JSON elsewhere
# (so the SQLite-backed test suite runs without a live Postgres).
_JSON = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Map Python types -> SQL column types once, globally. ClassVar: this is
    # SQLAlchemy configuration shared by every model, never per-instance state.
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: DateTime(timezone=True),  # always timezone-aware
        uuid.UUID: Uuid(),  # native uuid on PG, CHAR(32) elsewhere
        dict[str, Any]: _JSON,
        list[str]: _JSON,
    }


def enum_column(enum_cls: type, name: str) -> SAEnum:
    """Return a native enum column type that stores the enum's string values.

    ``values_callable`` ensures the database stores ``"scheduled"`` rather than
    the Python member name ``"SCHEDULED"``.
    """
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        values_callable=lambda e: [member.value for member in e],
        validate_strings=True,
    )


class UUIDMixin:
    """Surrogate UUID primary key, generated application-side for portability."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """Timezone-aware creation/update timestamps, filled by the database."""

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SoftDeleteMixin:
    """Non-destructive deletion.

    Setting ``deleted_at`` marks a row deleted while keeping it for audit and
    referential history. Repositories filter these out by default. Used only on
    user-owned/business rows (users, calendars, subscriptions, fixtures, ...),
    never on append-only logs.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(default=None)

    @hybrid_property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class AuditMixin:
    """Optional actor attribution.

    Nullable UUIDs (no FK) so audit stays lightweight and does not couple
    reference/log tables to ``users`` or complicate delete ordering. Populated
    by the application once authentication exists.
    """

    created_by: Mapped[uuid.UUID | None] = mapped_column(default=None)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(default=None)
