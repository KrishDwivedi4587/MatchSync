"""Account service: profile + preferences (Stage 10).

Thin composition over existing repositories. Preferences are a get-or-create
JSON document; the shape is validated by the API schema, not the DB.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.persistence.models.preferences import UserPreferences
from app.persistence.models.user import User
from app.persistence.repositories.preferences import UserPreferencesRepository
from app.persistence.repositories.user import UserRepository

logger = get_logger(__name__)

DEFAULT_PREFERENCES: dict[str, Any] = {
    "notifications": {
        "email": {"enabled": False, "target": None},
        "push": {"enabled": False, "target": None},
        "discord": {"enabled": False, "target": None},
        "slack": {"enabled": False, "target": None},
        "browser": {"enabled": False, "target": None},
        "reminders_minutes": [60],
    },
    "display": {"theme": "system"},
}


class AccountService:
    def __init__(
        self,
        session: AsyncSession,
        users: UserRepository,
        preferences: UserPreferencesRepository,
    ) -> None:
        self._session = session
        self._users = users
        self._preferences = preferences

    async def update_profile(
        self, user: User, *, display_name: str | None = None, timezone: str | None = None
    ) -> User:
        # Reload into this service's own session so the update is independent of
        # which session the authenticated ``user`` instance came from.
        row = await self._users.get(user.id)
        if row is None:  # pragma: no cover - the caller is already authenticated
            return user
        if display_name is not None:
            row.display_name = display_name.strip() or None
        if timezone is not None:
            row.timezone = timezone
        await self._session.commit()
        logger.info("account.profile_updated", user_id=str(row.id))
        return row

    async def get_preferences(self, user: User) -> dict[str, Any]:
        row = await self._preferences.get_for_user(user.id)
        if row is None:
            return dict(DEFAULT_PREFERENCES)
        # Merge over defaults so newly-added keys always have a value.
        return _deep_merge(DEFAULT_PREFERENCES, row.data or {})

    async def set_preferences(self, user: User, data: dict[str, Any]) -> dict[str, Any]:
        merged = _deep_merge(DEFAULT_PREFERENCES, data)
        row = await self._preferences.get_for_user(user.id)
        if row is None:
            row = await self._preferences.add(UserPreferences(user_id=user.id, data=merged))
        else:
            row.data = merged
        await self._session.commit()
        logger.info("account.preferences_updated", user_id=str(user.id))
        return merged


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively overlay ``override`` onto ``base`` without mutating either."""
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
