"""User-preferences repository."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.persistence.models.preferences import UserPreferences
from app.persistence.repositories.base import BaseRepository


class UserPreferencesRepository(BaseRepository[UserPreferences]):
    model = UserPreferences

    async def get_for_user(self, user_id: uuid.UUID) -> UserPreferences | None:
        stmt = select(UserPreferences).where(UserPreferences.user_id == user_id)
        return (await self.session.scalars(stmt)).first()
