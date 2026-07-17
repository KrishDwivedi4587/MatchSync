"""Security primitives: JWT access tokens and token hashing.

Kept in ``core`` (cross-cutting security). This module knows nothing about
users, Google, or the database — it only signs/verifies short-lived access
tokens and hashes opaque secrets. Instant revocation is handled at the session
layer (Redis), not here.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import jwt

from app.core.config import Settings
from app.exceptions.base import AuthenticationError


class TokenType(StrEnum):
    ACCESS = "access"


def hash_token(secret: str, key: str) -> str:
    """Keyed hash (HMAC-SHA256) of an opaque secret, for at-rest comparison.

    Used to store refresh-token secrets so a Redis dump never reveals a usable
    token. Keyed with the app secret for defence in depth.
    """
    return hmac.new(key.encode(), secret.encode(), hashlib.sha256).hexdigest()


class JWTService:
    """Signs and verifies stateless access-token JWTs (HS256)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._secret = settings.secret_key.get_secret_value()
        self._alg = settings.jwt_algorithm
        self._issuer = settings.app_name

    def create_access_token(
        self, *, subject: str, session_id: str, extra: dict[str, Any] | None = None
    ) -> str:
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "sub": subject,
            "sid": session_id,
            "type": TokenType.ACCESS.value,
            "iss": self._issuer,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=self._settings.access_token_expire_minutes),
            "jti": uuid.uuid4().hex,
            **(extra or {}),
        }
        return jwt.encode(payload, self._secret, algorithm=self._alg)

    def decode_access_token(self, token: str) -> dict[str, Any]:
        """Verify signature/expiry and return claims, or raise AuthenticationError."""
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=[self._alg],
                issuer=self._issuer,
                options={"require": ["exp", "sub", "sid"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Access token has expired.") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("Invalid access token.") from exc

        if claims.get("type") != TokenType.ACCESS.value:
            raise AuthenticationError("Wrong token type.")
        return claims
