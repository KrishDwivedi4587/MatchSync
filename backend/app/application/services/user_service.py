"""User service — account provisioning and retrieval for authentication.

Uses Stage 3 repositories exclusively (no raw model queries beyond constructing
rows to persist). Contains no product/business logic — only identity mapping:
turn a verified provider profile into a MatchSync ``User`` (+ ``GoogleAccount``
+ encrypted ``OAuthToken``), creating on first login and reusing thereafter.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.core.logging import get_logger
from app.domain.ports.identity_provider import OAuthProfile, OAuthTokens
from app.domain.value_objects.enums import CalendarProvider, UserStatus
from app.infrastructure.crypto.encryption import TokenEncryptor
from app.persistence.models.account import GoogleAccount, OAuthToken
from app.persistence.models.user import User
from app.persistence.repositories.user import (
    GoogleAccountRepository,
    OAuthTokenRepository,
    UserRepository,
)

logger = get_logger(__name__)


class UserService:
    def __init__(
        self,
        users: UserRepository,
        accounts: GoogleAccountRepository,
        tokens: OAuthTokenRepository,
        encryptor: TokenEncryptor,
    ) -> None:
        self._users = users
        self._accounts = accounts
        self._tokens = tokens
        self._encryptor = encryptor

    async def get_active_user(self, user_id: uuid.UUID) -> User | None:
        """Return the user iff they exist, are active, and not soft-deleted."""
        user = await self._users.get(user_id)
        if user is None or user.deleted_at is not None:
            return None
        if user.status != UserStatus.ACTIVE:
            return None
        return user

    async def get_or_create_from_oauth(self, profile: OAuthProfile, tokens: OAuthTokens) -> User:
        provider = CalendarProvider(profile.provider)

        account = await self._accounts.get_by_subject(provider.value, profile.subject)
        if account is not None:
            user = await self._users.get(account.user_id)
            if user is None:  # defensive: orphaned account
                raise RuntimeError("GoogleAccount references a missing user.")
            account.email = profile.email
            account.scopes = tokens.scopes or account.scopes
            await self._upsert_token(account.id, tokens)
            logger.info("auth.user.login", user_id=str(user.id), provider=provider.value)
            return user

        # No linked account yet. Link to an existing user by *verified* email to
        # avoid duplicate accounts; otherwise create a fresh user.
        user = None
        if profile.email_verified:
            user = await self._users.get_by_email(profile.email)
        created = user is None
        if user is None:
            user = await self._users.create(
                email=profile.email,
                display_name=profile.display_name,
                locale=profile.locale,
            )

        account = await self._accounts.add(
            GoogleAccount(
                user_id=user.id,
                provider=provider,
                provider_subject=profile.subject,
                email=profile.email,
                scopes=tokens.scopes,
                is_primary=True,
            )
        )
        await self._upsert_token(account.id, tokens)
        logger.info(
            "auth.user.provisioned" if created else "auth.user.linked",
            user_id=str(user.id),
            provider=provider.value,
        )
        return user

    async def _upsert_token(self, account_id: uuid.UUID, tokens: OAuthTokens) -> None:
        """Store encrypted OAuth tokens for the account (create or update)."""
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=tokens.expires_in) if tokens.expires_in else None
        )
        access_enc = self._encryptor.encrypt(tokens.access_token)
        refresh_enc = (
            self._encryptor.encrypt(tokens.refresh_token) if tokens.refresh_token else None
        )

        existing = await self._tokens.get_by_account_id(account_id)
        if existing is None:
            await self._tokens.add(
                OAuthToken(
                    google_account_id=account_id,
                    access_token_encrypted=access_enc,
                    refresh_token_encrypted=refresh_enc,
                    expires_at=expires_at,
                    scopes=tokens.scopes,
                )
            )
        else:
            existing.access_token_encrypted = access_enc
            # Google omits the refresh token on subsequent consents — keep the
            # previously stored one rather than nulling it.
            if refresh_enc is not None:
                existing.refresh_token_encrypted = refresh_enc
            existing.expires_at = expires_at
            existing.scopes = tokens.scopes or existing.scopes
            existing.rotated_at = datetime.now(UTC)
