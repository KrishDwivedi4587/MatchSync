"""Repositories for system tables: scheduler jobs, providers, application logs."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select

from app.domain.value_objects.enums import ProviderType
from app.persistence.models.system import (
    ApplicationLog,
    ProviderMetadata,
    SchedulerJob,
)
from app.persistence.repositories.base import BaseRepository


class SchedulerJobRepository(BaseRepository[SchedulerJob]):
    model = SchedulerJob

    async def get_by_key(self, key: str) -> SchedulerJob | None:
        return (
            await self.session.scalars(select(SchedulerJob).where(SchedulerJob.key == key))
        ).first()


class ProviderMetadataRepository(BaseRepository[ProviderMetadata]):
    model = ProviderMetadata

    async def get_by_key(self, key: str) -> ProviderMetadata | None:
        stmt = select(ProviderMetadata).where(ProviderMetadata.key == key)
        return (await self.session.scalars(stmt)).first()

    async def list_by_type(self, provider_type: ProviderType) -> Sequence[ProviderMetadata]:
        stmt = select(ProviderMetadata).where(ProviderMetadata.provider_type == provider_type)
        return (await self.session.scalars(stmt)).all()


class ApplicationLogRepository(BaseRepository[ApplicationLog]):
    model = ApplicationLog
    # Append-only: inherits create()/add(); no update/delete usage intended.
