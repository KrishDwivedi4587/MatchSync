"""User, GoogleAccount, and Calendar repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.persistence.models.account import GoogleAccount, OAuthToken
from app.persistence.models.calendar import Calendar
from app.persistence.models.user import User
from app.persistence.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email.lower(), User.deleted_at.is_(None))
        return (await self.session.scalars(stmt)).first()


class GoogleAccountRepository(BaseRepository[GoogleAccount]):
    model = GoogleAccount

    async def get_by_subject(self, provider: str, provider_subject: str) -> GoogleAccount | None:
        stmt = select(GoogleAccount).where(
            GoogleAccount.provider == provider,
            GoogleAccount.provider_subject == provider_subject,
        )
        return (await self.session.scalars(stmt)).first()

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[GoogleAccount]:
        stmt = select(GoogleAccount).where(
            GoogleAccount.user_id == user_id,
            GoogleAccount.deleted_at.is_(None),
        )
        return (await self.session.scalars(stmt)).all()

    async def get_with_token(self, account_id: uuid.UUID) -> GoogleAccount | None:
        """Eager-load the token to avoid a second round-trip (N+1 guard)."""
        stmt = (
            select(GoogleAccount)
            .where(GoogleAccount.id == account_id)
            .options(selectinload(GoogleAccount.token))
        )
        return (await self.session.scalars(stmt)).first()


class OAuthTokenRepository(BaseRepository[OAuthToken]):
    model = OAuthToken

    async def get_by_account_id(self, account_id: uuid.UUID) -> OAuthToken | None:
        stmt = select(OAuthToken).where(OAuthToken.google_account_id == account_id)
        return (await self.session.scalars(stmt)).first()


class CalendarRepository(BaseRepository[Calendar]):
    model = Calendar

    async def get_by_external_id(
        self, account_id: uuid.UUID, external_calendar_id: str
    ) -> Calendar | None:
        """Look up by the provider's calendar id (matches the unique constraint)."""
        stmt = select(Calendar).where(
            Calendar.google_account_id == account_id,
            Calendar.external_calendar_id == external_calendar_id,
        )
        return (await self.session.scalars(stmt)).first()

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[Calendar]:
        """All non-deleted calendars across every account linked to the user."""
        stmt = (
            select(Calendar)
            .join(GoogleAccount, Calendar.google_account_id == GoogleAccount.id)
            .where(GoogleAccount.user_id == user_id, Calendar.deleted_at.is_(None))
            .order_by(Calendar.summary)
        )
        return (await self.session.scalars(stmt)).all()

    async def list_for_account(self, account_id: uuid.UUID) -> Sequence[Calendar]:
        stmt = select(Calendar).where(
            Calendar.google_account_id == account_id,
            Calendar.deleted_at.is_(None),
        )
        return (await self.session.scalars(stmt)).all()

    async def list_sync_targets(self, account_id: uuid.UUID) -> Sequence[Calendar]:
        stmt = select(Calendar).where(
            Calendar.google_account_id == account_id,
            Calendar.is_sync_target.is_(True),
            Calendar.deleted_at.is_(None),
        )
        return (await self.session.scalars(stmt)).all()
