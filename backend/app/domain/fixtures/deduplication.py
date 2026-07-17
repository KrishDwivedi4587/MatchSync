"""Fixture deduplication engine (pure, reusable).

Two jobs:

1. **Intra-batch dedup** — a provider sometimes returns the same match twice in
   one payload. ``dedupe_batch`` collapses those before we touch the database.

2. **Match against what we already stored** — ``FixtureMatcher.match`` decides
   whether an incoming fixture is a *new* row or an *update* to an existing one.

The matching ladder, strongest signal first (same shape as the calendar
platform's duplicate ladder, deliberately):

    1. Provider id      — the vendor's own id within this competition.
    2. Identity key     — provider-independent fingerprint (see identity.py).
    3. Participants + kickoff within a tolerance window — catches the case where
       a provider both reissued its id *and* moved the match across midnight.

Rung 3 is the only heuristic. It requires an exact participant-set match, so it
can never merge two different matches; the tolerance only forgives rescheduling.

Stage 8's synchronization engine reuses ``FixtureMatcher`` unchanged: it is pure,
takes plain refs, and knows nothing about ORM, providers, or calendars.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

DEFAULT_TOLERANCE = timedelta(hours=12)


@dataclass(frozen=True)
class ExistingFixtureRef:
    """A stored fixture, reduced to just what matching needs."""

    id: uuid.UUID
    provider_fixture_id: str
    identity_key: str
    scheduled_start: datetime
    participant_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)


@dataclass(frozen=True)
class CandidateFixture:
    """An incoming fixture, reduced to just what matching needs."""

    provider_fixture_id: str
    identity_key: str
    scheduled_start: datetime
    participant_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)


@dataclass(frozen=True)
class DuplicateGroup:
    """A set of incoming records that all describe the same match."""

    kept_external_id: str
    dropped_external_ids: tuple[str, ...]
    reason: str


class FixtureMatcher:
    """Matches candidates against already-stored fixtures within one competition."""

    def __init__(self, tolerance: timedelta = DEFAULT_TOLERANCE) -> None:
        self._tolerance = tolerance

    def build_index(self, existing: list[ExistingFixtureRef]) -> FixtureIndex:
        return FixtureIndex(existing)

    def match(self, candidate: CandidateFixture, index: FixtureIndex) -> ExistingFixtureRef | None:
        if hit := index.by_provider_id.get(candidate.provider_fixture_id):
            return hit
        if hit := index.by_identity_key.get(candidate.identity_key):
            return hit
        if candidate.participant_ids:
            for ref in index.by_participants.get(candidate.participant_ids, ()):
                if abs(ref.scheduled_start - candidate.scheduled_start) <= self._tolerance:
                    return ref
        return None


class FixtureIndex:
    """O(1) lookup structures over the stored fixtures of one competition."""

    def __init__(self, existing: list[ExistingFixtureRef]) -> None:
        self.by_provider_id: dict[str, ExistingFixtureRef] = {}
        self.by_identity_key: dict[str, ExistingFixtureRef] = {}
        self.by_participants: dict[frozenset[uuid.UUID], list[ExistingFixtureRef]] = defaultdict(
            list
        )

        for ref in existing:
            self.by_provider_id[ref.provider_fixture_id] = ref
            self.by_identity_key[ref.identity_key] = ref
            if ref.participant_ids:
                self.by_participants[ref.participant_ids].append(ref)


def dedupe_batch(
    items: list[tuple[str, CandidateFixture]],
    *,
    tolerance: timedelta = DEFAULT_TOLERANCE,
) -> tuple[list[tuple[str, CandidateFixture]], list[DuplicateGroup]]:
    """Collapse duplicates *within one provider payload*.

    ``items`` are ``(external_id, candidate)`` pairs. The first occurrence wins;
    later ones are reported. Uses the same ladder as ``FixtureMatcher``.
    """
    kept: list[tuple[str, CandidateFixture]] = []
    groups: dict[str, list[str]] = defaultdict(list)
    reasons: dict[str, str] = {}

    seen_provider: dict[str, str] = {}
    seen_identity: dict[str, str] = {}
    seen_participants: dict[frozenset[uuid.UUID], list[tuple[str, datetime]]] = defaultdict(list)

    for external_id, candidate in items:
        winner: str | None = None
        reason = ""

        if candidate.provider_fixture_id in seen_provider:
            winner, reason = seen_provider[candidate.provider_fixture_id], "provider_id"
        elif candidate.identity_key in seen_identity:
            winner, reason = seen_identity[candidate.identity_key], "identity_key"
        elif candidate.participant_ids:
            for prior_id, prior_start in seen_participants[candidate.participant_ids]:
                if abs(prior_start - candidate.scheduled_start) <= tolerance:
                    winner, reason = prior_id, "participants_time_window"
                    break

        if winner is not None:
            groups[winner].append(external_id)
            reasons[winner] = reason
            continue

        kept.append((external_id, candidate))
        seen_provider[candidate.provider_fixture_id] = external_id
        seen_identity[candidate.identity_key] = external_id
        if candidate.participant_ids:
            seen_participants[candidate.participant_ids].append(
                (external_id, candidate.scheduled_start)
            )

    duplicates = [
        DuplicateGroup(
            kept_external_id=kept_id,
            dropped_external_ids=tuple(dropped),
            reason=reasons[kept_id],
        )
        for kept_id, dropped in groups.items()
    ]
    return kept, duplicates
