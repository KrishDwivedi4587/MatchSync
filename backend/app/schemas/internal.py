"""Internal persistence schemas.

These are **internal** Pydantic DTOs used to move validated data in/out of the
repository layer and seed/service code. They are NOT the public API contract —
API request/response models arrive with their endpoints in later stages.

Kept intentionally small: a representative Create/Update per major aggregate
plus read "record" models. Extend per feature as needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.domain.value_objects.enums import (
    CalendarProvider,
    CompetitionType,
    FixtureStatus,
    SportCategory,
    SubscriptionStatus,
    SubscriptionType,
    UserStatus,
)


class _ORMModel(BaseModel):
    """Base for read models: populate directly from ORM instances."""

    model_config = ConfigDict(from_attributes=True)


# --- User ------------------------------------------------------------------
class CreateUser(BaseModel):
    email: EmailStr
    display_name: str | None = None
    timezone: str = "UTC"
    locale: str | None = None


class UpdateUser(BaseModel):
    display_name: str | None = None
    timezone: str | None = None
    locale: str | None = None
    status: UserStatus | None = None


class UserRecord(_ORMModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    timezone: str
    status: UserStatus
    created_at: datetime


# --- Calendar --------------------------------------------------------------
class CalendarRecord(_ORMModel):
    id: uuid.UUID
    google_account_id: uuid.UUID
    provider: CalendarProvider
    external_calendar_id: str
    summary: str
    is_sync_target: bool


# --- Sport / Competition (seed/reference) ----------------------------------
class CreateSport(BaseModel):
    key: str
    name: str
    category: SportCategory
    provider_key: str
    display_order: int = 0


class CreateCompetition(BaseModel):
    sport_id: uuid.UUID
    provider_competition_id: str
    name: str
    type: CompetitionType = CompetitionType.LEAGUE
    country: str | None = None
    season: str | None = None


# --- Subscription ----------------------------------------------------------
class CreateSubscription(BaseModel):
    user_id: uuid.UUID
    target_calendar_id: uuid.UUID
    sport_id: uuid.UUID
    scope_type: SubscriptionType
    competition_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    sync_frequency_minutes: int = Field(default=360, ge=15)


# --- Fixture ---------------------------------------------------------------
class FixtureRecord(_ORMModel):
    id: uuid.UUID
    competition_id: uuid.UUID
    identity_key: str
    content_hash: str
    home_team_id: uuid.UUID | None
    away_team_id: uuid.UUID | None
    scheduled_start: datetime
    status: FixtureStatus


# --- Subscription read -----------------------------------------------------
class SubscriptionRecord(_ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    sport_id: uuid.UUID
    scope_type: SubscriptionType
    status: SubscriptionStatus
    last_synced_at: datetime | None
