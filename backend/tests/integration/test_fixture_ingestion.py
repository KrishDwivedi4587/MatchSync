"""FixtureIngestionService pipeline tests against SQLite and a fake sports service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.fixture_ingestion_service import FixtureIngestionService
from app.core.config import get_settings
from app.domain.ports.sports_provider import (
    Fixture as FixtureDTO,
)
from app.domain.ports.sports_provider import (
    Participant,
    ParticipantSide,
    Venue,
)
from app.domain.value_objects.enums import (
    CompetitionType,
    FixtureChangeType,
    FixtureStatus,
    ImportStatus,
    SportCategory,
)
from app.domain.value_objects.time_window import TimeWindow
from app.exceptions.sports import ProviderUnavailableError
from app.persistence.models.catalog import Competition, Sport, Team
from app.persistence.models.fixture import Fixture
from app.persistence.models.ingestion import FixtureVersion, ImportRun
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

SPORT_KEY = "football"
COMP_ID = "PL"
PROVIDER = "football-api"
START = datetime(2026, 8, 1, 15, 0, tzinfo=UTC)


class FakeSportsService:
    """Stands in for Stage 6's SportsService.get_fixtures."""

    def __init__(self) -> None:
        self.fixtures: list[FixtureDTO] = []
        self.error: Exception | None = None
        self.calls = 0

    async def get_fixtures(self, sport_key, competition_id, window):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.fixtures)


class FakeProvider:
    key = PROVIDER
    supported_sports = (SPORT_KEY,)


class FakeRegistry:
    def get_for_sport(self, sport_key):
        return FakeProvider()

    def get(self, provider_key):
        return FakeProvider()


def make_dto(**overrides) -> FixtureDTO:
    defaults = {
        "external_id": "m1",
        "competition_id": COMP_ID,
        "sport_key": SPORT_KEY,
        "start": START,
        "status": FixtureStatus.SCHEDULED,
        "participants": (
            Participant("57", "Arsenal", ParticipantSide.HOME),
            Participant("61", "Chelsea", ParticipantSide.AWAY),
        ),
    }
    defaults.update(overrides)
    return FixtureDTO(**defaults)


@pytest_asyncio.fixture
async def ctx(db_session: AsyncSession):
    sport = Sport(
        key=SPORT_KEY, name="Football", category=SportCategory.TEAM, provider_key=PROVIDER
    )
    competition = Competition(
        sport=sport,
        provider_competition_id=COMP_ID,
        name="Premier League",
        type=CompetitionType.LEAGUE,
    )
    arsenal = Team(sport=sport, provider_team_id="57", name="Arsenal")
    chelsea = Team(sport=sport, provider_team_id="61", name="Chelsea")
    db_session.add_all([sport, competition, arsenal, chelsea])
    await db_session.commit()

    sports_service = FakeSportsService()
    service = FixtureIngestionService(
        db_session,
        sports_service,
        FakeRegistry(),
        SportRepository(db_session),
        CompetitionRepository(db_session),
        TeamRepository(db_session),
        FixtureRepository(db_session),
        FixtureVersionRepository(db_session),
        ImportRunRepository(db_session),
        get_settings(),
    )
    return service, sports_service, db_session, competition, arsenal, chelsea


async def _count(db: AsyncSession, model) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


# --- happy path + idempotency ----------------------------------------------
async def test_import_persists_fixtures_and_creates_version_one(ctx) -> None:
    service, sports, db, _, arsenal, chelsea = ctx
    sports.fixtures = [make_dto(venue=Venue("Emirates"), round="1")]

    report = await service.import_sport(SPORT_KEY)

    assert report.status is ImportStatus.SUCCESS
    assert report.stats.fetched == 1 and report.stats.created == 1
    assert await _count(db, Fixture) == 1

    fixture = await db.scalar(select(Fixture))
    assert fixture.version == 1
    assert fixture.home_team_id == arsenal.id and fixture.away_team_id == chelsea.id
    assert fixture.venue == "Emirates"

    version = await db.scalar(select(FixtureVersion))
    assert version.version == 1
    assert version.change_type is FixtureChangeType.CREATED
    assert version.import_run_id == report.id  # linked to its run


