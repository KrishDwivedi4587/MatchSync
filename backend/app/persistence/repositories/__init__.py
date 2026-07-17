"""Repository implementations. Translate ORM rows <-> domain entities.

One repository per aggregate root. Repositories hold data-access queries only —
no business logic.
"""

from app.persistence.repositories.base import BaseRepository
from app.persistence.repositories.catalog import (
    CompetitionRepository,
    SportRepository,
    TeamRepository,
)
from app.persistence.repositories.fixture import FixtureRepository
from app.persistence.repositories.ingestion import (
    FixtureVersionRepository,
    ImportRunRepository,
)
from app.persistence.repositories.subscription import (
    CalendarEventRepository,
    SubscriptionRepository,
)
from app.persistence.repositories.sync import SyncRepository
from app.persistence.repositories.system import (
    ApplicationLogRepository,
    ProviderMetadataRepository,
    SchedulerJobRepository,
)
from app.persistence.repositories.user import (
    CalendarRepository,
    GoogleAccountRepository,
    OAuthTokenRepository,
    UserRepository,
)

__all__ = [
    "ApplicationLogRepository",
    "BaseRepository",
    "CalendarEventRepository",
    "CalendarRepository",
    "CompetitionRepository",
    "FixtureRepository",
    "FixtureVersionRepository",
    "GoogleAccountRepository",
    "ImportRunRepository",
    "OAuthTokenRepository",
    "ProviderMetadataRepository",
    "SchedulerJobRepository",
    "SportRepository",
    "SubscriptionRepository",
    "SyncRepository",
    "TeamRepository",
    "UserRepository",
]
