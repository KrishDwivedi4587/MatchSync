"""Application-layer API schemas (Stage 10)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.value_objects.enums import SubscriptionStatus, SubscriptionType


# --- subscriptions ---------------------------------------------------------
class CreateSubscriptionRequest(BaseModel):
    """Uses the same identifiers the browse endpoints return (sport key +
    provider external ids), resolved to internal ids by the service."""

    calendar_id: uuid.UUID
    sport: str
    scope: SubscriptionType
    competition_id: str | None = None  # provider external id
    team_id: str | None = None  # provider external id
    sync_frequency_minutes: int = Field(default=360, ge=15, le=10080)
    event_prefix: str | None = Field(default=None, max_length=64)


class BulkSubscribeRequest(BaseModel):
    items: list[CreateSubscriptionRequest] = Field(min_length=1, max_length=100)


class BulkUnsubscribeRequest(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class UpdateSubscriptionRequest(BaseModel):
    sync_frequency_minutes: int | None = Field(default=None, ge=15, le=10080)
    event_prefix: str | None = Field(default=None, max_length=64)
    clear_event_prefix: bool = False


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    scope: SubscriptionType
    status: SubscriptionStatus
    label: str
    sport_key: str | None
    sport_name: str | None
    competition_name: str | None
    team_name: str | None
    calendar_id: uuid.UUID
    calendar_name: str | None
    sync_frequency_minutes: int
    event_prefix: str | None
    last_synced_at: datetime | None
    next_sync_at: datetime | None
    created_at: datetime


class SubscriptionListResponse(BaseModel):
    subscriptions: list[SubscriptionOut]
    total: int


# --- onboarding ------------------------------------------------------------
class OnboardingStepOut(BaseModel):
    key: str
    done: bool


class OnboardingStateResponse(BaseModel):
    complete: bool
    current_step: str
    steps: list[OnboardingStepOut]


# --- account / preferences -------------------------------------------------
class UpdateProfileRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    timezone: str | None = Field(default=None, max_length=64)


class ChannelPreference(BaseModel):
    enabled: bool = False
    target: str | None = None


class NotificationPreferences(BaseModel):
    email: ChannelPreference = ChannelPreference()
    push: ChannelPreference = ChannelPreference()
    discord: ChannelPreference = ChannelPreference()
    slack: ChannelPreference = ChannelPreference()
    browser: ChannelPreference = ChannelPreference()
    reminders_minutes: list[int] = Field(default_factory=lambda: [60])


class DisplayPreferences(BaseModel):
    theme: Literal["light", "dark", "system"] = "system"


class PreferencesModel(BaseModel):
    notifications: NotificationPreferences = NotificationPreferences()
    display: DisplayPreferences = DisplayPreferences()


class PreferencesResponse(BaseModel):
    preferences: dict[str, Any]


# --- dashboard -------------------------------------------------------------
class DashboardResponse(BaseModel):
    calendar: dict[str, Any]
    subscriptions: dict[str, Any]
    sync: dict[str, Any]
    orchestration: dict[str, Any]
    providers: list[dict[str, Any]]
