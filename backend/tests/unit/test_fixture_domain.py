"""Unit tests for the fixture ingestion domain: identity, validation, dedup, versions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.fixtures.deduplication import (
    CandidateFixture,
    ExistingFixtureRef,
    FixtureMatcher,
    dedupe_batch,
)
from app.domain.fixtures.identity import compute_content_hash, compute_identity_key
from app.domain.fixtures.report import CompetitionResult, ImportReport, ImportStats
from app.domain.fixtures.validation import (
    Severity,
    has_errors,
    validate_fixture,
    verify_normalized,
)
from app.domain.fixtures.versioning import (
    FixtureField,
    FixtureState,
    classify_change,
    diff_states,
)
from app.domain.ports.sports_provider import (
    Fixture,
    Participant,
    ParticipantSide,
    Venue,
)
from app.domain.value_objects.enums import (
    FixtureChangeType,
    FixtureStatus,
    ImportStatus,
)

START = datetime(2026, 8, 1, 15, 0, tzinfo=UTC)


def make_fixture(**overrides) -> Fixture:
    defaults = {
        "external_id": "m1",
        "competition_id": "PL",
        "sport_key": "football",
        "start": START,
        "status": FixtureStatus.SCHEDULED,
        "participants": (
            Participant("57", "Arsenal", ParticipantSide.HOME),
            Participant("61", "Chelsea", ParticipantSide.AWAY),
        ),
    }
    defaults.update(overrides)
    return Fixture(**defaults)


# --- identity --------------------------------------------------------------
def test_identity_key_is_order_independent() -> None:
    a = make_fixture()
    b = make_fixture(
        participants=(
            Participant("61", "Chelsea", ParticipantSide.HOME),
            Participant("57", "Arsenal", ParticipantSide.AWAY),
        )
    )
    assert compute_identity_key(a) == compute_identity_key(b)


def test_identity_key_survives_intraday_kickoff_shift() -> None:
    original = make_fixture()
    shifted = make_fixture(start=START + timedelta(minutes=30))
    assert compute_identity_key(original) == compute_identity_key(shifted)


def test_identity_key_changes_across_midnight() -> None:
    """Documented limitation: the provider id rung covers this case."""
    original = make_fixture()
    next_day = make_fixture(start=START + timedelta(days=1))
    assert compute_identity_key(original) != compute_identity_key(next_day)


def test_identity_key_differs_per_competition_and_sport() -> None:
    base = compute_identity_key(make_fixture())
    assert compute_identity_key(make_fixture(competition_id="CL")) != base
    assert compute_identity_key(make_fixture(sport_key="basketball")) != base


def test_content_hash_tracks_mutable_fields_only() -> None:
    base = compute_content_hash(make_fixture())
    assert compute_content_hash(make_fixture()) == base
    assert compute_content_hash(make_fixture(start=START + timedelta(minutes=30))) != base
    assert compute_content_hash(make_fixture(status=FixtureStatus.POSTPONED)) != base
    assert compute_content_hash(make_fixture(venue=Venue("Emirates"))) != base
    # external_id is identity, not content -> hash unchanged.
    assert compute_content_hash(make_fixture(external_id="different")) == base


# --- validation ------------------------------------------------------------
def test_valid_fixture_has_no_errors() -> None:
    assert not has_errors(validate_fixture(make_fixture()))
    assert verify_normalized(make_fixture()) == []


def test_naive_datetime_fails_normalization_verification() -> None:
    fixture = make_fixture(start=datetime(2026, 8, 1, 15, 0))
    codes = [i.code for i in verify_normalized(fixture)]
    assert "naive_datetime" in codes


def test_missing_participants_is_rejected() -> None:
    issues = validate_fixture(make_fixture(participants=()))
    assert has_errors(issues)
    assert "no_participants" in [i.code for i in issues]


def test_duplicate_participants_rejected() -> None:
    issues = validate_fixture(
        make_fixture(
            participants=(
                Participant("57", "Arsenal", ParticipantSide.HOME),
                Participant("57", "Arsenal", ParticipantSide.AWAY),
            )
        )
    )
    assert "duplicate_participants" in [i.code for i in issues]


def test_end_before_start_rejected() -> None:
    issues = validate_fixture(make_fixture(end=START - timedelta(hours=1)))
    assert "end_before_start" in [i.code for i in issues]


def test_implausible_date_rejected_as_provider_regression() -> None:
    issues = validate_fixture(make_fixture(start=datetime(1800, 1, 1, tzinfo=UTC)))
    assert "implausible_date" in [i.code for i in issues]


def test_single_participant_warns_but_does_not_reject() -> None:
    issues = validate_fixture(
        make_fixture(participants=(Participant("57", "Arsenal", ParticipantSide.NEUTRAL),))
    )
    assert not has_errors(issues)
    assert any(i.code == "single_participant" and i.severity is Severity.WARNING for i in issues)


# --- deduplication ---------------------------------------------------------
TEAM_A, TEAM_B = uuid.uuid4(), uuid.uuid4()


def candidate(external_id="m1", identity="ident-1", start=START, participants=(TEAM_A, TEAM_B)):
    return CandidateFixture(external_id, identity, start, frozenset(participants))


def existing(
    fid=None, provider_id="m1", identity="ident-1", start=START, participants=(TEAM_A, TEAM_B)
):
    return ExistingFixtureRef(
        fid or uuid.uuid4(), provider_id, identity, start, frozenset(participants)
    )


def test_matcher_prefers_provider_id() -> None:
    matcher = FixtureMatcher()
    ref = existing(provider_id="m1", identity="other")
    index = matcher.build_index([ref])
    assert matcher.match(candidate(identity="ident-1"), index) is ref


def test_matcher_falls_back_to_identity_key_when_provider_id_reissued() -> None:
    matcher = FixtureMatcher()
    ref = existing(provider_id="OLD-999", identity="ident-1")
    index = matcher.build_index([ref])
    assert matcher.match(candidate(external_id="NEW-1", identity="ident-1"), index) is ref


def test_matcher_fuzzy_rung_catches_cross_midnight_reschedule() -> None:
    """Provider reissued its id AND moved the match across midnight."""
    matcher = FixtureMatcher(timedelta(hours=12))
    ref = existing(provider_id="OLD", identity="old-identity", start=START)
    index = matcher.build_index([ref])
    moved = candidate(external_id="NEW", identity="new-identity", start=START + timedelta(hours=10))
    assert matcher.match(moved, index) is ref


def test_matcher_will_not_merge_different_participants() -> None:
    matcher = FixtureMatcher(timedelta(hours=12))
    index = matcher.build_index([existing(provider_id="OLD", identity="x")])
    other = candidate(external_id="NEW", identity="y", participants=(uuid.uuid4(), uuid.uuid4()))
    assert matcher.match(other, index) is None


def test_matcher_respects_tolerance_window() -> None:
    matcher = FixtureMatcher(timedelta(hours=2))
    index = matcher.build_index([existing(provider_id="OLD", identity="x")])
    far = candidate(external_id="NEW", identity="y", start=START + timedelta(hours=5))
    assert matcher.match(far, index) is None


def test_dedupe_batch_collapses_provider_repeats() -> None:
    items = [
        ("m1", candidate("m1")),
        ("m1-again", candidate("m1-again", identity="ident-2")),  # same provider id? no
    ]
    # Same provider id: build explicitly.
    items = [("a", candidate("dup", identity="i1")), ("b", candidate("dup", identity="i2"))]
    kept, groups = dedupe_batch(items)
    assert len(kept) == 1
    assert groups[0].dropped_external_ids == ("b",)
    assert groups[0].reason == "provider_id"


def test_dedupe_batch_collapses_identity_repeats() -> None:
    items = [("a", candidate("p1", identity="same")), ("b", candidate("p2", identity="same"))]
    kept, groups = dedupe_batch(items)
    assert len(kept) == 1 and groups[0].reason == "identity_key"


def test_dedupe_batch_keeps_distinct_fixtures() -> None:
    items = [
        ("a", candidate("p1", identity="i1")),
        ("b", candidate("p2", identity="i2", participants=(uuid.uuid4(), uuid.uuid4()))),
    ]
    kept, groups = dedupe_batch(items)
    assert len(kept) == 2 and groups == []


# --- versioning ------------------------------------------------------------
COMP = uuid.uuid4()


def state(**overrides) -> FixtureState:
    defaults = {
        "competition_id": COMP,
        "scheduled_start": START,
        "status": FixtureStatus.SCHEDULED,
        "venue": "Emirates",
        "home_team_id": TEAM_A,
        "away_team_id": TEAM_B,
    }
    defaults.update(overrides)
    return FixtureState(**defaults)


def test_diff_detects_each_versionable_field() -> None:
    old = state()
    assert diff_states(old, state()) == set()
    assert diff_states(old, state(scheduled_start=START + timedelta(hours=1))) == {
        FixtureField.SCHEDULED_START
    }
    assert diff_states(old, state(venue="Wembley")) == {FixtureField.VENUE}
    assert diff_states(old, state(status=FixtureStatus.POSTPONED)) == {FixtureField.STATUS}
    assert diff_states(old, state(competition_id=uuid.uuid4())) == {FixtureField.COMPETITION}
    assert diff_states(old, state(away_team_id=uuid.uuid4())) == {FixtureField.PARTICIPANTS}


def test_diff_handles_naive_datetimes_from_sqlite() -> None:
    naive = state(scheduled_start=datetime(2026, 8, 1, 15, 0))
    assert diff_states(naive, state()) == set()


@pytest.mark.parametrize(
    ("old_status", "new_status", "expected"),
    [
        (FixtureStatus.LIVE, FixtureStatus.CANCELLED, FixtureChangeType.ABANDONED),
        (FixtureStatus.SCHEDULED, FixtureStatus.CANCELLED, FixtureChangeType.CANCELLED),
        (FixtureStatus.SCHEDULED, FixtureStatus.POSTPONED, FixtureChangeType.POSTPONED),
        (FixtureStatus.CANCELLED, FixtureStatus.SCHEDULED, FixtureChangeType.RESTORED),
        (FixtureStatus.POSTPONED, FixtureStatus.LIVE, FixtureChangeType.RESTORED),
        (FixtureStatus.SCHEDULED, FixtureStatus.FINISHED, FixtureChangeType.UPDATED),
    ],
)
def test_classify_change(old_status, new_status, expected) -> None:
    assert classify_change(old_status, new_status, {FixtureField.STATUS}) is expected


def test_soft_deleted_fixture_reappearing_is_restored() -> None:
    result = classify_change(
        FixtureStatus.DELETED, FixtureStatus.SCHEDULED, set(), was_deleted=True
    )
    assert result is FixtureChangeType.RESTORED


def test_snapshot_is_json_safe() -> None:
    snapshot = state().to_snapshot()
    assert snapshot["status"] == "scheduled"
    assert isinstance(snapshot["competition_id"], str)
    assert snapshot["scheduled_start"].endswith("+00:00")


# --- report ----------------------------------------------------------------
def test_report_finalize_aggregates_and_derives_status() -> None:
    report = ImportReport(id=uuid.uuid4(), provider_key="p")
    ok = CompetitionResult("A", stats=ImportStats(fetched=10, created=10))
    report.competitions.append(ok)
    report.finalize()
    assert report.status is ImportStatus.SUCCESS
    assert report.stats.created == 10


def test_report_status_is_partial_when_a_competition_fails() -> None:
    report = ImportReport(id=uuid.uuid4(), provider_key="p")
    report.competitions.append(CompetitionResult("A", stats=ImportStats(created=5)))
    report.competitions.append(CompetitionResult("B", success=False))
    report.finalize()
    assert report.status is ImportStatus.PARTIAL


def test_report_status_is_failed_when_all_competitions_fail() -> None:
    report = ImportReport(id=uuid.uuid4(), provider_key="p")
    report.competitions.append(CompetitionResult("A", success=False))
    report.finalize()
    assert report.status is ImportStatus.FAILED


def test_report_partial_when_records_invalid() -> None:
    report = ImportReport(id=uuid.uuid4(), provider_key="p")
    report.competitions.append(
        CompetitionResult("A", stats=ImportStats(fetched=2, invalid=1, created=1))
    )
    report.finalize()
    assert report.status is ImportStatus.PARTIAL
