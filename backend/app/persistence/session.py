"""Database engine and session management (async).

Provides the SQLAlchemy 2.0 async engine, a session factory, a FastAPI
dependency for request-scoped sessions, a transactional context manager for
background/worker code, and a lightweight connectivity check for the readiness
probe.

``Base`` now lives in ``app.persistence.models.base`` (colocated with the mixins
it anchors) and is re-exported here for backwards compatibility — Alembic's
``env.py`` and any existing imports of ``session.Base`` keep working unchanged.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

# Re-export the declarative base (single source of truth in models.base).
from app.persistence.models.base import Base  # noqa: F401  (re-exported)


def _create_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url_async,
        echo=settings.debug,
        pool_pre_ping=True,  # detect stale connections before using them
        pool_size=10,  # steady-state pooled connections
        max_overflow=20,  # burst capacity above pool_size
        pool_recycle=1800,  # recycle connections every 30 min
        future=True,
    )


# One engine (and connection pool) per process.
engine: AsyncEngine = _create_engine()
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped async session.

    The session is closed automatically; committing is the caller's (service
    layer's) responsibility so a request maps to an explicit unit of work.
    """
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def transaction() -> AsyncGenerator[AsyncSession]:
    """Transactional session for worker/script code.

    Commits on success, rolls back on any exception, always closes. Use this in
    Celery tasks and seed scripts where there is no request lifecycle.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database_connection() -> bool:
    """Return True if the database answers a trivial query. Used by /ready."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
