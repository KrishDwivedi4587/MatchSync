"""Domain enumerations.

Stage 1 placed value objects such as ``FixtureStatus`` in the domain layer, so
all persistence enums live here as the single source of truth. Models import
these; migrations mirror their string values.

Design note — why sports are NOT an enum:
    Stage 1's core promise is that adding a sport requires only a new provider
    and a new ``sports`` row — never a code/schema change. A ``SportType`` enum
    would break that promise, so sports are data (the ``sports`` table). We keep
    a stable ``SportCategory`` classification enum instead, which changes rarely
    and does not grow when a new sport is onboarded.

All members use explicit lowercase string values so the database stores stable,
human-readable tokens independent of Python identifier names.
"""

from __future__ import annotations

from enum import StrEnum


class UserStatus(StrEnum):
    """Lifecycle state of a MatchSync account."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class CalendarProvider(StrEnum):
    """External identity/calendar provider.

    Shared by ``google_accounts.provider`` and ``calendars.provider`` (same
    provider set). Enables Apple/Microsoft support later without schema change.
    """

    GOOGLE = "google"
    APPLE = "apple"
    MICROSOFT = "microsoft"


class CalendarAccessRole(StrEnum):
    """The caller's permission on a calendar (mirrors Google's accessRole).

    Not persisted as a DB enum — ``calendars.access_role`` is a plain string
    column (Stage 3, frozen). This enum is the in-code representation.
    """

    OWNER = "owner"
    WRITER = "writer"
    READER = "reader"
    FREE_BUSY_READER = "freeBusyReader"
    NONE = "none"


class SportCategory(StrEnum):
    """Stable classification of a sport (does not grow per new sport)."""

    TEAM = "team"
    INDIVIDUAL = "individual"
    ESPORTS = "esports"


class CompetitionType(StrEnum):
    """Kind of competition — a league, knockout, cup, or single season/event."""

    LEAGUE = "league"
    TOURNAMENT = "tournament"
    CUP = "cup"
    SEASON = "season"
    OTHER = "other"


class FixtureStatus(StrEnum):
    """State of a fixture as reported by its provider."""

    SCHEDULED = "scheduled"
    LIVE = "live"
    FINISHED = "finished"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    DELETED = "deleted"  # vanished from provider across the stability threshold


class FixtureChangeType(StrEnum):
    """Why a new fixture version row was written.

    ``ABANDONED`` is derived, not reported: no provider vocabulary has it and the
    frozen ``FixtureStatus`` enum has no such member. A fixture that was LIVE and
    becomes CANCELLED was abandoned mid-play.
    """

    CREATED = "created"
    UPDATED = "updated"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"
    RESTORED = "restored"  # reappeared after being marked missing/deleted
    DELETED = "deleted"  # absent from the provider across the stability threshold


class ImportStatus(StrEnum):
    """Outcome of one fixture-import run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"  # some competitions or records failed; the rest imported
    FAILED = "failed"


class NotificationChannel(StrEnum):
    """Notification delivery channels. Stage 10 stores *configuration only* —
    delivery is a future stage (Stage 1's expansion list)."""

    EMAIL = "email"
    PUSH = "push"
    DISCORD = "discord"
    SLACK = "slack"
    BROWSER = "browser"


class SubscriptionType(StrEnum):
    """Scope granularity of a subscription (Stage 1's polymorphic scope)."""

    SPORT = "sport"  # follow everything in a sport
    COMPETITION = "competition"  # follow one league/tournament
    TEAM = "team"  # follow one team/club


class SubscriptionStatus(StrEnum):
    """Whether a subscription is actively synced."""

    ACTIVE = "active"
    PAUSED = "paused"  # e.g. account needs re-auth
    DISABLED = "disabled"


class CalendarEventState(StrEnum):
    """State of the mapping between a fixture and a calendar event."""

    ACTIVE = "active"
    CANCELLED = "cancelled"
    DELETED = "deleted"


class SyncStatus(StrEnum):
    """Outcome of a sync run (also reused for scheduler last-run status)."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"  # some items failed, run continued
    FAILED = "failed"


class SyncTrigger(StrEnum):
    """What initiated a sync run."""

    SCHEDULED = "scheduled"
    MANUAL = "manual"
    INITIAL = "initial"  # first backfill after subscribing


class OperationType(StrEnum):
    """Per-fixture action produced by the reconcile engine."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    CANCEL = "cancel"
    SKIP = "skip"


class OperationStatus(StrEnum):
    """Outcome of a single sync operation."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProviderType(StrEnum):
    """Category of an external provider."""

    SPORTS = "sports"
    CALENDAR = "calendar"
    IDENTITY = "identity"


class ProviderStatus(StrEnum):
    """Health of an external provider (updated by health checks later)."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    DISABLED = "disabled"


class JobStatus(StrEnum):
    """State of a scheduler job registry entry."""

    ENABLED = "enabled"
    PAUSED = "paused"
    DISABLED = "disabled"


class LogLevel(StrEnum):
    """Severity of a durable application/audit log record."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
