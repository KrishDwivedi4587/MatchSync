"""Global exception handlers.

Translate exceptions into the single, consistent error envelope defined in
Stage 1:

    {"error": {"code": ..., "message": ..., "details": ..., "request_id": ...}}

Every error carries the ``request_id`` so users can quote it to support and we
can correlate it with logs. Unexpected exceptions are logged at ERROR and, in
production, never leak internal details to the client.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import get_settings
from app.core.logging import get_logger
from app.exceptions.base import AppError
from app.utils.correlation import request_id_var

logger = get_logger(__name__)


def _envelope(*, code: str, message: str, details: Any | None = None) -> dict[str, dict[str, Any]]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id_var.get(),
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all handlers to the FastAPI application."""

    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=_envelope(code=exc.code, message=exc.message),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Route-miss 404s, 405s, and any raw HTTPException must use the same
        # envelope as application errors — without this they leak Starlette's
        # default {"detail": ...} shape, which the documented contract (and the
        # frontend client) do not expect.
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(code=code, message=str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_envelope(
                code="validation_error",
                message="Request validation failed.",
                details=exc.errors(),
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Log the full exception; never expose internals to the client in prod.
        logger.error("unhandled_exception", error=str(exc), exc_info=exc)
        message = (
            "Internal server error."
            if get_settings().is_production
            else f"Internal server error: {exc}"
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope(code="internal_error", message=message),
        )