async def test_reimporting_identical_data_creates_zero_duplicates(ctx) -> None:
    """The headline guarantee: importing twice changes nothing."""
    service, sports, db, _, _, _ = ctx
    sports.fixtures = [make_dto()]

    await service.import_sport(SPORT_KEY)
    second = await service.import_sport(SPORT_KEY)

    assert await _count(db, Fixture) == 1
    assert await _count(db, FixtureVersion) == 1  # no new version row
    assert second.stats.created == 0
    assert second.stats.unchanged == 1
    assert second.status is ImportStatus.SUCCESS


async def test_provider_repeating_a_fixture_in_one_payload_is_deduped(ctx) -> None:
    service, sports, db, _, _, _ = ctx
    sports.fixtures = [make_dto(), make_dto()]  # same provider id twice

    report = await service.import_sport(SPORT_KEY)

    assert await _count(db, Fixture) == 1
    assert report.stats.created == 1
    assert report.stats.duplicates == 1
    assert any(i.code == "duplicate_in_payload" for i in report.warnings)


# --- version detection ------------------------------------------------------
async def test_time_change_creates_a_new_version(ctx) -> None:
    service, sports, db, _, _, _ = ctx
    sports.fixtures = [make_dto()]
    await service.import_sport(SPORT_KEY)

    sports.fixtures = [make_dto(start=START + timedelta(hours=2))]
    report = await service.import_sport(SPORT_KEY)

    assert report.stats.updated == 1
    fixture = await db.scalar(select(Fixture))
    assert fixture.version == 2

    versions = (await db.scalars(select(FixtureVersion).order_by(FixtureVersion.version))).all()
    assert [v.version for v in versions] == [1, 2]
    assert versions[1].change_type is FixtureChangeType.UPDATED
    assert versions[1].changed_fields == ["scheduled_start"]


async def test_postponement_is_versioned(ctx) -> None:
    service, sports, db, _, _, _ = ctx
    sports.fixtures = [make_dto()]
    await service.import_sport(SPORT_KEY)

    sports.fixtures = [make_dto(status=FixtureStatus.POSTPONED)]
    await service.import_sport(SPORT_KEY)

    latest = await db.scalar(select(FixtureVersion).where(FixtureVersion.version == 2))
    assert latest.change_type is FixtureChangeType.POSTPONED
    fixture = await db.scalar(select(Fixture))
    assert fixture.status is FixtureStatus.POSTPONED


async def test_cancellation_is_versioned(ctx) -> None:
    service, sports, db, _, _, _ = ctx
    sports.fixtures = [make_dto()]
    await service.import_sport(SPORT_KEY)

    sports.fixtures = [make_dto(status=FixtureStatus.CANCELLED)]
    await service.import_sport(SPORT_KEY)

    latest = await db.scalar(select(FixtureVersion).where(FixtureVersion.version == 2))
    assert latest.change_type is FixtureChangeType.CANCELLED


async def test_abandonment_detected_when_live_becomes_cancelled(ctx) -> None:
    service, sports, db, _, _, _ = ctx
    sports.fixtures = [make_dto(status=FixtureStatus.LIVE)]
    await service.import_sport(SPORT_KEY)

    sports.fixtures = [make_dto(status=FixtureStatus.CANCELLED)]
    await service.import_sport(SPORT_KEY)

    latest = await db.scalar(select(FixtureVersion).where(FixtureVersion.version == 2))
    assert latest.change_type is FixtureChangeType.ABANDONED


async def test_venue_and_participant_changes_are_recorded(ctx) -> None:
    service, sports, db, _, _, _ = ctx
    sports.fixtures = [make_dto(venue=Venue("Emirates"))]
    await service.import_sport(SPORT_KEY)

    sports.fixtures = [make_dto(venue=Venue("Wembley"))]
    await service.import_sport(SPORT_KEY)

    latest = await db.scalar(select(FixtureVersion).where(FixtureVersion.version == 2))
    assert latest.changed_fields == ["venue"]
    assert latest.snapshot["venue"] == "Wembley"


