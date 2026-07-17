"""SQLAlchemy ORM models.

Importing this package registers every model on ``Base.metadata`` so Alembic
autogenerate and ``metadata.create_all`` (tests) see the full schema. Import
models from here (or their modules) — never redefine ``Base`` elsewhere.
"""

from app.persistence.models.account import GoogleAccount, OAuthToken
from app.persistence.models.base import (
    AuditMixin,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDMixin,
)
from app.persistence.models.calendar import Calendar
from app.persistence.models.calendar_event import CalendarEvent
from app.persistence.models.catalog import (
    Competition,
    Sport,
    Team,
    team_competition,
)
from app.persistence.models.fixture import Fixture
from app.persistence.models.ingestion import FixtureVersion, ImportRun
from app.persistence.models.preferences import UserPreferences
from app.persistence.models.subscription import Subscription
from app.persistence.models.sync import SyncHistory, SyncOperation
from app.persistence.models.system import (
    ApplicationLog,
    ProviderMetadata,
    SchedulerJob,
)
from app.persistence.models.user import User

__all__ = [
    "ApplicationLog",
    "AuditMixin",
    # Base + mixins
    "Base",
    "Calendar",
    "CalendarEvent",
    "Competition",
    "Fixture",
    "FixtureVersion",
    "GoogleAccount",
    "ImportRun",
    "OAuthToken",
    "ProviderMetadata",
    "SchedulerJob",
    "SoftDeleteMixin",
    "Sport",
    "Subscription",
    "SyncHistory",
    "SyncOperation",
    "Team",
    "TimestampMixin",
    "UUIDMixin",
    # Models
    "User",
    "UserPreferences",
    "team_competition",
]
