"""End-to-end authentication flow tests.

Exercise the real FastAPI app with mocked Google OAuth and an in-memory session
store, driving the full login -> me -> refresh -> logout lifecycle through HTTP.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncGenerator
from urllib.parse import parse_qs, urlparse

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.api.v1.deps import get_db, get_identity_provider, get_session_store
from app.domain.ports.identity_provider import OAuthProfile, OAuthTokens
from app.exceptions.base import AuthenticationError
from app.main import app
from app.persistence.models.user import User


class FakeSessionStore:
    """In-memory SessionStore for tests (ttl ignored)."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._sets: dict[str, set[str]] = defaultdict(set)

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._kv[key] = value

    async def delete(self, key: str) -> None:
        self._kv.pop(key, None)

    async def add_member(self, key: str, member: str) -> None:
        self._sets[key].add(member)

    async def members(self, key: str) -> set[str]:
        return set(self._sets.get(key, set()))

    async def remove_member(self, key: str, member: str) -> None:
        self._sets[key].discard(member)


class FakeGoogleProvider:
    """Deterministic identity provider stand-in."""

    key = "google"

    def __init__(self) -> None:
        self.profile = OAuthProfile(
            provider="google",
            subject="google-sub-1",
            email="alice@example.com",
            email_verified=True,
            display_name="Alice",
        )
        self.tokens = OAuthTokens(
            access_token="g-access",
            refresh_token="g-refresh",
            id_token="g-id",
            expires_in=3600,
            scopes=["openid", "email", "profile"],
        )

    def build_authorization_url(self, *, state: str, code_challenge: str) -> str:
        return (
            "https://accounts.google.com/o/oauth2/v2/auth"
            f"?client_id=test&state={state}&code_challenge={code_challenge}"
        )

    async def exchange_code(self, *, code: str, code_verifier: str) -> OAuthTokens:
        if code == "bad-code":
            raise AuthenticationError("bad code")
        return self.tokens

    async def fetch_profile(self, tokens: OAuthTokens) -> OAuthProfile:
        return self.profile


@pytest_asyncio.fixture
async def auth_ctx(
    engine: AsyncEngine,
) -> AsyncGenerator[tuple[AsyncClient, FakeSessionStore, FakeGoogleProvider]]:
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db() -> AsyncGenerator:
        async with factory() as session:
            yield session

    store = FakeSessionStore()
    provider = FakeGoogleProvider()
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_identity_provider] = lambda: provider

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store, provider

    app.dependency_overrides.clear()


async def _login(client: AsyncClient, code: str = "good-code") -> None:
    """Drive the login + callback so the client ends up authenticated."""
    r = await client.get("/api/v1/auth/login")
    assert r.status_code == 307
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = await client.get(f"/api/v1/auth/callback?code={code}&state={state}")
    assert r.status_code == 307


# --------------------------------------------------------------------------
async def test_login_redirects_to_google(auth_ctx) -> None:
    client, _, _ = auth_ctx
    r = await client.get("/api/v1/auth/login")
    assert r.status_code == 307
    assert r.headers["location"].startswith("https://accounts.google.com/")
    # A state cookie must be set to bind the flow (CSRF).
    assert "ms_oauth_state" in r.cookies


async def test_full_login_creates_user_and_authenticates(auth_ctx, engine) -> None:
    client, _, provider = auth_ctx
    await _login(client)

    # Auth cookies present.
    assert client.cookies.get("ms_access")
    assert client.cookies.get("ms_refresh")

    # /me returns the provisioned user.
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == provider.profile.email

    # User row actually created.
    factory = async_sessionmaker(engine)
    async with factory() as s:
        count = await s.scalar(select(func.count()).select_from(User))
    assert count == 1


async def test_existing_user_login_creates_no_duplicate(auth_ctx, engine) -> None:
    client, _, _ = auth_ctx
    await _login(client)
    await _login(client)  # same Google subject -> same user

    factory = async_sessionmaker(engine)
    async with factory() as s:
        count = await s.scalar(select(func.count()).select_from(User))
    assert count == 1


async def test_me_requires_authentication(auth_ctx) -> None:
    client, _, _ = auth_ctx
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "not_authenticated"


async def test_status_endpoint(auth_ctx) -> None:
    client, _, _ = auth_ctx
    r = await client.get("/api/v1/auth/status")
    assert r.json() == {"authenticated": False, "user": None}

    await _login(client)
    r = await client.get("/api/v1/auth/status")
    body = r.json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == "alice@example.com"


async def test_refresh_rotates_token_and_keeps_session(auth_ctx) -> None:
    client, _, _ = auth_ctx
    await _login(client)
    old_refresh = client.cookies.get("ms_refresh")

    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 200
    assert r.json()["status"] == "refreshed"
    assert client.cookies.get("ms_refresh") != old_refresh  # rotated

    # Still authenticated with the new access token.
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_logout_revokes_session_instantly(auth_ctx) -> None:
    client, _, _ = auth_ctx
    await _login(client)
    access = client.cookies.get("ms_access")

    r = await client.post("/api/v1/auth/logout")
    assert r.status_code == 204

    # Even replaying the (still-unexpired) access token fails: the session is gone.
    # (Set on the client jar — httpx deprecates per-request cookies.)
    client.cookies.set("ms_access", access)
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401


async def test_callback_rejects_bad_state(auth_ctx) -> None:
    client, _, _ = auth_ctx
    await client.get("/api/v1/auth/login")  # sets a valid state cookie
    r = await client.get("/api/v1/auth/callback?code=good-code&state=forged")
    assert r.status_code == 307
    assert "error=auth_failed" in r.headers["location"]
    assert not client.cookies.get("ms_access")


async def test_callback_handles_provider_failure(auth_ctx) -> None:
    client, _, _ = auth_ctx
    r = await client.get("/api/v1/auth/login")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = await client.get(f"/api/v1/auth/callback?code=bad-code&state={state}")
    assert r.status_code == 307
    assert "error=auth_failed" in r.headers["location"]


async def test_callback_user_denied(auth_ctx) -> None:
    client, _, _ = auth_ctx
    r = await client.get("/api/v1/auth/callback?error=access_denied")
    assert r.status_code == 307
    assert "error=oauth_failed" in r.headers["location"]
