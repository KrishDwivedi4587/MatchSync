"""GoogleTokenManager tests — consuming and refreshing the existing encrypted tokens."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.domain.value_objects.enums import CalendarProvider
from app.exceptions.calendar import CalendarReauthRequiredError
from app.infrastructure.crypto.encryption import TokenEncryptor
from app.infrastructure.google.token_manager import GoogleTokenManager
from app.infrastructure.http.resilient import ResilientHttpClient, RetryPolicy
from app.persistence.models.account import GoogleAccount, OAuthToken
from app.persistence.models.user import User
from app.persistence.repositories.user import OAuthTokenRepository


def _manager(db: AsyncSession, handler) -> tuple[GoogleTokenManager, TokenEncryptor]:
    encryptor = TokenEncryptor(get_settings())
    http = ResilientHttpClient(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retry=RetryPolicy(max_attempts=2, base_delay=0.0, max_delay=0.0),
    )
    return GoogleTokenManager(OAuthTokenRepository(db), encryptor, get_settings(), http), encryptor


async def _account_with_token(
    db: AsyncSession, encryptor: TokenEncryptor, *, expires_at, refresh: str | None = "r-tok"
) -> GoogleAccount:
    user = User(email="tok@example.com")
    account = GoogleAccount(
        user=user, provider=CalendarProvider.GOOGLE, provider_subject="s1", email="tok@example.com"
    )
    db.add(account)
    await db.flush()
    db.add(
        OAuthToken(
            google_account_id=account.id,
            access_token_encrypted=encryptor.encrypt("old-access"),
            refresh_token_encrypted=encryptor.encrypt(refresh) if refresh else None,
            expires_at=expires_at,
        )
    )
    await db.commit()
    return account


async def test_valid_token_is_returned_without_network(db_session: AsyncSession) -> None:
    def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("no refresh expected")

    manager, encryptor = _manager(db_session, handler)
    account = await _account_with_token(
        db_session, encryptor, expires_at=datetime.now(UTC) + timedelta(hours=1)
    )
    assert await manager.get_access_token(account.id) == "old-access"


async def test_expired_token_is_refreshed_and_persisted(db_session: AsyncSession) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert b"grant_type=refresh_token" in request.content
        return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})

    manager, encryptor = _manager(db_session, handler)
    account = await _account_with_token(
        db_session, encryptor, expires_at=datetime.now(UTC) - timedelta(minutes=5)
    )

    assert await manager.get_access_token(account.id) == "new-access"
    assert calls["n"] == 1

    # The new token is written back, re-encrypted (never plaintext).
    await db_session.commit()
    row = await OAuthTokenRepository(db_session).get_by_account_id(account.id)
    assert row is not None
    assert row.access_token_encrypted != "new-access"
    assert encryptor.decrypt(row.access_token_encrypted) == "new-access"
    assert row.rotated_at is not None


async def test_revoked_refresh_token_requires_reauth(db_session: AsyncSession) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    manager, encryptor = _manager(db_session, handler)
    account = await _account_with_token(
        db_session, encryptor, expires_at=datetime.now(UTC) - timedelta(minutes=5)
    )
    with pytest.raises(CalendarReauthRequiredError):
        await manager.get_access_token(account.id)


async def test_missing_refresh_token_requires_reauth(db_session: AsyncSession) -> None:
    def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not call Google")

    manager, encryptor = _manager(db_session, handler)
    account = await _account_with_token(
        db_session, encryptor, expires_at=datetime.now(UTC) - timedelta(minutes=5), refresh=None
    )
    with pytest.raises(CalendarReauthRequiredError):
        await manager.get_access_token(account.id)


async def test_missing_credentials_requires_reauth(db_session: AsyncSession) -> None:
    def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not call Google")

    manager, _ = _manager(db_session, handler)
    import uuid

    with pytest.raises(CalendarReauthRequiredError):
        await manager.get_access_token(uuid.uuid4())
