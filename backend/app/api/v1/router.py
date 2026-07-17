"""Aggregate router for API v1.

Individual feature routers are included here as they are built in later stages.
Versioning is URI-based (``/api/v1``) per Stage 1, Section 11.
"""

from fastapi import APIRouter

from app.api.v1.routers import (
    account,
    auth,
    calendars,
    fixtures,
    health,
    jobs,
    sports,
    subscriptions,
    sync,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(calendars.router)
api_router.include_router(sports.router)
api_router.include_router(fixtures.router)
api_router.include_router(sync.router)
api_router.include_router(jobs.router)
api_router.include_router(subscriptions.router)
api_router.include_router(account.router)
