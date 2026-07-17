"""The action executor.

Consumes a ``SyncPlan`` and performs it **exclusively through CalendarService**.
It never touches Google, a sports API, or the database — it returns per-action
results and lets the engine persist them.

Separation of planning and execution is the load-bearing design choice: the
planner is pure and previewable, the executor is effectful and dumb.

**No rollback.** Calendar mutations are external side-effects and cannot be
undone safely. Recovery is *forward*: a mapping's `synced_content_hash` is only
written after its calendar call succeeds, so an aborted run leaves the database
describing exactly what is true, and the next run re-plans the remainder. Because
event ids are deterministic, replay is idempotent.

Fatal errors (quota exhausted, access revoked, provider down) abort the remaining
actions rather than hammering a dead endpoint. Item-level failures never abort.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TypeVar

from app.application.services.calendar_service import CalendarService
from app.core.logging import get_logger
from app.domain.ports.calendar_provider import BatchResult, CalendarEventInput
from app.domain.sync.models import SyncAction, SyncActionType, SyncPlan
from app.exceptions.calendar import (
    CalendarNotFoundError,
    CalendarPermissionError,
    CalendarReauthRequiredError,
    EventConflictError,
    QuotaExceededError,
)
from app.exceptions.provider import ProviderUnavailableError, RateLimitError
from app.persistence.models.user import User

logger = get_logger(__name__)

# Errors that make the rest of the run pointless. The remainder is left unplanned
# and picked up by the next run, unchanged, because nothing was marked synced.
FATAL_ERRORS = (
    QuotaExceededError,
    RateLimitError,
    ProviderUnavailableError,
    CalendarReauthRequiredError,
    CalendarPermissionError,
)


@dataclass(frozen=True)
class ActionResult:
    action: SyncAction
    success: bool
    external_event_id: str | None = None
    error_code: str | None = None
    message: str | None = None
    # True when the provider rejected a duplicate insert — proof that invariant
    # I2 held even though the application believed a create was needed.
    duplicate_prevented: bool = False


@dataclass
class ExecutionOutcome:
    results: list[ActionResult] = field(default_factory=list)
    api_calls: int = 0
    aborted: bool = False
    abort_reason: str | None = None

    @property
    def succeeded(self) -> list[ActionResult]:
        return [r for r in self.results if r.success]

    @property
    def failed(self) -> list[ActionResult]:
        return [r for r in self.results if not r.success]

    @property
    def duplicates_prevented(self) -> int:
        return sum(1 for r in self.results if r.duplicate_prevented)


class SyncExecutor:
    def __init__(self, calendar_service: CalendarService, *, batch_size: int = 50) -> None:
        self._calendar = calendar_service
        self._batch_size = batch_size

    async def execute(
        self,
        plan: SyncPlan,
        *,
        user: User,
        calendar_id: uuid.UUID,
        bodies: dict[uuid.UUID, CalendarEventInput],
        cancel_bodies: dict[uuid.UUID, CalendarEventInput],
    ) -> ExecutionOutcome:
        """Execute the plan. ``bodies`` maps fixture_id -> rendered event."""
        outcome = ExecutionOutcome()
        if plan.is_empty:
            # Invariant I5: an empty plan performs zero calendar API calls.
            logger.info("sync.execute.noop", subscription_id=str(plan.subscription_id))
            return outcome

        try:
            await self._creates(plan, user, calendar_id, bodies, outcome)
            await self._updates(plan, user, calendar_id, bodies, outcome)
            await self._cancels(plan, user, calendar_id, cancel_bodies, outcome)
            await self._deletes(plan, user, calendar_id, outcome)
        except FATAL_ERRORS as exc:
            outcome.aborted = True
            outcome.abort_reason = getattr(exc, "code", exc.__class__.__name__)
            logger.warning(
                "sync.execute.aborted",
                subscription_id=str(plan.subscription_id),
                reason=outcome.abort_reason,
                completed=len(outcome.results),
            )

        # Conflicts never call the calendar; they are recorded, not executed.
        for action in plan.of_type(SyncActionType.CONFLICT):
            outcome.results.append(
                ActionResult(action, success=False, error_code="conflict", message=action.reason)
            )
        return outcome

    # --- create / recreate ---------------------------------------------------
    async def _creates(
        self,
        plan: SyncPlan,
        user: User,
        calendar_id: uuid.UUID,
        bodies: dict[uuid.UUID, CalendarEventInput],
        outcome: ExecutionOutcome,
    ) -> None:
        actions = [
            a
            for a in plan.of_type(SyncActionType.CREATE, SyncActionType.RECREATE)
            if a.fixture_id in bodies
        ]
        for chunk in _chunks(actions, self._batch_size):
            # fixture_id is non-None by construction (actions were filtered on
            # membership in `bodies`); the walrus narrows without filtering.
            events = [bodies[fid] for a in chunk if (fid := a.fixture_id) is not None]
            results = await self._calendar.batch_create_events(user, calendar_id, events)
            outcome.api_calls += 1
            await self._absorb_creates(chunk, events, results, user, calendar_id, outcome)

    async def _absorb_creates(
        self,
        actions: list[SyncAction],
        events: list[CalendarEventInput],
        results: list[BatchResult],
        user: User,
        calendar_id: uuid.UUID,
        outcome: ExecutionOutcome,
    ) -> None:
        by_index = {r.index: r for r in results}
        for index, action in enumerate(actions):
            result = by_index.get(index)
            if result is None:
                outcome.results.append(
                    ActionResult(action, False, error_code="missing_batch_result")
                )
                continue

            if result.success and result.event:
                outcome.results.append(ActionResult(action, True, result.event.id))
                continue

            # The provider rejected a duplicate insert: the event already exists
            # under our deterministic id. Invariant I2 held. Repair by patching.
            if _is_conflict(result.error_code):
                event_id = events[index].event_id
                repaired = await self._repair_conflict(
                    action, event_id, events[index], user, calendar_id, outcome
                )
                outcome.results.append(repaired)
                continue

            outcome.results.append(
                ActionResult(
                    action, False, error_code=result.error_code, message=result.error_message
                )
            )

    async def _repair_conflict(
        self,
        action: SyncAction,
        event_id: str | None,
        body: CalendarEventInput,
        user: User,
        calendar_id: uuid.UUID,
        outcome: ExecutionOutcome,
    ) -> ActionResult:
        if not event_id:
            return ActionResult(action, False, error_code="duplicate_no_event_id")
        try:
            record = await self._calendar.update_event(user, calendar_id, event_id, body)
            outcome.api_calls += 1
            logger.info(
                "sync.duplicate_prevented", identity_key=action.identity_key, event_id=event_id
            )
            return ActionResult(action, True, record.id, duplicate_prevented=True)
        except (CalendarNotFoundError, EventConflictError) as exc:
            return ActionResult(action, False, error_code=getattr(exc, "code", "conflict"))

    # --- update / cancel -----------------------------------------------------
    async def _updates(
        self,
        plan: SyncPlan,
        user: User,
        calendar_id: uuid.UUID,
        bodies: dict[uuid.UUID, CalendarEventInput],
        outcome: ExecutionOutcome,
    ) -> None:
        actions = [
            a
            for a in plan.of_type(SyncActionType.UPDATE)
            if a.external_event_id and a.fixture_id in bodies
        ]
        await self._patch(actions, bodies, user, calendar_id, outcome)

    async def _cancels(
        self,
        plan: SyncPlan,
        user: User,
        calendar_id: uuid.UUID,
        bodies: dict[uuid.UUID, CalendarEventInput],
        outcome: ExecutionOutcome,
    ) -> None:
        actions = [
            a
            for a in plan.of_type(SyncActionType.CANCEL)
            if a.external_event_id and a.fixture_id in bodies
        ]
        await self._patch(actions, bodies, user, calendar_id, outcome)

    async def _patch(
        self,
        actions: list[SyncAction],
        bodies: dict[uuid.UUID, CalendarEventInput],
        user: User,
        calendar_id: uuid.UUID,
        outcome: ExecutionOutcome,
    ) -> None:
        for chunk in _chunks(actions, self._batch_size):
            # Both ids are non-None by construction (see the action filters);
            # the walruses narrow without filtering.
            items = [
                (eid, bodies[fid])
                for a in chunk
                if (eid := a.external_event_id) is not None and (fid := a.fixture_id) is not None
            ]
            results = await self._calendar.batch_update_events(user, calendar_id, items)
            outcome.api_calls += 1
            by_index = {r.index: r for r in results}

            for index, action in enumerate(chunk):
                result = by_index.get(index)
                if result and result.success:
                    outcome.results.append(ActionResult(action, True, action.external_event_id))
                    continue
                code = result.error_code if result else "missing_batch_result"
                if _is_not_found(code):
                    # The event vanished. Recreate it now rather than waiting a run.
                    recreated = await self._recreate(action, bodies, user, calendar_id, outcome)
                    outcome.results.append(recreated)
                    continue
                outcome.results.append(
                    ActionResult(
                        action,
                        False,
                        error_code=code,
                        message=result.error_message if result else None,
                    )
                )

    async def _recreate(
        self,
        action: SyncAction,
        bodies: dict[uuid.UUID, CalendarEventInput],
        user: User,
        calendar_id: uuid.UUID,
        outcome: ExecutionOutcome,
    ) -> ActionResult:
        body = bodies[action.fixture_id]  # type: ignore[index]
        try:
            record = await self._calendar.create_event(user, calendar_id, body)
            outcome.api_calls += 1
            return ActionResult(action, True, record.id, message="recreated_missing_event")
        except EventConflictError:
            return ActionResult(action, False, error_code="event_conflict")

    # --- delete --------------------------------------------------------------
    async def _deletes(
        self, plan: SyncPlan, user: User, calendar_id: uuid.UUID, outcome: ExecutionOutcome
    ) -> None:
        actions = [a for a in plan.of_type(SyncActionType.DELETE) if a.external_event_id]
        for chunk in _chunks(actions, self._batch_size):
            # external_event_id is non-None by construction (filtered above).
            event_ids = [eid for a in chunk if (eid := a.external_event_id) is not None]
            results = await self._calendar.batch_delete_events(user, calendar_id, event_ids)
            outcome.api_calls += 1
            by_index = {r.index: r for r in results}

            for index, action in enumerate(chunk):
                result = by_index.get(index)
                # A missing event is the desired end state, not a failure.
                if result and (result.success or _is_not_found(result.error_code)):
                    outcome.results.append(ActionResult(action, True, action.external_event_id))
                    continue
                outcome.results.append(
                    ActionResult(
                        action, False, error_code=result.error_code if result else "missing"
                    )
                )


_T = TypeVar("_T")


def _chunks(items: list[_T], size: int) -> list[list[_T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _is_conflict(code: str | None) -> bool:
    return code is not None and code.lower() in {
        "duplicate",
        "conflict",
        "409",
        "calendar_event_conflict",
    }


def _is_not_found(code: str | None) -> bool:
    return code is not None and code.lower() in {"notfound", "not_found", "404", "410", "deleted"}


def utcnow() -> datetime:
    return datetime.now(UTC)