# --- provider regressions and policies --------------------------------------
async def test_stale_provider_revision_is_skipped(ctx) -> None:
    service, sports, db, _, _, _ = ctx
    newer = datetime(2026, 7, 1, tzinfo=UTC)
    sports.fixtures = [make_dto(provider_updated_at=newer)]
    await service.import_sport(SPORT_KEY)

    # Provider regresses: same fixture, older revision, different time.
    sports.fixtures = [
        make_dto(start=START + timedelta(hours=5), provider_updated_at=newer - timedelta(days=1))
    ]
    report = await service.import_sport(SPORT_KEY)

    assert report.stats.skipped_stale == 1
    assert report.stats.updated == 0
    fixture = await db.scalar(select(Fixture))
    assert fixture.version == 1  # untouched


async def test_fixtures_outside_the_window_are_skipped(ctx) -> None:
    service, sports, db, _, _, _ = ctx
    now = datetime.now(UTC)
    sports.fixtures = [
        make_dto(external_id="old", start=now - timedelta(days=400)),
        make_dto(external_id="far", start=now + timedelta(days=400)),
    ]
    window = TimeWindow(now - timedelta(days=7), now + timedelta(days=30))

    report = await service.import_sport(SPORT_KEY, window=window)

    assert report.stats.skipped_out_of_window == 2
    assert await _count(db, Fixture) == 0


# --- absence handling --------------------------------------------------------
async def test_absence_requires_two_consecutive_runs_before_deletion(ctx) -> None:
    service, sports, db, _, _, _ = ctx
    sports.fixtures = [make_dto()]
    await service.import_sport(SPORT_KEY)

    # First absence: flagged, not deleted (a flaky read must not destroy data).
    sports.fixtures = []
    first = await service.import_sport(SPORT_KEY)
    assert first.stats.missing_marked == 1 and first.stats.deleted == 0
    fixture = await db.scalar(select(Fixture))
    assert fixture.deleted_at is None and fixture.missing_since is not None

    # Second consecutive absence: soft-deleted + versioned.
    second = await service.import_sport(SPORT_KEY)
    assert second.stats.deleted == 1
    await db.refresh(fixture)
    assert fixture.deleted_at is not None
    assert fixture.status is FixtureStatus.DELETED
    latest = await db.scalar(select(FixtureVersion).where(FixtureVersion.version == 2))
    assert latest.change_type is FixtureChangeType.DELETED


async def test_reappearing_fixture_is_restored_not_duplicated(ctx) -> None:
    service, sports, db, _, _, _ = ctx
    sports.fixtures = [make_dto()]
    await service.import_sport(SPORT_KEY)
    sports.fixtures = []
    await service.import_sport(SPORT_KEY)  # missing
    await service.import_sport(SPORT_KEY)  # deleted

    sports.fixtures = [make_dto()]
    report = await service.import_sport(SPORT_KEY)

    assert await _count(db, Fixture) == 1  # no duplicate despite UNIQUE identity_key
    assert report.stats.updated == 1
    fixture = await db.scalar(select(Fixture))
    assert fixture.deleted_at is None and fixture.missing_since is None
    latest = await db.scalar(select(FixtureVersion).where(FixtureVersion.version == 3))
    assert latest.change_type is FixtureChangeType.RESTORED


# --- partial failure isolation ----------------------------------------------
async def test_one_malformed_fixture_does_not_stop_the_others(ctx) -> None:
    """1 fails, 999 succeed (scaled down)."""
    service, sports, db, _, _, _ = ctx
    good = [make_dto(external_id=f"g{i}", start=START + timedelta(days=i)) for i in range(9)]
    bad = make_dto(external_id="bad", participants=())  # no participants -> rejected
    sports.fixtures = [*good, bad]

    report = await service.import_sport(SPORT_KEY)

    assert report.stats.created == 9
    assert report.stats.invalid == 1
    assert report.status is ImportStatus.PARTIAL
    assert await _count(db, Fixture) == 9
    assert any(i.code == "no_participants" for i in report.errors)


