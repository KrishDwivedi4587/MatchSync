"""Session service — server-side refresh sessions + OAuth CSRF state (Redis).

Responsibilities (no HTTP, no DB models):
- Create a session on login and mint the opaque refresh token.
- Rotate the refresh token on each refresh, with reuse/replay detection.
- Validate that a session still exists (enables instant revocation of access
  tokens, since ``get_current_user`` checks this).
- Revoke a single session (logout) or all of a user's sessions (logout-all).
- Store/consume one-time OAuth ``state`` -> PKCE verifier pairs.

Refresh token format: ``"{session_id}.{secret}"``. Only ``hash_token(secret)``
is stored, so a Redis dump never yields a usable token.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.config import Settings
from app.core.security import hash_token
from app.exceptions.base import AuthenticationError
from app.infrastructure.redis import SessionStore

_SESSION_PREFIX = "session:"
_USER_SESSIONS_PREFIX = "user_sessions:"
_OAUTH_STATE_PREFIX = "oauth_state:"


@dataclass(frozen=True)
class RotationResult:
    session_id: str
    refresh_token: str
    user_id: uuid.UUID


class SessionService:
    def __init__(self, store: SessionStore, settings: Settings) -> None:
        self._store = store
        self._settings = settings
        self._key = settings.secret_key.get_secret_value()
        self._ttl = settings.refresh_token_expire_days * 86400

    # --- login -------------------------------------------------------------
    async def create_session(self, user_id: uuid.UUID) -> tuple[str, str]:
        """Create a session; return (session_id, opaque_refresh_token)."""
        session_id = uuid.uuid4().hex
        secret = secrets.token_urlsafe(48)
        now = datetime.now(UTC).isoformat()
        record = {
            "user_id": str(user_id),
            "refresh_hash": hash_token(secret, self._key),
            "created_at": now,
            "rotated_at": now,
        }
        await self._store.set(_SESSION_PREFIX + session_id, json.dumps(record), self._ttl)
        await self._store.add_member(_USER_SESSIONS_PREFIX + str(user_id), session_id)
        return session_id, f"{session_id}.{secret}"

    # --- validation / refresh ---------------------------------------------
    async def get_session(self, session_id: str) -> dict[str, str] | None:
        raw = await self._store.get(_SESSION_PREFIX + session_id)
        return json.loads(raw) if raw else None

    async def rotate(self, refresh_token: str) -> RotationResult:
        """Validate + rotate a refresh token. Raises AuthenticationError if bad."""
        session_id, _, secret = refresh_token.partition(".")
        if not session_id or not secret:
            raise AuthenticationError("Malformed refresh token.")

        record = await self.get_session(session_id)
        if record is None:
            raise AuthenticationError("Session not found or expired.")

        if not secrets.compare_digest(record["refresh_hash"], hash_token(secret, self._key)):
            # A valid session but a stale secret => token reuse. Revoke the whole
            # session so a leaked token cannot be used going forward.
            await self.revoke(session_id)
            raise AuthenticationError("Refresh token reuse detected.")

        new_secret = secrets.token_urlsafe(48)
        record["refresh_hash"] = hash_token(new_secret, self._key)
        record["rotated_at"] = datetime.now(UTC).isoformat()
        await self._store.set(_SESSION_PREFIX + session_id, json.dumps(record), self._ttl)
        return RotationResult(
            session_id=session_id,
            refresh_token=f"{session_id}.{new_secret}",
            user_id=uuid.UUID(record["user_id"]),
        )

    # --- revocation --------------------------------------------------------
    async def revoke(self, session_id: str) -> None:
        record = await self.get_session(session_id)
        await self._store.delete(_SESSION_PREFIX + session_id)
        if record:
            await self._store.remove_member(_USER_SESSIONS_PREFIX + record["user_id"], session_id)

    async def revoke_all(self, user_id: uuid.UUID) -> None:
        key = _USER_SESSIONS_PREFIX + str(user_id)
        for session_id in await self._store.members(key):
            await self._store.delete(_SESSION_PREFIX + session_id)
        await self._store.delete(key)

    # --- OAuth state (CSRF) ------------------------------------------------
    async def store_oauth_state(self, state: str, code_verifier: str) -> None:
        await self._store.set(
            _OAUTH_STATE_PREFIX + state,
            code_verifier,
            self._settings.oauth_state_expire_minutes * 60,
        )

    async def consume_oauth_state(self, state: str) -> str | None:
        """Return the PKCE verifier for ``state`` and delete it (one-time use)."""
        key = _OAUTH_STATE_PREFIX + state
        verifier = await self._store.get(key)
        if verifier is not None:
            await self._store.delete(key)
        return verifier
