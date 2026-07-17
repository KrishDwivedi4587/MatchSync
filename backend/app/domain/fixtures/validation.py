"""Fixture validation and normalization verification.

Two distinct passes, deliberately separate:

**verify_normalized()** — did Stage 6 do its job? Times timezone-aware UTC,
statuses the shared enum, ids plain strings. A failure here is *our* bug (a
provider adapter that skipped normalization), not bad upstream data, so it is
reported with a distinct code.

**validate_fixture()** — is this record semantically usable? Required fields,
sane participants, a plausible date. A failure here is bad upstream data.

Neither raises: both return issues so the pipeline can reject one record and
keep the other 999 (see the partial-failure design).

Window policy (past/future cutoffs) is deliberately NOT here — that is an import
*policy*, not a validity question, and lives in the ingestion service.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.domain.ports.sports_provider import Fixture
from app.domain.value_objects.enums import FixtureStatus

# A fixture more than this far from now is a provider regression, not a fixture.
MAX_PAST = timedelta(days=365 * 5)
MAX_FUTURE = timedelta(days=365 * 5)


class Severity(StrEnum):
    ERROR = "error"  # record is rejected
    WARNING = "warning"  # record is imported, but flagged


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: Severity = Severity.ERROR
    external_id: str | None = None


def _err(code: str, message: str, external_id: str | None = None) -> ValidationIssue:
    return ValidationIssue(code, message, Severity.ERROR, external_id)


def _warn(code: str, message: str, external_id: str | None = None) -> ValidationIssue:
    return ValidationIssue(code, message, Severity.WARNING, external_id)


def verify_normalized(fixture: Fixture) -> list[ValidationIssue]:
    """Assert the Stage 6 normalization contract holds. Failures are our bug."""
    issues: list[ValidationIssue] = []
    ext = getattr(fixture, "external_id", None)

    if not isinstance(fixture.start, datetime):
        issues.append(_err("not_normalized_start", "start is not a datetime.", ext))
    elif fixture.start.tzinfo is None:
        issues.append(_err("naive_datetime", "start is not timezone-aware.", ext))
    elif fixture.start.utcoffset() != timedelta(0):
        issues.append(_err("not_utc", "start is not UTC.", ext))

    if not isinstance(fixture.status, FixtureStatus):
        issues.append(_err("not_normalized_status", "status is not a FixtureStatus.", ext))

    if not isinstance(fixture.external_id, str):
        issues.append(_err("not_normalized_id", "external_id is not a string.", ext))

    return issues


def validate_fixture(fixture: Fixture, *, now: datetime | None = None) -> list[ValidationIssue]:
    """Semantic validation. Returns issues; ERROR severity rejects the record."""
    issues: list[ValidationIssue] = []
    now = now or datetime.now(UTC)
    ext = fixture.external_id if isinstance(fixture.external_id, str) else None

    # --- required identifiers ---
    if not (isinstance(fixture.external_id, str) and fixture.external_id.strip()):
        issues.append(_err("missing_external_id", "Fixture has no provider id."))
    if not (isinstance(fixture.competition_id, str) and fixture.competition_id.strip()):
        issues.append(_err("missing_competition", "Fixture has no competition.", ext))
    if not (isinstance(fixture.sport_key, str) and fixture.sport_key.strip()):
        issues.append(_err("missing_sport", "Fixture has no sport.", ext))

    # --- participants ---
    participants = fixture.participants or ()
    if not participants:
        issues.append(_err("no_participants", "Fixture has no participants.", ext))
    else:
        ids = [p.external_id for p in participants]
        if any(not (isinstance(i, str) and i.strip()) for i in ids):
            issues.append(_err("blank_participant_id", "A participant has no id.", ext))
        elif len(set(ids)) != len(ids):
            issues.append(_err("duplicate_participants", "A participant appears twice.", ext))
        if any(not (p.name or "").strip() for p in participants):
            issues.append(_err("blank_participant_name", "A participant has no name.", ext))
        if len(participants) == 1:
            # Legal for future individual-sport events, but suspicious today.
            issues.append(_warn("single_participant", "Fixture has one participant.", ext))

    # --- dates ---
    if isinstance(fixture.start, datetime) and fixture.start.tzinfo is not None:
        if fixture.start < now - MAX_PAST or fixture.start > now + MAX_FUTURE:
            issues.append(
                _err("implausible_date", f"start {fixture.start.isoformat()} is out of range.", ext)
            )
        if fixture.end is not None:
            if fixture.end.tzinfo is None:
                issues.append(_err("naive_end", "end is not timezone-aware.", ext))
            elif fixture.end < fixture.start:
                issues.append(_err("end_before_start", "end precedes start.", ext))
    else:
        issues.append(_err("missing_start", "Fixture has no usable start time.", ext))

    return issues


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(i.severity is Severity.ERROR for i in issues)
