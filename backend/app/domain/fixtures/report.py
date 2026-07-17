"""Import report models (pure).

The pipeline never raises for a single bad record; it accumulates counters and
issues into these structures, which are then persisted to ``import_runs`` and
returned to the caller.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.fixtures.validation import Severity
from app.domain.value_objects.enums import ImportStatus


@dataclass
class ImportStats:
    """Mutable counters accumulated while a competition is processed."""

    fetched: int = 0
    invalid: int = 0
    duplicates: int = 0  # collapsed within the provider's own payload
    created: int = 0
    updated: int = 0
    unchanged: int = 0  # content hash identical -> no write
    skipped_out_of_window: int = 0
    skipped_stale: int = 0  # provider sent an older revision than we hold
    missing_marked: int = 0  # first absence, awaiting the stability threshold
    deleted: int = 0  # second consecutive absence -> soft-deleted
    failed: int = 0  # record could not be persisted

    def merge(self, other: ImportStats) -> None:
        for name in self.__dataclass_fields__:
            setattr(self, name, getattr(self, name) + getattr(other, name))

    def as_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class ImportIssue:
    code: str
    message: str
    severity: Severity = Severity.ERROR
    external_id: str | None = None
    competition_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "external_id": self.external_id,
            "competition_id": self.competition_id,
        }


@dataclass
class CompetitionResult:
    competition_id: str
    success: bool = True
    stats: ImportStats = field(default_factory=ImportStats)
    issues: list[ImportIssue] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "competition_id": self.competition_id,
            "success": self.success,
            "stats": self.stats.as_dict(),
            "issues": [i.as_dict() for i in self.issues],
        }


@dataclass
class ImportReport:
    """The full outcome of one import run."""

    id: uuid.UUID
    provider_key: str
    sport_key: str | None = None
    status: ImportStatus = ImportStatus.RUNNING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int = 0
    stats: ImportStats = field(default_factory=ImportStats)
    competitions: list[CompetitionResult] = field(default_factory=list)
    error_summary: str | None = None

    @property
    def errors(self) -> list[ImportIssue]:
        return [i for c in self.competitions for i in c.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[ImportIssue]:
        return [i for c in self.competitions for i in c.issues if i.severity is Severity.WARNING]

    def finalize(self) -> None:
        """Aggregate competition stats and derive the overall status."""
        self.stats = ImportStats()
        for competition in self.competitions:
            self.stats.merge(competition.stats)

        any_failed = any(not c.success for c in self.competitions) or self.stats.failed > 0
        all_failed = bool(self.competitions) and all(not c.success for c in self.competitions)

        if all_failed:
            self.status = ImportStatus.FAILED
        elif any_failed or self.stats.invalid > 0:
            self.status = ImportStatus.PARTIAL
        else:
            self.status = ImportStatus.SUCCESS

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_key": self.provider_key,
            "sport_key": self.sport_key,
            "status": self.status.value,
            "duration_ms": self.duration_ms,
            "stats": self.stats.as_dict(),
            "competitions": [c.as_dict() for c in self.competitions],
        }
