"""Authentication endpoints.

    GET  /auth/login     -> 307 redirect to Google consent (sets state cookie)
    GET  /auth/callback  -> handles Google's redirect; sets auth cookies; 307 to app
    POST /auth/refresh   -> rotates refresh token, re-issues access token
    POST /auth/logout    -> revokes the session, clears cookies
    GET  /auth/me        -> current authenticated user (protected)
    GET  /auth/status    -> { authenticated, user? } (never 401)

Routers are thin: they translate service results into cookies/redirects and
delegate all logic to the injected services.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api.v1.deps import (
    CurrentUser,
    OptionalUser,
    get_auth_service,
    get_cookie_service,
)
from app.application.services.auth_service import AuthService
from app.core.config import get_settings
from app.core.cookies import CookieService
from app.core.logging import get_logger
from app.exceptions.base import AuthenticationError
from app.schemas.auth import AuthStatusResponse, MessageResponse, UserOut

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

AuthSvc = Annotated[AuthService, Depends(get_auth_service)]
Cookies = Annotated[CookieService, Depends(get_cookie_service)]


def _frontend(path_suffix: str = "") -> str:
    s = get_settings()
    return f"{s.frontend_url}{path_suffix}"


@router.get("/login", summary="Begin Google OAuth login")
async def login(auth: AuthSvc, cookies: Cookies) -> RedirectResponse:
    url, state = await auth.build_login_redirect()
    response = RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    cookies.set_state_cookie(response, state)
    return response


@router.get("/callback", summary="Google OAuth callback")
async def callback(
    request: Request,
    auth: AuthSvc,
    cookies: Cookies,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    settings = get_settings()

    # User denied consent or Google returned an error.
    if error or not code or not state:
        response = RedirectResponse(
            _frontend(f"{settings.post_logout_path}?error=oauth_failed"),
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
        cookies.clear_state_cookie(response)
        return response

    cookie_state = request.cookies.get(settings.oauth_state_cookie_name)
    try:
        _user, access, refresh = await auth.complete_login(
            code=code, state=state, cookie_state=cookie_state
        )
    except AuthenticationError:
        # Do not leak details to the URL; the frontend shows a generic message.
        response = RedirectResponse(
            _frontend(f"{settings.post_logout_path}?error=auth_failed"),
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
        cookies.clear_state_cookie(response)
        return response

    response = RedirectResponse(
        _frontend(settings.post_login_path),
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )
    cookies.set_auth_cookies(response, access=access, refresh=refresh)
    cookies.clear_state_cookie(response)
    return response


@router.post("/refresh", response_model=MessageResponse, summary="Refresh the session")
async def refresh(
    request: Request, response: Response, auth: AuthSvc, cookies: Cookies
) -> MessageResponse:
    settings = get_settings()
    token = request.cookies.get(settings.refresh_cookie_name)
    if not token:
        raise AuthenticationError("Missing refresh token.")
    try:
        access, new_refresh = await auth.refresh(token)
    except AuthenticationError:
        cookies.clear_auth_cookies(response)
        raise
    cookies.set_auth_cookies(response, access=access, refresh=new_refresh)
    return MessageResponse(status="refreshed")


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Log out")
async def logout(request: Request, auth: AuthSvc, cookies: Cookies) -> Response:
    settings = get_settings()
    access = request.cookies.get(settings.access_cookie_name)
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    await auth.logout(access, refresh_token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    cookies.clear_auth_cookies(response)
    return response


@router.get("/me", response_model=UserOut, summary="Current authenticated user")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.get("/status", response_model=AuthStatusResponse, summary="Authentication status")
async def auth_status(user: OptionalUser) -> AuthStatusResponse:
    return AuthStatusResponse(
        authenticated=user is not None,
        user=UserOut.model_validate(user) if user is not None else None,
    )
