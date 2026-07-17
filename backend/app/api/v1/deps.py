"""Shared FastAPI dependencies for API v1.

Provides the DB-session dependency plus the authentication dependency graph:
service factories and the ``current_user`` guards used by protected routes.
Everything is wired here so routers stay declarative and tests can override any
node (DB, session store, identity provider) in isolation.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.account_service import AccountService
from app.application.services.auth_service import AuthService
from app.application.services.calendar_service import CalendarService
from app.application.services.calendar_validator import CalendarValidator
from app.application.services.dashboard_service import DashboardService
from app.application.services.fixture_ingestion_service import FixtureIngestionService
from app.application.services.fixture_query_service import FixtureQueryService
from app.application.services.job_service import JobService
from app.application.services.metadata_service import MetadataService
from app.application.services.onboarding_service import OnboardingService
from app.application.services.orchestration_service import OrchestrationService
from app.application.services.session_service import SessionService
from app.application.services.sports_service import SportsService
from app.application.services.subscription_service import SubscriptionService
from app.application.services.sync_service import SyncService
from app.application.services.user_service import UserService
from app.core.config import Settings, get_settings
from app.core.cookies import CookieService
from app.core.security import JWTService
from app.domain.ports.identity_provider import IdentityProvider
from app.exceptions.base import AuthenticationError
from app.infrastructure.cache import Cache, RedisCache
from app.infrastructure.calendar.factory import CalendarProviderFactory
from app.infrastructure.crypto.encryption import TokenEncryptor
from app.infrastructure.google.oauth_client import GoogleOAuthClient
from app.infrastructure.google.token_manager import GoogleTokenManager
from app.infrastructure.heartbeat import HeartbeatRegistry
from app.infrastructure.http.resilient import ResilientHttpClient, get_http_client
from app.infrastructure.jobs import JobStore
from app.infrastructure.providers.registry import (
    SportsProviderRegistry,
    get_sports_registry,
)
from app.infrastructure.redis import RedisSessionStore, SessionStore, get_redis_client
from app.persistence.models.user import User
from app.persistence.repositories.catalog import (
    CompetitionRepository,
    SportRepository,
    TeamRepository,
)
from app.persistence.repositories.fixture import FixtureRepository
from app.persistence.repositories.ingestion import (
    FixtureVersionRepository,
    ImportRunRepository,
)
from app.persistence.repositories.sync import SyncRepository
from app.persistence.repositories.sync_engine import (
    SyncFixtureRepository,
    SyncMappingRepository,
    SyncRunRepository,
    SyncSubscriptionRepository,
)
from app.persistence.repositories.system import ProviderMetadataRepository
from app.persistence.repositories.user import (
    CalendarRepository,
    GoogleAccountRepository,
    OAuthTokenRepository,
    UserRepository,
)
from app.persistence.session import get_session


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Re-exported session dependency; the canonical name routers depend on."""
    async for session in get_session():
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


# --- singletons / stateless services --------------------------------------
def get_settings_dep() -> Settings:
    return get_settings()


def get_jwt_service() -> JWTService:
    return JWTService(get_settings())


def get_cookie_service() -> CookieService:
    return CookieService(get_settings())


def get_encryptor() -> TokenEncryptor:
    return TokenEncryptor(get_settings())


def get_session_store() -> SessionStore:
    return RedisSessionStore(get_redis_client())


def get_identity_provider() -> IdentityProvider:
    return GoogleOAuthClient(get_settings())


# --- request-scoped services ----------------------------------------------
def get_session_service(
    store: Annotated[SessionStore, Depends(get_session_store)],
) -> SessionService:
    return SessionService(store, get_settings())


def get_user_service(
    db: DbSession,
    encryptor: Annotated[TokenEncryptor, Depends(get_encryptor)],
) -> UserService:
    return UserService(
        UserRepository(db),
        GoogleAccountRepository(db),
        OAuthTokenRepository(db),
        encryptor,
    )


def get_auth_service(
    db: DbSession,
    provider: Annotated[IdentityProvider, Depends(get_identity_provider)],
    user_service: Annotated[UserService, Depends(get_user_service)],
    session_service: Annotated[SessionService, Depends(get_session_service)],
    jwt_service: Annotated[JWTService, Depends(get_jwt_service)],
) -> AuthService:
    return AuthService(db, provider, user_service, session_service, jwt_service)


