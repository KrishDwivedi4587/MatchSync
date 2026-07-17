"""Google OAuth 2.0 / OIDC client (Authorization Code flow + PKCE).

Implements the ``IdentityProvider`` port. Talks to Google's discovery endpoints
over HTTPS; contains no session/cookie/DB concerns.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.ports.identity_provider import OAuthProfile, OAuthTokens
from app.exceptions.base import AuthenticationError, RetryableError
from app.infrastructure.google.endpoints import (
    GOOGLE_AUTH_ENDPOINT as _AUTH_ENDPOINT,
)
from app.infrastructure.google.endpoints import (
    GOOGLE_TOKEN_ENDPOINT as _TOKEN_ENDPOINT,
)
from app.infrastructure.google.endpoints import (
    GOOGLE_USERINFO_ENDPOINT as _USERINFO_ENDPOINT,
)

logger = get_logger(__name__)

_TIMEOUT = httpx.Timeout(10.0)


class GoogleOAuthClient:
    key = "google"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def build_authorization_url(self, *, state: str, code_challenge: str) -> str:
        params = {
            "client_id": self._settings.google_client_id,
            "redirect_uri": self._settings.google_redirect_uri,
            "response_type": "code",
            "scope": " ".join(self._settings.google_scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",  # request a refresh token
            "include_granted_scopes": "true",  # incremental auth for Stage 5
            "prompt": "consent",
        }
        return f"{_AUTH_ENDPOINT}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> OAuthTokens:
        data = {
            "code": code,
            "client_id": self._settings.google_client_id,
            "client_secret": self._settings.google_client_secret.get_secret_value(),
            "redirect_uri": self._settings.google_redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(_TOKEN_ENDPOINT, data=data)
        except httpx.HTTPError as exc:
            raise RetryableError("Failed to reach Google token endpoint.") from exc

        if resp.status_code >= 500:
            raise RetryableError("Google token endpoint is unavailable.")
        if resp.status_code != 200:
            # 4xx: bad/expired code, misconfigured client. Do not log the body
            # (it may echo the code); log only the status.
            logger.warning("google_token_exchange_failed", status=resp.status_code)
            raise AuthenticationError("Google token exchange failed.")

        payload = resp.json()
        return OAuthTokens(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            id_token=payload.get("id_token"),
            expires_in=payload.get("expires_in"),
            scopes=(payload.get("scope") or "").split(),
        )

    async def fetch_profile(self, tokens: OAuthTokens) -> OAuthProfile:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    _USERINFO_ENDPOINT,
                    headers={"Authorization": f"Bearer {tokens.access_token}"},
                )
        except httpx.HTTPError as exc:
            raise RetryableError("Failed to reach Google userinfo endpoint.") from exc

        if resp.status_code >= 500:
            raise RetryableError("Google userinfo endpoint is unavailable.")
        if resp.status_code != 200:
            raise AuthenticationError("Failed to fetch Google profile.")

        data = resp.json()
        if not data.get("sub") or not data.get("email"):
            raise AuthenticationError("Google profile is missing required fields.")

        return OAuthProfile(
            provider=self.key,
            subject=data["sub"],
            email=data["email"].lower(),
            email_verified=bool(data.get("email_verified", False)),
            display_name=data.get("name"),
            picture=data.get("picture"),
            locale=data.get("locale"),
        )
