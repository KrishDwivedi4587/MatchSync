"""Onboarding state (Stage 10).

Onboarding progress is **computed from existing data**, never stored. This makes
"recovery after interruption" automatic: a user who closes the tab mid-flow
returns to exactly the right step because the step is derived from what they have
actually done, not from a saved cursor that could drift.

Steps:
    1. connect_google   — a linked account exists
    2. grant_calendar   — the token carries the calendar scopes
    3. select_calendar  — a sync-target calendar is chosen
    4. add_subscription — at least one subscription exists
    5. first_sync       — at least one sync run has happened

Complete once a calendar is selected AND a subscription exists (a first sync is
encouraged but not required to consider onboarding "done").
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.services.calendar_service import CalendarService
from app.exceptions.calendar import CalendarReauthRequiredError
from app.persistence.models.user import User
from app.persistence.repositories.subscription import SubscriptionRepository
from app.persistence.repositories.sync import SyncRepository


@dataclass(frozen=True)
class OnboardingStep:
    key: str
    done: bool


@dataclass(frozen=True)
class OnboardingState:
    complete: bool
    current_step: str
    steps: tuple[OnboardingStep, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "complete": self.complete,
            "current_step": self.current_step,
            "steps": [{"key": s.key, "done": s.done} for s in self.steps],
        }


class OnboardingService:
    def __init__(
        self,
        calendar_service: CalendarService,
        subscriptions: SubscriptionRepository,
        sync_history: SyncRepository,
    ) -> None:
        self._calendar = calendar_service
        self._subscriptions = subscriptions
        self._sync_history = sync_history

    async def state(self, user: User) -> OnboardingState:
        try:
            status = await self._calendar.get_status(user)
            connected = status.connected
            has_scope = status.has_calendar_scope
            calendar_selected = status.default_calendar_id is not None
        except CalendarReauthRequiredError:
            connected = has_scope = calendar_selected = False

        subs = await self._subscriptions.list_for_user(user.id)
        has_subscription = len(subs) > 0

        first_sync = False
        if subs:
            for sub in subs:
                runs = await self._sync_history.list_for_subscription(sub.id, limit=1)
                if runs:
                    first_sync = True
                    break

        steps = (
            OnboardingStep("connect_google", connected),
            OnboardingStep("grant_calendar", has_scope),
            OnboardingStep("select_calendar", calendar_selected),
            OnboardingStep("add_subscription", has_subscription),
            OnboardingStep("first_sync", first_sync),
        )
        current = next((s.key for s in steps if not s.done), "done")
        complete = calendar_selected and has_subscription
        return OnboardingState(complete=complete, current_step=current, steps=steps)