async def test_provider_failure_is_isolated_to_its_competition(ctx) -> None:
    service, sports, db, _, _, _ = ctx
    sports.error = ProviderUnavailableError()

    report = await service.import_sport(SPORT_KEY)

    assert report.status is ImportStatus.FAILED
    assert await _count(db, Fixture) == 0
    assert report.errors[0].code == "provider_unavailable"
    # The run is still recorded for audit.
    assert await _count(db, ImportRun) == 1


async def test_missing_sport_in_catalog_is_reported_not_raised(ctx) -> None:
    service, _, _db, _, _, _ = ctx

    class Registry(FakeRegistry):
        def get_for_sport(self, sport_key):
            return FakeProvider()

    service._registry = Registry()
    report = await service.import_sport("cricket")
    assert report.status is ImportStatus.FAILED
    assert report.errors[0].code == "sport_not_in_catalog"


# --- identity semantics --------------------------------------------------------
async def test_same_teams_same_day_is_one_match_by_definition(ctx) -> None:
    """Identity is (sport, competition, participants, day).

    Two records for the same teams in the same competition on the same day ARE
    the same match, however far apart their kickoff times or provider ids. This
    is what makes an intra-day reschedule free; it also means a provider that
    (wrongly) lists a fixture twice with different ids is collapsed.
    """
    service, sports, db, _, _, _ = ctx
    sports.fixtures = [
        make_dto(external_id="a", start=START),
        make_dto(external_id="b", start=START + timedelta(hours=3)),
    ]

    report = await service.import_sport(SPORT_KEY)

    assert await _count(db, Fixture) == 1
    assert report.stats.created == 1 and report.stats.duplicates == 1
    assert report.warnings[0].code == "duplicate_in_payload"

    # A different day is a different match.
    sports.fixtures = [make_dto(external_id="c", start=START + timedelta(days=2))]
    await service.import_sport(SPORT_KEY)
    assert await _count(db, Fixture) == 2


# --- bulk / scale -------------------------------------------------------------
def _distinct_matches(count: int) -> list[FixtureDTO]:
    """Realistic payload: every fixture is a distinct pairing."""
    return [
        make_dto(
            external_id=f"m{i}",
            start=START + timedelta(hours=i),
            participants=(
                Participant(f"h{i}", f"Home {i}", ParticipantSide.HOME),
                Participant(f"a{i}", f"Away {i}", ParticipantSide.AWAY),
            ),
        )
        for i in range(count)
    ]


async def test_bulk_import_of_many_fixtures(ctx) -> None:
    service, sports, db, _, _, _ = ctx
    sports.fixtures = _distinct_matches(250)  # > batch size (500? no: exercises chunking)

    report = await service.import_sport(SPORT_KEY)

    assert report.stats.created == 250
    assert report.stats.duplicates == 0
    assert await _count(db, Fixture) == 250
    assert await _count(db, FixtureVersion) == 250

    # Re-import: still no duplicates, no new versions. Idempotent at scale.
    again = await service.import_sport(SPORT_KEY)
    assert again.stats.unchanged == 250
    assert again.stats.created == 0
    assert await _count(db, Fixture) == 250
    assert await _count(db, FixtureVersion) == 250


async def test_bulk_import_chunks_beyond_batch_size(ctx) -> None:
    """More rows than fixture_import_batch_size exercises the chunking path."""
    service, sports, db, _, _, _ = ctx
    settings = get_settings()
    count = settings.fixture_import_batch_size + 37
    sports.fixtures = _distinct_matches(count)

    report = await service.import_sport(SPORT_KEY)
    assert report.stats.created == count
    assert await _count(db, Fixture) == count


