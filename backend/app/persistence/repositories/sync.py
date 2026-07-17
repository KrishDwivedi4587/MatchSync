"""Sync history repository (SyncHistory aggregate, incl. its operations)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import desc, select
from sqlalchemy.orm import selectinload

from app.persistence.models.sync import SyncHistory
from app.persistence.repositories.base import BaseRepository


class SyncRepository(BaseRepository[SyncHistory]):
    model = SyncHistory

    async def list_for_subscription(
        self, subscription_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> Sequence[SyncHistory]:
        """Newest-first run history for a subscription (paginated)."""
        stmt = (
            select(SyncHistory)
            .where(SyncHistory.subscription_id == subscription_id)
            .order_by(desc(SyncHistory.created_at))
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.scalars(stmt)).all()

    async def get_with_operations(self, run_id: uuid.UUID) -> SyncHistory | None:
        """Load a run with its operations eagerly (avoids N+1 on detail views)."""
        stmt = (
            select(SyncHistory)
            .where(SyncHistory.id == run_id)
            .options(selectinload(SyncHistory.operations))
        )
        return await self.session.scalar(stmt)
