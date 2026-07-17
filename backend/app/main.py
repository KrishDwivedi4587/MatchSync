"""FastAPI application factory.

Wires together the foundation: configuration, structured logging, middleware,
exception handlers, and the versioned API router. Business features attach to
``api_router`` in later stages without touching this file.

The lifespan handler is the single place startup/shutdown work is coordinated.
Today it only logs and confirms configuration is loaded; later stages hook the
scheduler, warm caches, etc. here.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.middleware.auth import AuthenticationMiddleware
from app.api.middleware.logging import AccessLogMiddleware
from app.api.middleware.request_id import RequestIDMiddleware
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.exceptions.handlers import register_exception_handlers

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()
    logger.info(
        "application_startup",
        app=settings.app_name,
        environment=settings.environment,
        debug=settings.debug,
    )
    yield
    logger.info("application_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        # Hide interactive docs in production per Stage 1 security defaults.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
        lifespan=lifespan,
    )

    # Middleware order matters: request-id first (so logs are tagged), then
    # access logging. CORS is added by Starlette's own middleware.
    from starlette.middleware.cors import CORSMiddleware

    # Order (outermost first): CORS -> RequestID -> Authentication -> AccessLog.
    # Auth runs before AccessLog so authenticated requests log their user_id,
    # and after RequestID so any auth logs carry the correlation id.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,  # required for the httpOnly session cookie
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
