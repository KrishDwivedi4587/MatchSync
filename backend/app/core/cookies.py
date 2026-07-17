"""Cookie service — the single place auth cookies are set/cleared.

All decisions are config-driven so dev and prod differ only by settings:
- ``httponly=True``         : JS can never read the tokens (XSS mitigation).
- ``secure``                : HTTPS-only outside local dev (see cookies_secure).
- ``samesite=lax``          : sent on top-level navigations (the OAuth redirect)
                              but not on cross-site sub-requests (CSRF mitigation).
                              Same-origin in dev via the Next.js /api proxy.
- refresh cookie ``path``   : scoped to the auth routes so the long-lived refresh
                              token is only transmitted where it is needed.
"""

from __future__ import annotations

from starlette.responses import Response

from app.core.config import Settings

_REFRESH_PATH = "/api/v1/auth"


class CookieService:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def set_auth_cookies(self, response: Response, *, access: str, refresh: str) -> None:
        response.set_cookie(
            self._s.access_cookie_name,
            access,
            max_age=self._s.access_token_expire_minutes * 60,
            httponly=True,
            secure=self._s.cookies_secure,
            samesite=self._s.cookie_samesite,
            domain=self._s.cookie_domain,
            path="/",
        )
        response.set_cookie(
            self._s.refresh_cookie_name,
            refresh,
            max_age=self._s.refresh_token_expire_days * 86400,
            httponly=True,
            secure=self._s.cookies_secure,
            samesite=self._s.cookie_samesite,
            domain=self._s.cookie_domain,
            path=_REFRESH_PATH,
        )

    def set_state_cookie(self, response: Response, state: str) -> None:
        """Short-lived, httpOnly cookie binding the OAuth state (CSRF defence)."""
        response.set_cookie(
            self._s.oauth_state_cookie_name,
            state,
            max_age=self._s.oauth_state_expire_minutes * 60,
            httponly=True,
            secure=self._s.cookies_secure,
            samesite=self._s.cookie_samesite,
            domain=self._s.cookie_domain,
            path=_REFRESH_PATH,
        )

    def clear_auth_cookies(self, response: Response) -> None:
        response.delete_cookie(self._s.access_cookie_name, path="/", domain=self._s.cookie_domain)
        response.delete_cookie(
            self._s.refresh_cookie_name, path=_REFRESH_PATH, domain=self._s.cookie_domain
        )

    def clear_state_cookie(self, response: Response) -> None:
        response.delete_cookie(
            self._s.oauth_state_cookie_name, path=_REFRESH_PATH, domain=self._s.cookie_domain
        )
