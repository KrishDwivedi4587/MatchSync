"""Dashboard aggregation (Stage 10).

Pure composition: one endpoint that fans out to the platforms already built and
assembles the home screen. It performs no synchronization, no provider calls
beyond what CalendarService already caches, and no new persistence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.application.services.calendar_service import CalendarService
from app.application.services.orchestration_service import OrchestrationService
from app.core.logging import get_logger
from app.domain.value_objects.enums import SubscriptionStatus
from app.exceptions.calendar import CalendarReauthRequiredError
from app.persistence.models.user import User
from app.persistence.repositories.application import ApplicationSubscriptionRepository
from app.persistence.repositories.sync_engine import SyncRunRepository
from app.persistence.repositories.system import ProviderMetadataRepository

logger = get_logger(__name__)


class DashboardService:
    def __init__(
        self,
        calendar_service: CalendarService,
        subscriptions: ApplicationSubscriptionRepository,
        runs: SyncRunRepository,
        providers: ProviderMetadataRepository,
        orchestration: OrchestrationService,
    ) -> None:
        self._calendar = calendar_service
        self._subscriptions = subscriptions
        self._runs = runs
        self._providers = providers
        self._orchestration = orchestration

    async def summary(self, user: User) -> dict[str, Any]:
        subs = list(await self._subscriptions.list_for_user_detailed(user.id))
        active = [s for s in subs if s.status is SubscriptionStatus.ACTIVE]
        paused = [s for s in subs if s.status is SubscriptionStatus.PAUSED]

        # Calendar connection (already cached by the Calendar Platform).
        try:
            status = await self._calendar.get_status(user)
            calendar = {
                "connected": status.connected,
                "account_email": status.account_email,
                "needs_reauth": status.needs_reauth,
                "default_calendar": status.default_calendar_summary,
                "calendar_count": status.calendar_count,
            }
        except CalendarReauthRequiredError:
            calendar = {"connected": False, "needs_reauth": True}

        sub_ids = [s.id for s in subs]
        metrics = await self._runs.metrics(sub_ids)
        statuses = await self._runs.status_counts(sub_ids)

        # Schedule window across all subscriptions.
        now = datetime.now(UTC)
        next_syncs = [_as_utc(s.next_sync_at) for s in active if s.next_sync_at]
        last_syncs = [_as_utc(s.last_synced_at) for s in subs if s.last_synced_at]

        # Provider health from the persisted metadata rows.
        provider_rows = await self._providers.list(limit=50)
        providers = [
            {
                "key": p.key,
                "name": p.name,
                "status": p.status.value,
                "last_success_at": p.last_success_at.isoformat() if p.last_success_at else None,
            }
            for p in provider_rows
        ]

        health = await self._orchestration.health()

        return {
            "calendar": calendar,
            "subscriptions": {
                "total": len(subs),
                "active": len(active),
                "paused": len(paused),
                "items": [_subscription_card(s) for s in subs],
            },
            "sync": {
                "runs": metrics.get("runs", 0),
                "created": metrics.get("created", 0),
                "updated": metrics.get("updated", 0),
                "deleted": metrics.get("deleted", 0),
                "by_status": statuses,
                "last_synced_at": max(last_syncs).isoformat() if last_syncs else None,
                "next_sync_at": min(next_syncs).isoformat() if next_syncs else None,
                "overdue": sum(1 for n in next_syncs if n <= now),
            },
            "orchestration": {
                "healthy": health.get("healthy", False),
                "workers_online": health.get("workers_online", 0),
                "scheduler_alive": health.get("scheduler_alive", False),
            },
            "providers": providers,
        }


def _subscription_card(subscription) -> dict[str, Any]:
    scope = subscription.scope_type.value
    if subscription.competition is not None:
        label = subscription.competition.name
    elif subscription.team is not None:
        label = subscription.team.name
    elif subscription.sport is not None:
        label = f"All {subscription.sport.name}"
    else:
        label = scope
    return {
        "id": str(subscription.id),
        "label": label,
        "scope": scope,
        "sport": subscription.sport.name if subscription.sport else None,
        "status": subscription.status.value,
        "calendar": subscription.target_calendar.summary if subscription.target_calendar else None,
        "last_synced_at": (
            subscription.last_synced_at.isoformat() if subscription.last_synced_at else None
        ),
        "next_sync_at": (
            subscription.next_sync_at.isoformat() if subscription.next_sync_at else None
        ),
    }


def _as_utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
