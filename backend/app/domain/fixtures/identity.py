"""Fixture identity strategy.

Three distinct identifiers, each doing a different job:

**1. Provider id** (``fixtures.provider_fixture_id``)
    The vendor's own id, unique within a competition. It is the *strongest*
    match signal — if the provider says "this is match 12345", it is. But it is
    not portable across providers and a vendor can (rarely) reissue ids.

**2. Identity key** (``fixtures.identity_key``, globally UNIQUE)
    A derived, provider-independent fingerprint of *which real-world match this
    is*:

        hash(sport, competition, sorted(participant ids), kickoff day)

    - **Sorted participants** so "A vs B" and "B vs A" are the same match.
    - **Day bucket, not exact time**, so a kickoff moved from 15:00 to 15:30
      does not mint a new fixture.
    - It intentionally excludes venue and status: those change, the match doesn't.

**3. Content hash** (``fixtures.content_hash``)
    A digest of the *mutable* fields. Identity says "same match"; the content
    hash says "did anything change" in O(1), which is what makes re-imports free.

**How identity survives provider updates.** The matcher tries the provider id
first, then the identity key, then a fuzzy participants+time window. So:

- Kickoff shifts within the day → same identity key → matched.
- Kickoff shifts *across midnight* → identity key changes, but the **provider id
  still matches**, so we update the existing row (and rewrite its identity key)
  rather than creating a duplicate.
- The provider reissues its id → the **identity key still matches**.

Both failure modes are covered because the two keys are independent.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.hashing import stable_hash
from app.domain.ports.sports_provider import Fixture


def participants_key(fixture: Fixture) -> str:
    """Order-independent participant fingerprint ("A vs B" == "B vs A")."""
    return "|".join(sorted(p.external_id for p in fixture.participants))


def day_bucket(moment: datetime) -> str:
    """UTC calendar day. Absorbs intra-day kickoff shifts."""
    return moment.astimezone(UTC).date().isoformat()


def compute_identity_key(fixture: Fixture) -> str:
    """Stable, provider-independent identity for a real-world match."""
    return stable_hash(
        fixture.sport_key,
        fixture.competition_id,
        participants_key(fixture),
        day_bucket(fixture.start),
    )


def compute_content_hash(fixture: Fixture) -> str:
    """Digest of the mutable fields. Equal hash => nothing to write."""
    return stable_hash(
        fixture.start.astimezone(UTC).isoformat(),
        fixture.end.astimezone(UTC).isoformat() if fixture.end else "",
        fixture.status.value,
        fixture.venue.name if fixture.venue else "",
        fixture.round or "",
        fixture.stage or "",
        participants_key(fixture),
    )
