"""User preferences (Stage 10).

A single additive table holding durable, user-level configuration that has no
home in the frozen schema: notification channel config (delivery is a future
stage) and display preferences. One row per user (1-1).

Kept as a JSON document rather than columns because these are *configuration*
that will grow (new channels, new toggles) and are never queried by field — the
exact shape SQL columns serve poorly. Functional sync settings that the engine
already consumes (per-subscription frequency, event prefix, user timezone) stay
where they are; nothing here changes engine behavior.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.persistence.models.base import Base, TimestampMixin, UUIDMixin


class UserPreferences(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    # The full preferences document (notifications, display, sync defaults).
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)

    user: Mapped[User] = relationship()


# Late import avoided: relationship target resolved by SQLAlchemy registry.
from app.persistence.models.user import User  # noqa: E402