# --- concurrency ---------------------------------------------------------------
async def test_concurrent_insert_conflict_is_absorbed_not_duplicated(ctx, monkeypatch) -> None:
    """Two imports race: both read an empty index, both try to INSERT.

    The UNIQUE identity_key rejects the loser, whose row is absorbed as a
    duplicate. This is the database-level guarantee behind "never create
    duplicates" — application logic alone cannot win a race.
    """
    service, sports, db, _, _, _ = ctx
    sports.fixtures = [make_dto()]
    await service.import_sport(SPORT_KEY)  # the winner committed

    async def stale_read(*_args, **_kwargs):
        return []  # our transaction never saw the winner's row

    monkeypatch.setattr(service._fixtures, "list_for_matching", stale_read)
    report = await service.import_sport(SPORT_KEY)

    assert await _count(db, Fixture) == 1
    assert await _count(db, FixtureVersion) == 1  # the loser's version row was dropped
    assert report.stats.created == 0
    assert report.stats.duplicates == 1
    assert any(i.code == "concurrent_insert_conflict" for i in report.warnings)


async def test_failed_insert_does_not_roll_back_the_successful_ones(ctx, monkeypatch) -> None:
    """Per-row savepoint fallback: good rows commit, the conflicting one doesn't."""
    service, sports, db, _, _, _ = ctx
    sports.fixtures = [make_dto(external_id="dup")]
    await service.import_sport(SPORT_KEY)

    async def stale_read(*_args, **_kwargs):
        return []

    monkeypatch.setattr(service._fixtures, "list_for_matching", stale_read)
    # "dup" collides; the other two are new.
    sports.fixtures = [
        make_dto(external_id="dup"),
        *_distinct_matches(2),
    ]
    report = await service.import_sport(SPORT_KEY)

    assert report.stats.created == 2
    assert report.stats.duplicates == 1
    assert await _count(db, Fixture) == 3  # 1 original + 2 new


# --- report persistence -------------------------------------------------------
async def test_import_run_row_records_the_report(ctx) -> None:
    service, sports, db, _, _, _ = ctx
    sports.fixtures = [make_dto()]
    report = await service.import_sport(SPORT_KEY)

    run = await db.scalar(select(ImportRun).where(ImportRun.id == report.id))
    assert run.status is ImportStatus.SUCCESS
    assert run.provider_key == PROVIDER
    assert run.created_count == 1
    assert run.finished_at is not None and run.duration_ms >= 0
    assert run.report["stats"]["created"] == 1


async def test_unresolved_team_is_stored_as_null_not_rejected(ctx) -> None:
    service, sports, db, _, _, _ = ctx
    sports.fixtures = [
        make_dto(
            participants=(
                Participant("57", "Arsenal", ParticipantSide.HOME),
                Participant("999", "Unknown FC", ParticipantSide.AWAY),
            )
        )
    ]
    report = await service.import_sport(SPORT_KEY)

    assert report.stats.created == 1
    fixture = await db.scalar(select(Fixture))
    assert fixture.home_team_id is not None
    assert fixture.away_team_id is None  # team not in catalog yet


async def test_neutral_side_participants_are_slotted_deterministically(ctx) -> None:
    service, sports, db, _, _, _ = ctx
    sports.fixtures = [
        make_dto(
            participants=(
                Participant("61", "Chelsea", ParticipantSide.NEUTRAL),
                Participant("57", "Arsenal", ParticipantSide.NEUTRAL),
            )
        )
    ]
    await service.import_sport(SPORT_KEY)
    first = await db.scalar(select(Fixture))
    home_first, away_first = first.home_team_id, first.away_team_id

    # Re-import with the participants in the opposite order: same slots.
    sports.fixtures = [
        make_dto(
            participants=(
                Participant("57", "Arsenal", ParticipantSide.NEUTRAL),
                Participant("61", "Chelsea", ParticipantSide.NEUTRAL),
            )
        )
    ]
    report = await service.import_sport(SPORT_KEY)
    assert report.stats.unchanged == 1
    await db.refresh(first)
    assert (first.home_team_id, first.away_team_id) == (home_first, away_first)
