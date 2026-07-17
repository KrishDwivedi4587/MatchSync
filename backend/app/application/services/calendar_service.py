"""CalendarService — the calendar platform's public surface.

Future services call these methods and never learn that Google exists:

    await calendar_service.list_calendars(user)
    await calendar_service.create_event(user, calendar_id, event)
    await calendar_service.update_event(user, calendar_id, event_id, event)
    await calendar_service.delete_event(user, calendar_id, event_id)

Responsibilities: resolve the user's account -> provider, enforce ownership and
permissions, persist calendar discovery/selection through the existing Stage 3
repositories, and delegate event operations to the provider.

Explicitly NOT here: fixtures, subscriptions, sync/reconcile logic, scheduling.
Event operations are pass-through; they do not touch the ``calendar_events``
table (that mapping belongs to the synchronization stage).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.calendar_permissions import CalendarPermissions
from app.application.services.calendar_validator import CalendarValidator, ValidationResult
from app.core.logging import get_logger
from app.domain.ports.calendar_provider import (
    BatchResult,
    CalendarEventInput,
    CalendarEventRecord,
    CalendarProvider,
    EventQuery,
)
from app.domain.value_objects.enums import CalendarAccessRole
from app.domain.value_objects.enums import CalendarProvider as ProviderKey
from app.exceptions.calendar import CalendarNotFoundError, CalendarReauthRequiredError
from app.infrastructure.calendar.factory import CalendarProviderFactory
from app.persistence.models.account import GoogleAccount
from app.persistence.models.calendar import Calendar
from app.persistence.models.user import User
from app.persistence.repositories.user import CalendarRepository, GoogleAccountRepository

logger = get_logger(__name__)


@dataclass(frozen=True)
class CalendarStatus:
    connected: bool
    account_email: str | None
    has_calendar_scope: bool
    needs_reauth: bool
    calendar_count: int
    default_calendar_id: uuid.UUID | None
    default_calendar_summary: str | None


class CalendarService:
    def __init__(
        self,
        session: AsyncSession,
        calendars: CalendarRepository,
        accounts: GoogleAccountRepository,
        factory: CalendarProviderFactory,
        validator: CalendarValidator,
    ) -> None:
        self._session = session
        self._calendars = calendars
        self._accounts = accounts
        self._factory = factory
        self._validator = validator

    # --- account / provider resolution -------------------------------------
    async def _primary_account(self, user: User) -> GoogleAccount:
        accounts = await self._accounts.list_for_user(user.id)
        if not accounts:
            raise CalendarReauthRequiredError("No calendar account is connected.")
        # Prefer the account flagged primary; otherwise the first linked one.
        return next((a for a in accounts if a.is_primary), accounts[0])

    async def _provider_for(self, user: User) -> tuple[CalendarProvider, GoogleAccount]:
        account = await self._primary_account(user)
        provider = self._factory.for_account(ProviderKey(account.provider), account.id)
        return provider, account

    async def _account_ids(self, user: User) -> set[uuid.UUID]:
        return {a.id for a in await self._accounts.list_for_user(user.id)}

    async def _resolve_calendar(self, user: User, calendar_id: uuid.UUID) -> Calendar:
        """Load a calendar row, enforcing ownership and liveness."""
        calendar = await self._calendars.get(calendar_id)
        if calendar is None:
            raise CalendarNotFoundError()
        self._validator.validate_ownership(calendar, await self._account_ids(user))
        self._validator.validate_not_deleted(calendar)
        return calendar

    # --- discovery ---------------------------------------------------------
    async def discover_calendars(self, user: User) -> list[Calendar]:
        """Fetch calendars from the provider and reconcile the local catalog."""
        provider, account = await self._provider_for(user)
        remote = await provider.list_calendars()

        seen: set[str] = set()
        for info in remote:
            seen.add(info.external_id)
            existing = await self._calendars.get_by_external_id(account.id, info.external_id)
            if existing is None:
                await self._calendars.add(
                    Calendar(
                        google_account_id=account.id,
                        provider=account.provider,
                        external_calendar_id=info.external_id,
                        summary=info.summary,
                        description=info.description,
                        time_zone=info.time_zone,
                        is_primary=info.is_primary,
                        access_role=info.access_role.value,
                    )
                )
            else:
                existing.summary = info.summary
                existing.description = info.description
                existing.time_zone = info.time_zone
                existing.is_primary = info.is_primary
                existing.access_role = info.access_role.value
                existing.deleted_at = None  # reappeared

        # Calendars that vanished remotely are soft-deleted, never dropped.
        for local in await self._calendars.list_for_account(account.id):
            if local.external_calendar_id not in seen:
                await self._calendars.soft_delete(local)
                logger.info("calendar.discovery.removed", calendar_id=str(local.id))

        await self._session.commit()
        logger.info("calendar.discovery.synced", account_id=str(account.id), count=len(remote))
        return list(await self._calendars.list_for_account(account.id))

    async def list_calendars(self, user: User) -> list[Calendar]:
        """Locally-known calendars across all of the user's linked accounts."""
        return list(await self._calendars.list_for_user(user.id))

    async def get_calendar(self, user: User, calendar_id: uuid.UUID) -> Calendar:
        return await self._resolve_calendar(user, calendar_id)

    # --- selection ---------------------------------------------------------
    async def get_default_calendar(self, user: User) -> Calendar | None:
        for calendar in await self._calendars.list_for_user(user.id):
            if calendar.is_sync_target:
                return calendar
        return None

    async def set_default_calendar(self, user: User, calendar_id: uuid.UUID) -> Calendar:
        """Select the sync-target calendar, validating access before persisting."""
        calendar = await self._resolve_calendar(user, calendar_id)
        provider, _ = await self._provider_for(user)

        result = await self._validator.validate_remote(provider, calendar.external_calendar_id)
        if not result.valid:
            logger.warning(
                "calendar.selection.rejected",
                calendar_id=str(calendar_id),
                reason=result.reason,
            )
            raise CalendarNotFoundError(result.reason or "Calendar is not accessible.")
        CalendarPermissions.require_write(result.access_role or CalendarAccessRole.NONE)

        # Exactly one sync target per account.
        for other in await self._calendars.list_for_account(calendar.google_account_id):
            other.is_sync_target = other.id == calendar.id
        calendar.access_role = (result.access_role or CalendarAccessRole.NONE).value

        await self._session.commit()
        logger.info("calendar.selection.changed", calendar_id=str(calendar.id))
        return calendar

    # --- status / validation -----------------------------------------------
    async def get_status(self, user: User) -> CalendarStatus:
        accounts = await self._accounts.list_for_user(user.id)
        if not accounts:
            return CalendarStatus(False, None, False, True, 0, None, None)

        account = next((a for a in accounts if a.is_primary), accounts[0])
        # The provider declares which scopes it needs; the service stays agnostic.
        provider = self._factory.for_account(ProviderKey(account.provider), account.id)
        granted = set(account.scopes or [])
        has_scope = all(scope in granted for scope in provider.required_scopes)
        calendars = await self._calendars.list_for_user(user.id)
        default = next((c for c in calendars if c.is_sync_target), None)

        return CalendarStatus(
            connected=True,
            account_email=account.email,
            has_calendar_scope=has_scope,
            needs_reauth=not has_scope,
            calendar_count=len(calendars),
            default_calendar_id=default.id if default else None,
            default_calendar_summary=default.summary if default else None,
        )

    async def validate_calendar(self, user: User, calendar_id: uuid.UUID) -> ValidationResult:
        calendar = await self._resolve_calendar(user, calendar_id)
        provider, _ = await self._provider_for(user)
        return await self._validator.validate_remote(provider, calendar.external_calendar_id)

    # --- event platform (generic; no sports concepts) ------------------------
    async def _writable_target(
        self, user: User, calendar_id: uuid.UUID
    ) -> tuple[CalendarProvider, str]:
        calendar = await self._resolve_calendar(user, calendar_id)
        provider, _ = await self._provider_for(user)
        CalendarPermissions.require_write(
            CalendarAccessRole(calendar.access_role or CalendarAccessRole.NONE.value)
        )
        return provider, calendar.external_calendar_id

    async def create_event(
        self, user: User, calendar_id: uuid.UUID, event: CalendarEventInput
    ) -> CalendarEventRecord:
        provider, external_id = await self._writable_target(user, calendar_id)
        return await provider.create_event(external_id, event)

    async def update_event(
        self, user: User, calendar_id: uuid.UUID, event_id: str, event: CalendarEventInput
    ) -> CalendarEventRecord:
        provider, external_id = await self._writable_target(user, calendar_id)
        return await provider.update_event(external_id, event_id, event)

    async def delete_event(self, user: User, calendar_id: uuid.UUID, event_id: str) -> None:
        provider, external_id = await self._writable_target(user, calendar_id)
        await provider.delete_event(external_id, event_id)

    async def get_event(
        self, user: User, calendar_id: uuid.UUID, event_id: str
    ) -> CalendarEventRecord | None:
        calendar = await self._resolve_calendar(user, calendar_id)
        provider, _ = await self._provider_for(user)
        return await provider.get_event(calendar.external_calendar_id, event_id)

    async def list_events(
        self, user: User, calendar_id: uuid.UUID, query: EventQuery
    ) -> list[CalendarEventRecord]:
        calendar = await self._resolve_calendar(user, calendar_id)
        provider, _ = await self._provider_for(user)
        return await provider.list_events(calendar.external_calendar_id, query)

    async def search_events(
        self, user: User, calendar_id: uuid.UUID, query: EventQuery
    ) -> list[CalendarEventRecord]:
        calendar = await self._resolve_calendar(user, calendar_id)
        provider, _ = await self._provider_for(user)
        return await provider.search_events(calendar.external_calendar_id, query)

    async def batch_create_events(
        self, user: User, calendar_id: uuid.UUID, events: list[CalendarEventInput]
    ) -> list[BatchResult]:
        provider, external_id = await self._writable_target(user, calendar_id)
        return await provider.batch_create(external_id, events)

    async def batch_update_events(
        self, user: User, calendar_id: uuid.UUID, items: list[tuple[str, CalendarEventInput]]
    ) -> list[BatchResult]:
        provider, external_id = await self._writable_target(user, calendar_id)
        return await provider.batch_update(external_id, items)

    async def batch_delete_events(
        self, user: User, calendar_id: uuid.UUID, event_ids: list[str]
    ) -> list[BatchResult]:
        provider, external_id = await self._writable_target(user, calendar_id)
        return await provider.batch_delete(external_id, event_ids)
