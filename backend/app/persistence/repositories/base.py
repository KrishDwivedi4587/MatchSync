"""Generic repository.

One repository per aggregate (Stage 1). Repositories are the *only* place that
builds queries; they expose data-access methods and contain **no business
logic**. The generic base provides typed CRUD + soft-delete-aware listing;
concrete repositories add aggregate-specific lookups.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Typed async CRUD for a single model/aggregate root."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- helpers -----------------------------------------------------------
    @property
    def _soft_deletable(self) -> bool:
        return hasattr(self.model, "deleted_at")

    # --- reads -------------------------------------------------------------
    async def get(self, id_: uuid.UUID) -> ModelT | None:
        """Fetch by primary key (returns soft-deleted rows too; callers decide)."""
        return await self.session.get(self.model, id_)

    async def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> Sequence[ModelT]:
        stmt = select(self.model)
        if self._soft_deletable and not include_deleted:
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.scalars(stmt)
        return result.all()

    async def count(self, *, include_deleted: bool = False) -> int:
        stmt = select(func.count()).select_from(self.model)
        if self._soft_deletable and not include_deleted:
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return int(await self.session.scalar(stmt) or 0)

    async def exists(self, id_: uuid.UUID) -> bool:
        stmt = select(func.count()).select_from(self.model).where(self.model.id == id_)  # type: ignore[attr-defined]
        return bool(await self.session.scalar(stmt))

    # --- writes ------------------------------------------------------------
    async def add(self, instance: ModelT) -> ModelT:
        """Persist a pre-built instance. Flushes so PKs/defaults populate."""
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def create(self, **values: Any) -> ModelT:
        instance = self.model(**values)
        return await self.add(instance)

    async def update(self, instance: ModelT, **values: Any) -> ModelT:
        for key, value in values.items():
            setattr(instance, key, value)
        await self.session.flush()
        return instance

    async def delete(self, instance: ModelT) -> None:
        """Hard delete. Prefer ``soft_delete`` for user/business rows."""
        await self.session.delete(instance)
        await self.session.flush()

    async def soft_delete(self, instance: ModelT) -> ModelT:
        if not self._soft_deletable:
            raise TypeError(f"{self.model.__name__} is not soft-deletable")
        instance.deleted_at = func.now()  # type: ignore[attr-defined]
        await self.session.flush()
        return instance
