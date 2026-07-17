"""Identity-provider port.

Mirrors Stage 1's provider-abstraction philosophy for authentication: the auth
service depends on this interface, not on Google specifically, so Apple/Microsoft
sign-in can be added later by implementing another adapter — no changes to the
session/JWT/cookie layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class OAuthProfile:
    """Normalized identity returned by any provider."""

    provider: str
    subject: str  # provider-stable user id (the OIDC ``sub``)
    email: str
    email_verified: bool
    display_name: str | None = None
    picture: str | None = None
    locale: str | None = None


@dataclass(frozen=True)
class OAuthTokens:
    """Raw tokens returned by a provider's token endpoint."""

    access_token: str
    refresh_token: str | None = None
    id_token: str | None = None
    expires_in: int | None = None
    scopes: list[str] = field(default_factory=list)


class IdentityProvider(Protocol):
    """An OAuth2/OIDC identity provider."""

    key: str

    def build_authorization_url(self, *, state: str, code_challenge: str) -> str: ...

    async def exchange_code(self, *, code: str, code_verifier: str) -> OAuthTokens: ...

    async def fetch_profile(self, tokens: OAuthTokens) -> OAuthProfile: ...
