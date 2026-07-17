"""Fixture ingestion pipeline (ETL).

    Sports Platform -> Validate -> Verify normalization -> Dedupe -> Match ->
    Detect version -> Persist (bulk) -> Absence handling -> Import report

Guarantees:
- **Idempotent.** Re-importing identical data creates zero rows: the content hash
  short-circuits before any write.
- **No duplicates.** Three-rung matching plus a UNIQUE ``identity_key`` at the
  database level, with a per-row fallback if a concurrent import races us.
- **Partial failure isolated.** One bad record is rejected; the other 999 import.
  One failing competition does not abort the others, and each competition commits
  independently so work is never lost.
- **History preserved.** Every persisted change appends a ``fixture_versions``
  row. Fixtures are soft-deleted, never destroyed.

Explicitly NOT here: synchronization, calendars, scheduling, subscriptions.
This stage ends when normalized fixtures are in the database.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.fixtures.deduplication import (
    CandidateFixture,
    ExistingFixtureRef,
    FixtureMatcher,
    dedupe_batch,
)
from app.domain.fixtures.identity import compute_content_hash, compute_identity_key
from app.domain.fixtures.report import (
    CompetitionResult,
    ImportIssue,
    ImportReport,
    ImportStats,
)
from app.domain.fixtures.validation import (
    Severity,
    has_errors,
    validate_fixture,
    verify_normalized,
)
from app.domain.fixtures.versioning import (
    FixtureState,
    classify_change,
    diff_states,
)
from app.domain.ports.sports_provider import Fixture as FixtureDTO
from app.domain.value_objects.enums import (
    FixtureChangeType,
    FixtureStatus,
    ImportStatus,
)
from app.domain.value_objects.time_window import TimeWindow
from app.exceptions.base import AppError
from app.persistence.models.catalog import Competition, Sport
from app.persistence.models.fixture import Fixture as FixtureModel
from app.persistence.models.ingestion import ImportRun
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

logger = get_logger(__name__)


def _as_utc(moment: datetime | None) -> datetime | None:
    """Coerce a possibly-naive datetime (SQLite round-trips) to aware UTC."""
    if moment is None:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _chunks(rows: list[Any], size: int) -> Iterator[list[Any]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


class FixtureIngestionService:
    def __init__(
        self,
        session: AsyncSession,
        sports_service,
        registry,
        sports: SportRepository,
        competitions: CompetitionRepository,
        teams: TeamRepository,
        fixtures: FixtureRepository,
        versions: FixtureVersionRepository,
        runs: ImportRunRepository,
        settings: Settings,
    ) -> None:
        self._session = session
        self._sports_service = sports_service
        self._registry = registry
        self._sports = sports
        self._competitions = competitions
        self._teams = teams
        self._fixtures = fixtures
        self._versions = versions
        self._runs = runs
        self._settings = settings
        self._matcher = FixtureMatcher(timedelta(hours=settings.fixture_match_tolerance_hours))

    # --- public entry points -----------------------------------------------
    def default_window(self) -> TimeWindow:
        now = datetime.now(UTC)
        return TimeWindow(
            start=now - timedelta(days=self._settings.fixture_import_past_days),
            end=now + timedelta(days=self._settings.fixture_import_future_days),
        )

    async def import_sport(
        self,
        sport_key: str,
        *,
        window: TimeWindow | None = None,
        competition_ids: list[str] | None = None,
    ) -> ImportReport:
        provider = self._registry.get_for_sport(sport_key)
        return await self._run(
            provider_key=provider.key,
            sport_keys=[sport_key],
            competition_ids=competition_ids,
            window=window,
        )

    async def import_provider(
        self, provider_key: str, *, window: TimeWindow | None = None
    ) -> ImportReport:
        provider = self._registry.get(provider_key)
        return await self._run(
            provider_key=provider_key,
            sport_keys=list(provider.supported_sports),
            window=window,
        )

    # --- run orchestration --------------------------------------------------
    async def _run(
        self,
        *,
        provider_key: str,
        sport_keys: list[str],
        window: TimeWindow | None = None,
        competition_ids: list[str] | None = None,
    ) -> ImportReport:
        window = window or self.default_window()
        started = datetime.now(UTC)
        clock = time.perf_counter()

        run = await self._runs.add(
            ImportRun(
                provider_key=provider_key,
                sport_key=sport_keys[0] if len(sport_keys) == 1 else None,
                status=ImportStatus.RUNNING,
                started_at=started,
            )
        )
        await self._session.commit()

        report = ImportReport(
            id=run.id,
            provider_key=provider_key,
            sport_key=run.sport_key,
            started_at=started,
        )
        logger.info(
            "fixtures.import.started",
            run_id=str(run.id),
            provider=provider_key,
            sports=sport_keys,
        )

        for sport_key in sport_keys:
            sport = await self._sports.get_by_key(sport_key)
            if sport is None:
                report.competitions.append(
                    CompetitionResult(
                        competition_id="*",
                        success=False,
                        issues=[
                            ImportIssue(
                                code="sport_not_in_catalog",
                                message=(
                                    f"Sport '{sport_key}' is not in the catalog. "
                                    "Run a metadata refresh first."
                                ),
                            )
                        ],
                    )
                )
                continue

            competitions = await self._resolve_competitions(sport, competition_ids)
            for competition in competitions:
                result = await self._import_competition(sport, competition, window, run.id)
                report.competitions.append(result)

        report.finished_at = datetime.now(UTC)
        report.duration_ms = int((time.perf_counter() - clock) * 1000)
        report.finalize()
        await self._persist_report(run, report)

        logger.info(
            "fixtures.import.finished",
            run_id=str(run.id),
            provider=provider_key,
            status=report.status.value,
            duration_ms=report.duration_ms,
            **report.stats.as_dict(),
        )
        return report

    async def _resolve_competitions(
        self, sport: Sport, competition_ids: list[str] | None
    ) -> list[Competition]:
        """Competitions come from the persisted catalog, not a provider call.

        Stage 6's metadata refresh owns the catalog; ingestion reads it. This also
        guarantees we already hold the internal UUID for each competition.
        """
        all_competitions = list(await self._competitions.list_for_sport(sport.id))
        if not competition_ids:
            return all_competitions
        wanted = set(competition_ids)
        return [c for c in all_competitions if c.provider_competition_id in wanted]

    # --- per-competition pipeline -------------------------------------------
    async def _import_competition(
        self,
        sport: Sport,
        competition: Competition,
        window: TimeWindow,
        run_id: uuid.UUID,
    ) -> CompetitionResult:
        result = CompetitionResult(competition_id=competition.provider_competition_id)
        stats = result.stats
        now = datetime.now(UTC)

        # 1. Fetch (a provider failure isolates to this competition).
        try:
            raw = await self._sports_service.get_fixtures(
                sport.key, competition.provider_competition_id, window
            )
        except AppError as exc:
            result.success = False
            result.issues.append(
                ImportIssue(
                    code=exc.code,
                    message=exc.message,
                    competition_id=competition.provider_competition_id,
                )
            )
            logger.warning(
                "fixtures.import.fetch_failed",
                sport=sport.key,
                competition=competition.provider_competition_id,
                code=exc.code,
            )
            return result

        stats.fetched = len(raw)

        # 2 + 3. Validate and verify normalization.
        valid = self._validate(raw, now, result)

        # 4. Import policy: past/future windows.
        in_window = []
        for fixture in valid:
            if fixture.start < window.start or fixture.start >= window.end:
                stats.skipped_out_of_window += 1
                continue
            in_window.append(fixture)

        # 5. Resolve participant teams in one round-trip.
        team_map = await self._teams.map_provider_ids(
            sport.id,
            sorted({p.external_id for f in in_window for p in f.participants}),
        )

        # 6. Build candidates + intra-batch dedup.
        prepared: dict[str, dict[str, Any]] = {}
        items: list[tuple[str, CandidateFixture]] = []
        for fixture in in_window:
            home_id, away_id = self._resolve_sides(fixture, team_map, result)
            identity = compute_identity_key(fixture)
            content = compute_content_hash(fixture)
            participants = frozenset(i for i in (home_id, away_id) if i is not None)
            prepared[fixture.external_id] = {
                "dto": fixture,
                "identity": identity,
                "content": content,
                "home": home_id,
                "away": away_id,
            }
            items.append(
                (
                    fixture.external_id,
                    CandidateFixture(fixture.external_id, identity, fixture.start, participants),
                )
            )

        kept, duplicate_groups = dedupe_batch(
            items, tolerance=timedelta(hours=self._settings.fixture_match_tolerance_hours)
        )
        for group in duplicate_groups:
            stats.duplicates += len(group.dropped_external_ids)
            result.issues.append(
                ImportIssue(
                    code="duplicate_in_payload",
                    message=(
                        f"Provider returned {len(group.dropped_external_ids)} duplicate(s) "
                        f"of {group.kept_external_id} (matched on {group.reason})."
                    ),
                    severity=Severity.WARNING,
                    external_id=group.kept_external_id,
                    competition_id=competition.provider_competition_id,
                )
            )

        # 7. Match against stored fixtures + detect versions.
        existing = list(
            await self._fixtures.list_for_matching(competition.id, window.start, window.end)
        )
        index = self._matcher.build_index([self._to_ref(m) for m in existing])
        by_id = {m.id: m for m in existing}

        inserts: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        versions: list[dict[str, Any]] = []
        version_by_fixture: dict[uuid.UUID, dict[str, Any]] = {}
        matched: set[uuid.UUID] = set()

        for external_id, candidate in kept:
            data = prepared[external_id]
            fixture: FixtureDTO = data["dto"]
            new_state = self._state_from_dto(competition.id, fixture, data["home"], data["away"])

            match = self._matcher.match(candidate, index)
            if match is None:
                fixture_id = uuid.uuid4()
                inserts.append(self._insert_row(fixture_id, competition.id, fixture, data))
                version = self._version_row(
                    fixture_id,
                    1,
                    FixtureChangeType.CREATED,
                    [],
                    data["content"],
                    new_state,
                    fixture.provider_updated_at,
                    run_id,
                )
                versions.append(version)
                version_by_fixture[fixture_id] = version
                stats.created += 1
                continue

            model = by_id[match.id]
            matched.add(model.id)

            # Provider regression: an older revision than the one we hold.
            incoming_updated = _as_utc(fixture.provider_updated_at)
            stored_updated = _as_utc(model.provider_updated_at)
            if incoming_updated and stored_updated and incoming_updated < stored_updated:
                stats.skipped_stale += 1
                continue

            was_deleted = model.deleted_at is not None
            was_missing = model.missing_since is not None

            if data["content"] == model.content_hash and not was_deleted and not was_missing:
                stats.unchanged += 1
                continue

            old_state = self._state_from_model(model)
            changed = diff_states(old_state, new_state)
            if not changed and not was_deleted and not was_missing:
                stats.unchanged += 1
                continue

            change_type = classify_change(
                model.status, fixture.status, changed, was_deleted=was_deleted
            )
            new_version = model.version + 1
            updates.append(
                {
                    "id": model.id,
                    "identity_key": data["identity"],
                    "content_hash": data["content"],
                    "provider_fixture_id": fixture.external_id,
                    "scheduled_start": fixture.start,
                    "scheduled_end": fixture.end,
                    "status": fixture.status,
                    "venue": fixture.venue.name if fixture.venue else None,
                    "round": fixture.round,
                    "stage": fixture.stage,
                    "home_team_id": data["home"],
                    "away_team_id": data["away"],
                    "provider_updated_at": fixture.provider_updated_at,
                    "version": new_version,
                    "deleted_at": None,  # reappearing fixtures are restored
                    "missing_since": None,
                }
            )
            versions.append(
                self._version_row(
                    model.id,
                    new_version,
                    change_type,
                    sorted(f.value for f in changed),
                    data["content"],
                    new_state,
                    fixture.provider_updated_at,
                    run_id,
                )
            )
            stats.updated += 1

        # 8. Absence handling (stability threshold: two consecutive absences).
        self._handle_absences(existing, matched, now, updates, versions, stats, run_id)

        # 9. Persist. Each competition commits independently (partial imports).
        await self._persist(
            inserts,
            updates,
            versions,
            version_by_fixture,
            stats,
            result,
            competition.provider_competition_id,
        )

        logger.info(
            "fixtures.import.competition",
            sport=sport.key,
            competition=competition.provider_competition_id,
            **stats.as_dict(),
        )
        return result

    # --- pipeline helpers ----------------------------------------------------
    def _validate(
        self, raw: Sequence[FixtureDTO], now: datetime, result: CompetitionResult
    ) -> list[FixtureDTO]:
        valid: list[FixtureDTO] = []
        for fixture in raw:
            issues = verify_normalized(fixture) + validate_fixture(fixture, now=now)
            for issue in issues:
                result.issues.append(
                    ImportIssue(
                        code=issue.code,
                        message=issue.message,
                        severity=issue.severity,
                        external_id=issue.external_id,
                        competition_id=result.competition_id,
                    )
                )
            if has_errors(issues):
                result.stats.invalid += 1
                logger.warning(
                    "fixtures.import.invalid_record",
                    competition=result.competition_id,
                    codes=[i.code for i in issues if i.severity is Severity.ERROR],
                )
                continue
            valid.append(fixture)
        return valid

    def _resolve_sides(
        self,
        fixture: FixtureDTO,
        team_map: dict[str, uuid.UUID],
        result: CompetitionResult,
    ) -> tuple[uuid.UUID | None, uuid.UUID | None]:
        """Map participants onto the frozen home/away columns.

        For two-sided sports the sides are semantic. For neutral-side sports
        (esports, online events) the columns are storage *slots*: participants are
        assigned in sorted-id order so the assignment is deterministic across
        imports. Fixtures with more than two participants (a future F1 grid) store
        no team refs and are flagged — they need a participants table, not a hack.
        """
        home = fixture.home
        away = fixture.away
        if home is not None or away is not None:
            return (
                team_map.get(home.external_id) if home else None,
                team_map.get(away.external_id) if away else None,
            )

        participants = sorted(fixture.participants, key=lambda p: p.external_id)
        if len(participants) > 2:
            result.issues.append(
                ImportIssue(
                    code="participants_not_storable",
                    message=f"{len(participants)} participants; team refs omitted.",
                    severity=Severity.WARNING,
                    external_id=fixture.external_id,
                    competition_id=result.competition_id,
                )
            )
            return None, None

        slots = [team_map.get(p.external_id) for p in participants]
        slots += [None, None]
        return slots[0], slots[1]

    @staticmethod
    def _to_ref(model: FixtureModel) -> ExistingFixtureRef:
        return ExistingFixtureRef(
            id=model.id,
            provider_fixture_id=model.provider_fixture_id,
            identity_key=model.identity_key,
            scheduled_start=_as_utc(model.scheduled_start),  # type: ignore[arg-type]
            participant_ids=frozenset(
                i for i in (model.home_team_id, model.away_team_id) if i is not None
            ),
        )

    @staticmethod
    def _state_from_model(model: FixtureModel) -> FixtureState:
        return FixtureState(
            competition_id=model.competition_id,
            scheduled_start=_as_utc(model.scheduled_start),  # type: ignore[arg-type]
            scheduled_end=_as_utc(model.scheduled_end),
            status=model.status,
            venue=model.venue,
            round=model.round,
            stage=model.stage,
            home_team_id=model.home_team_id,
            away_team_id=model.away_team_id,
        )

    @staticmethod
    def _state_from_dto(
        competition_id: uuid.UUID,
        fixture: FixtureDTO,
        home: uuid.UUID | None,
        away: uuid.UUID | None,
    ) -> FixtureState:
        return FixtureState(
            competition_id=competition_id,
            scheduled_start=fixture.start,
            scheduled_end=fixture.end,
            status=fixture.status,
            venue=fixture.venue.name if fixture.venue else None,
            round=fixture.round,
            stage=fixture.stage,
            home_team_id=home,
            away_team_id=away,
        )

    @staticmethod
    def _insert_row(
        fixture_id: uuid.UUID,
        competition_id: uuid.UUID,
        fixture: FixtureDTO,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "id": fixture_id,
            "competition_id": competition_id,
            "provider_fixture_id": fixture.external_id,
            "identity_key": data["identity"],
            "content_hash": data["content"],
            "home_team_id": data["home"],
            "away_team_id": data["away"],
            "scheduled_start": fixture.start,
            "scheduled_end": fixture.end,
            "status": fixture.status,
            "round": fixture.round,
            "stage": fixture.stage,
            "venue": fixture.venue.name if fixture.venue else None,
            "provider_updated_at": fixture.provider_updated_at,
            "version": 1,
            "missing_since": None,
        }

    @staticmethod
    def _version_row(
        fixture_id: uuid.UUID,
        version: int,
        change_type: FixtureChangeType,
        changed_fields: list[str],
        content_hash: str,
        state: FixtureState,
        provider_updated_at: datetime | None,
        import_run_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        return {
            "id": uuid.uuid4(),
            "fixture_id": fixture_id,
            "version": version,
            "change_type": change_type,
            "changed_fields": changed_fields,
            "content_hash": content_hash,
            "snapshot": state.to_snapshot(),
            "provider_updated_at": provider_updated_at,
            "import_run_id": import_run_id,
        }

    def _handle_absences(
        self,
        existing: list[FixtureModel],
        matched: set[uuid.UUID],
        now: datetime,
        updates: list[dict[str, Any]],
        versions: list[dict[str, Any]],
        stats: ImportStats,
        run_id: uuid.UUID,
    ) -> None:
        """Two consecutive absences before deletion (Stage 1's stability rule)."""
        for model in existing:
            if model.id in matched or model.deleted_at is not None:
                continue

            if model.missing_since is None:
                # First absence: remember it, change nothing else.
                updates.append({"id": model.id, "missing_since": now})
                stats.missing_marked += 1
                continue

            # Second consecutive absence: soft-delete + version row.
            new_version = model.version + 1
            updates.append(
                {
                    "id": model.id,
                    "status": FixtureStatus.DELETED,
                    "deleted_at": now,
                    "version": new_version,
                }
            )
            state = self._state_from_model(model)
            versions.append(
                self._version_row(
                    model.id,
                    new_version,
                    FixtureChangeType.DELETED,
                    ["status"],
                    model.content_hash,
                    state,
                    model.provider_updated_at,
                    run_id,
                )
            )
            stats.deleted += 1

    # --- persistence ---------------------------------------------------------
    async def _persist(
        self,
        inserts: list[dict[str, Any]],
        updates: list[dict[str, Any]],
        versions: list[dict[str, Any]],
        version_by_fixture: dict[uuid.UUID, dict[str, Any]],
        stats: ImportStats,
        result: CompetitionResult,
        competition_id: str,
    ) -> None:
        size = self._settings.fixture_import_batch_size
        rejected: set[uuid.UUID] = set()

        for chunk in _chunks(inserts, size):
            rejected |= await self._insert_chunk(chunk, stats, result, competition_id)

        if rejected:
            # Drop the version rows for fixtures that lost an insert race.
            versions = [v for v in versions if v["fixture_id"] not in rejected]

        for chunk in _chunks(updates, size):
            await self._update_chunk(chunk, stats, result, competition_id)

        for chunk in _chunks(versions, size):
            await self._versions.bulk_insert(chunk)

        await self._session.commit()

    async def _insert_chunk(
        self,
        chunk: list[dict[str, Any]],
        stats: ImportStats,
        result: CompetitionResult,
        competition_id: str,
    ) -> set[uuid.UUID]:
        """Bulk insert; on a unique-key race, fall back to per-row inserts."""
        try:
            async with self._session.begin_nested():
                await self._fixtures.bulk_insert(chunk)
            return set()
        except IntegrityError:
            pass  # a concurrent import won; resolve row by row

        rejected: set[uuid.UUID] = set()
        for row in chunk:
            try:
                async with self._session.begin_nested():
                    await self._fixtures.bulk_insert([row])
            except IntegrityError:
                # The fixture already exists (identity_key is UNIQUE). This is the
                # concurrency guarantee: a duplicate can never be created.
                stats.created -= 1
                stats.duplicates += 1
                rejected.add(row["id"])
                result.issues.append(
                    ImportIssue(
                        code="concurrent_insert_conflict",
                        message="Fixture was inserted by a concurrent import.",
                        severity=Severity.WARNING,
                        external_id=row["provider_fixture_id"],
                        competition_id=competition_id,
                    )
                )
        return rejected

    async def _update_chunk(
        self,
        chunk: list[dict[str, Any]],
        stats: ImportStats,
        result: CompetitionResult,
        competition_id: str,
    ) -> None:
        try:
            async with self._session.begin_nested():
                await self._fixtures.bulk_update(chunk)
            return
        except IntegrityError:
            pass

        for row in chunk:
            try:
                async with self._session.begin_nested():
                    await self._fixtures.bulk_update([row])
            except IntegrityError:
                stats.updated = max(0, stats.updated - 1)
                stats.failed += 1
                result.success = False
                result.issues.append(
                    ImportIssue(
                        code="update_conflict",
                        message="Fixture update collided with an existing identity key.",
                        competition_id=competition_id,
                    )
                )

    async def _persist_report(self, run: ImportRun, report: ImportReport) -> None:
        stats = report.stats
        run.status = report.status
        run.finished_at = report.finished_at
        run.duration_ms = report.duration_ms
        run.fetched_count = stats.fetched
        run.created_count = stats.created
        run.updated_count = stats.updated
        run.unchanged_count = stats.unchanged
        run.skipped_count = stats.skipped_out_of_window + stats.skipped_stale
        run.duplicate_count = stats.duplicates
        run.invalid_count = stats.invalid
        run.failed_count = stats.failed
        run.deleted_count = stats.deleted
        run.report = report.as_dict()
        errors = report.errors
        run.error_summary = f"{len(errors)} error(s); first: {errors[0].code}" if errors else None
        await self._session.commit()