# --- authorization guards --------------------------------------------------
async def _resolve_user(
    request: Request,
    jwt_service: JWTService,
    session_service: SessionService,
    user_service: UserService,
) -> User | None:
    """Validate the access cookie -> live session -> active user, or return None.

    Prefers claims decoded by AuthenticationMiddleware (avoids a second decode)
    but falls back to decoding the cookie directly so the dependency works even
    without the middleware (e.g. isolated tests).
    """
    settings = get_settings()
    claims = getattr(request.state, "access_claims", None)
    if claims is None:
        token = request.cookies.get(settings.access_cookie_name)
        if not token:
            return None
        try:
            claims = jwt_service.decode_access_token(token)
        except AuthenticationError:
            return None

    # Instant revocation: the JWT is valid but the session must still exist.
    if await session_service.get_session(claims["sid"]) is None:
        return None
    return await user_service.get_active_user(uuid.UUID(claims["sub"]))


async def get_current_user(
    request: Request,
    jwt_service: Annotated[JWTService, Depends(get_jwt_service)],
    session_service: Annotated[SessionService, Depends(get_session_service)],
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> User:
    """Require an authenticated, active user. Raises 401 otherwise."""
    user = await _resolve_user(request, jwt_service, session_service, user_service)
    if user is None:
        raise AuthenticationError()
    return user


async def get_current_user_optional(
    request: Request,
    jwt_service: Annotated[JWTService, Depends(get_jwt_service)],
    session_service: Annotated[SessionService, Depends(get_session_service)],
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> User | None:
    """Return the user if authenticated, else None (never raises)."""
    return await _resolve_user(request, jwt_service, session_service, user_service)


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]


# --- calendar platform (Stage 5) -------------------------------------------
def get_resilient_http() -> ResilientHttpClient:
    """Pooled, retrying HTTP client shared across the process."""
    return get_http_client()


def get_token_manager(
    db: DbSession,
    encryptor: Annotated[TokenEncryptor, Depends(get_encryptor)],
    http: Annotated[ResilientHttpClient, Depends(get_resilient_http)],
) -> GoogleTokenManager:
    return GoogleTokenManager(OAuthTokenRepository(db), encryptor, get_settings(), http)


def get_calendar_provider_factory(
    token_manager: Annotated[GoogleTokenManager, Depends(get_token_manager)],
    http: Annotated[ResilientHttpClient, Depends(get_resilient_http)],
) -> CalendarProviderFactory:
    return CalendarProviderFactory(token_manager, http)


def get_calendar_service(
    db: DbSession,
    factory: Annotated[CalendarProviderFactory, Depends(get_calendar_provider_factory)],
) -> CalendarService:
    return CalendarService(
        db,
        CalendarRepository(db),
        GoogleAccountRepository(db),
        factory,
        CalendarValidator(),
    )


# --- sports platform (Stage 6) ---------------------------------------------
def get_sports_registry_dep() -> SportsProviderRegistry:
    """Process-wide provider registry (lazy singleton)."""
    return get_sports_registry(get_settings())


def get_sports_cache() -> Cache:
    """Redis-backed metadata cache, shared across processes."""
    return RedisCache(get_redis_client())


def get_metadata_service(
    db: DbSession,
    registry: Annotated[SportsProviderRegistry, Depends(get_sports_registry_dep)],
) -> MetadataService:
    return MetadataService(
        db,
        registry,
        SportRepository(db),
        CompetitionRepository(db),
        TeamRepository(db),
        ProviderMetadataRepository(db),
    )


def get_sports_service(
    db: DbSession,
    registry: Annotated[SportsProviderRegistry, Depends(get_sports_registry_dep)],
    cache: Annotated[Cache, Depends(get_sports_cache)],
    metadata: Annotated[MetadataService, Depends(get_metadata_service)],
) -> SportsService:
    return SportsService(
        registry,
        cache,
        SportRepository(db),
        CompetitionRepository(db),
        TeamRepository(db),
        metadata,
    )


# --- fixture ingestion (Stage 7) -------------------------------------------
def get_fixture_ingestion_service(
    db: DbSession,
    sports: Annotated[SportsService, Depends(get_sports_service)],
    registry: Annotated[SportsProviderRegistry, Depends(get_sports_registry_dep)],
) -> FixtureIngestionService:
    return FixtureIngestionService(
        db,
        sports,
        registry,
        SportRepository(db),
        CompetitionRepository(db),
        TeamRepository(db),
        FixtureRepository(db),
        FixtureVersionRepository(db),
        ImportRunRepository(db),
        get_settings(),
    )


def get_fixture_query_service(db: DbSession) -> FixtureQueryService:
    return FixtureQueryService(
        FixtureRepository(db),
        FixtureVersionRepository(db),
        ImportRunRepository(db),
    )


# --- synchronization engine (Stage 8) --------------------------------------
def get_sync_service(
    db: DbSession,
    calendar: Annotated[CalendarService, Depends(get_calendar_service)],
) -> SyncService:
    """The engine talks to CalendarService and nothing else external."""
    return SyncService(
        db,
        calendar,
        SyncSubscriptionRepository(db),
        SyncFixtureRepository(db),
        SyncMappingRepository(db),
        SyncRunRepository(db),
        get_settings(),
    )


def get_sync_history_repository(db: DbSession) -> SyncRepository:
    """Reuses the Stage 3 read repository for history and run detail."""
    return SyncRepository(db)


def get_sync_subscription_repository(db: DbSession) -> SyncSubscriptionRepository:
    return SyncSubscriptionRepository(db)


# --- orchestration platform (Stage 9) --------------------------------------
def get_job_store() -> JobStore:
    return JobStore(get_redis_client(), retention_seconds=get_settings().job_retention_seconds)


def get_task_dispatcher() -> Any:
    """Celery dispatcher. Imported lazily so the API never pulls in the worker."""
    from app.tasks.base import CeleryDispatcher

    return CeleryDispatcher()


def get_heartbeats() -> HeartbeatRegistry:
    return HeartbeatRegistry(get_redis_client(), ttl_seconds=get_settings().heartbeat_ttl_seconds)


def get_job_service(
    store: Annotated[JobStore, Depends(get_job_store)],
    dispatcher: Annotated[Any, Depends(get_task_dispatcher)],
) -> JobService:
    return JobService(store, dispatcher)


def get_orchestration_service(
    db: DbSession,
    store: Annotated[JobStore, Depends(get_job_store)],
    beats: Annotated[HeartbeatRegistry, Depends(get_heartbeats)],
) -> OrchestrationService:
    from app.persistence.repositories.subscription import SubscriptionRepository
    from app.persistence.repositories.system import SchedulerJobRepository

    return OrchestrationService(
        store,
        beats,
        SubscriptionRepository(db),
        SchedulerJobRepository(db),
        stuck_after_seconds=get_settings().stuck_job_threshold_seconds,
    )


# --- application layer (Stage 10) ------------------------------------------
def get_subscription_service(db: DbSession) -> SubscriptionService:
    from app.persistence.repositories.application import ApplicationSubscriptionRepository

    return SubscriptionService(
        db,
        ApplicationSubscriptionRepository(db),
        CalendarRepository(db),
        SportRepository(db),
        CompetitionRepository(db),
        TeamRepository(db),
    )


def get_account_service(db: DbSession) -> AccountService:
    from app.persistence.repositories.preferences import UserPreferencesRepository

    return AccountService(db, UserRepository(db), UserPreferencesRepository(db))


def get_onboarding_service(
    db: DbSession,
    calendar: Annotated[CalendarService, Depends(get_calendar_service)],
) -> OnboardingService:
    from app.persistence.repositories.subscription import SubscriptionRepository
    from app.persistence.repositories.sync import SyncRepository

    return OnboardingService(calendar, SubscriptionRepository(db), SyncRepository(db))


def get_dashboard_service(
    db: DbSession,
    calendar: Annotated[CalendarService, Depends(get_calendar_service)],
    orchestration: Annotated[OrchestrationService, Depends(get_orchestration_service)],
) -> DashboardService:
    from app.persistence.repositories.application import ApplicationSubscriptionRepository

    return DashboardService(
        calendar,
        ApplicationSubscriptionRepository(db),
        SyncRunRepository(db),
        ProviderMetadataRepository(db),
        orchestration,
    )
