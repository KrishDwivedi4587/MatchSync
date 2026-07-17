"""Authentication service — orchestrates the login/refresh/logout flows.

Coordinates the identity provider, user provisioning, session lifecycle, and
access-token minting. Owns the transaction boundary for login (commits the
session after the user is provisioned). Holds no HTTP/cookie concerns — the
router turns its results into cookies/redirects.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.session_service import SessionService
from app.application.services.user_service import UserService
from app.core.logging import get_logger
from app.core.security import JWTService
from app.domain.ports.identity_provider import IdentityProvider
from app.exceptions.base import AuthenticationError
from app.persistence.models.user import User

logger = get_logger(__name__)


def _generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        provider: IdentityProvider,
        user_service: UserService,
        session_service: SessionService,
        jwt_service: JWTService,
    ) -> None:
        self._session = session
        self._provider = provider
        self._users = user_service
        self._sessions = session_service
        self._jwt = jwt_service

    async def build_login_redirect(self) -> tuple[str, str]:
        """Return (authorization_url, state). Persists state -> PKCE verifier."""
        state = secrets.token_urlsafe(32)
        verifier, challenge = _generate_pkce()
        await self._sessions.store_oauth_state(state, verifier)
        url = self._provider.build_authorization_url(state=state, code_challenge=challenge)
        return url, state

    async def complete_login(
        self, *, code: str, state: str, cookie_state: str | None
    ) -> tuple[User, str, str]:
        """Exchange the code, provision the user, open a session.

        Returns (user, access_token, refresh_token).
        """
        # CSRF: the state echoed by Google must match the state we bound to the
        # browser via cookie AND still exist in our one-time server store.
        if not cookie_state or not secrets.compare_digest(state, cookie_state):
            logger.warning("auth.login.state_mismatch")
            raise AuthenticationError("Invalid OAuth state.")
        verifier = await self._sessions.consume_oauth_state(state)
        if verifier is None:
            logger.warning("auth.login.state_expired")
            raise AuthenticationError("OAuth state expired or already used.")

        tokens = await self._provider.exchange_code(code=code, code_verifier=verifier)
        profile = await self._provider.fetch_profile(tokens)

        user = await self._users.get_or_create_from_oauth(profile, tokens)
        await self._session.commit()

        session_id, refresh_token = await self._sessions.create_session(user.id)
        access_token = self._jwt.create_access_token(subject=str(user.id), session_id=session_id)
        logger.info("auth.login.success", user_id=str(user.id))
        return user, access_token, refresh_token

    async def refresh(self, refresh_token: str) -> tuple[str, str]:
        """Rotate the refresh token and mint a new access token."""
        result = await self._sessions.rotate(refresh_token)
        access_token = self._jwt.create_access_token(
            subject=str(result.user_id), session_id=result.session_id
        )
        logger.info("auth.refresh.success", user_id=str(result.user_id))
        return access_token, result.refresh_token

    async def logout(self, access_token: str | None, refresh_token: str | None) -> None:
        """Revoke the session identified by either credential (best effort)."""
        session_id = self._session_id_from(access_token, refresh_token)
        if session_id is not None:
            await self._sessions.revoke(session_id)
            logger.info("auth.logout", session_id=session_id)

    def _session_id_from(self, access_token: str | None, refresh_token: str | None) -> str | None:
        if access_token:
            try:
                return self._jwt.decode_access_token(access_token)["sid"]
            except AuthenticationError:
                pass  # expired/invalid access token — fall back to refresh
        if refresh_token:
            session_id, _, _ = refresh_token.partition(".")
            return session_id or None
        return None
