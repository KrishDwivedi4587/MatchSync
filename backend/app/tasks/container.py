"""Engine construction for the worker process.

Mirrors the FastAPI dependency graph without importing FastAPI. Workers build
exactly the same engines the API builds — the synchronization engine especially
is used **unchanged**.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.calendar_service import CalendarService
from app.application.services.calendar_validator import CalendarValidator
from app.application.services.fixture_ingestion_service import FixtureIngestionService
from app.application.services.metadata_service import MetadataService
from app.application.services.sports_service import SportsService
from app.application.services.sync_service import SyncService
from app.core.config import get_settings
from app.infrastructure.cache import RedisCache
from app.infrastructure.calendar.factory import CalendarProviderFactory
from app.infrastructure.crypto.encryption import TokenEncryptor
from app.infrastructure.google.token_manager import GoogleTokenManager
from app.infrastructure.http.resilient import get_http_client
from app.infrastructure.providers.registry import get_sports_registry
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
)


def build_calendar_service(session: AsyncSession) -> CalendarService:
    settings = get_settings()
    http = get_http_client()
    token_manager = GoogleTokenManager(
        OAuthTokenRepository(session), TokenEncryptor(settings), settings, http
    )
    factory = CalendarProviderFactory(token_manager, http)
    return CalendarService(
        session,
        CalendarRepository(session),
        GoogleAccountRepository(session),
        factory,
        CalendarValidator(),
    )


def build_sync_service(session: AsyncSession) -> SyncService:
    """The Stage 8 engine, constructed exactly as the API constructs it."""
    return SyncService(
        session,
        build_calendar_service(session),
        SyncSubscriptionRepository(session),
        SyncFixtureRepository(session),
        SyncMappingRepository(session),
        SyncRunRepository(session),
        get_settings(),
    )


def build_metadata_service(session: AsyncSession) -> MetadataService:
    return MetadataService(
        session,
        get_sports_registry(get_settings()),
        SportRepository(session),
        CompetitionRepository(session),
        TeamRepository(session),
        ProviderMetadataRepository(session),
    )


def build_sports_service(session: AsyncSession, redis) -> SportsService:
    return SportsService(
        get_sports_registry(get_settings()),
        RedisCache(redis),
        SportRepository(session),
        CompetitionRepository(session),
        TeamRepository(session),
        build_metadata_service(session),
    )


def build_ingestion_service(session: AsyncSession, redis) -> FixtureIngestionService:
    settings = get_settings()
    return FixtureIngestionService(
        session,
        build_sports_service(session, redis),
        get_sports_registry(settings),
        SportRepository(session),
        CompetitionRepository(session),
        TeamRepository(session),
        FixtureRepository(session),
        FixtureVersionRepository(session),
        ImportRunRepository(session),
        settings,
    )
