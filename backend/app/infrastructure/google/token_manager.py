"""Google access-token manager.

Consumes the OAuth tokens Stage 4 already stored, encrypted, in the frozen
``oauth_tokens`` table. Responsibilities:

- Read + decrypt the stored access token for an account.
- Transparently refresh it via Google's token endpoint when it has expired (or
  is about to), re-encrypt, and write it back through the existing repository.
- Translate a revoked/invalid refresh token into ``CalendarReauthRequiredError``.

No new schema: ``access_token_encrypted``, ``refresh_token_encrypted``,
``expires_at``, ``rotated_at`` and ``token_version`` all already exist.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.core.logging import get_logger
from app.exceptions.calendar import CalendarReauthRequiredError, ProviderUnavailableError
from app.infrastructure.crypto.encryption import TokenEncryptor
from app.infrastructure.google.endpoints import GOOGLE_TOKEN_ENDPOINT
from app.infrastructure.http.resilient import ResilientHttpClient
from app.persistence.repositories.user import OAuthTokenRepository

logger = get_logger(__name__)

# Refresh a little early so an in-flight request never races the expiry.
_EXPIRY_SKEW = timedelta(seconds=60)


class GoogleTokenManager:
    def __init__(
        self,
        tokens: OAuthTokenRepository,
        encryptor: TokenEncryptor,
        settings: Settings,
        http: ResilientHttpClient,
    ) -> None:
        self._tokens = tokens
        self._encryptor = encryptor
        self._settings = settings
        self._http = http

    async def get_access_token(self, account_id: uuid.UUID) -> str:
        """Return a valid access token for the account, refreshing if needed."""
        row = await self._tokens.get_by_account_id(account_id)
        if row is None:
            logger.warning("calendar.token.missing", account_id=str(account_id))
            raise CalendarReauthRequiredError("No stored credentials for this account.")

        if not self._is_expired(row.expires_at):
            return self._encryptor.decrypt(row.access_token_encrypted)

        if not row.refresh_token_encrypted:
            logger.warning("calendar.token.no_refresh_token", account_id=str(account_id))
            raise CalendarReauthRequiredError("No refresh token available.")

        refresh_token = self._encryptor.decrypt(row.refresh_token_encrypted)
        access_token, expires_in = await self._refresh(refresh_token)

        row.access_token_encrypted = self._encryptor.encrypt(access_token)
        row.expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        row.rotated_at = datetime.now(UTC)
        logger.info("calendar.token.refreshed", account_id=str(account_id))
        return access_token

    @staticmethod
    def _is_expired(expires_at: datetime | None) -> bool:
        if expires_at is None:
            return True  # unknown expiry -> refresh to be safe
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= datetime.now(UTC) + _EXPIRY_SKEW

    async def _refresh(self, refresh_token: str) -> tuple[str, int]:
        response = await self._http.request(
            "POST",
            GOOGLE_TOKEN_ENDPOINT,
            data={
                "client_id": self._settings.google_client_id,
                "client_secret": self._settings.google_client_secret.get_secret_value(),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )

        if response.status_code == 400:
            # invalid_grant: user revoked access or the token was rotated away.
            logger.warning("calendar.token.refresh_rejected")
            raise CalendarReauthRequiredError("Calendar access was revoked.")
        if response.status_code != 200:
            raise ProviderUnavailableError("Token refresh failed.")

        payload = response.json()
        return payload["access_token"], int(payload.get("expires_in", 3600))
