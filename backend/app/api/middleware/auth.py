"""Authentication middleware.

Best-effort decode of the access-token cookie into ``request.state`` so:
- the authorization dependencies can reuse the claims (no double decode), and
- access logs can carry the ``user_id`` for authenticated requests.

It NEVER blocks a request — route protection is the job of the
``get_current_user`` dependency. Invalid/expired/missing tokens simply leave the
claims unset.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings
from app.core.security import JWTService
from app.exceptions.base import AuthenticationError


class AuthenticationMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        settings = get_settings()
        request.state.access_claims = None
        request.state.user_id = None

        token = request.cookies.get(settings.access_cookie_name)
        if token:
            try:
                claims = JWTService(settings).decode_access_token(token)
                request.state.access_claims = claims
                request.state.user_id = claims.get("sub")
            except AuthenticationError:
                pass  # leave unauthenticated; dependencies decide what to do

        return await call_next(request)
