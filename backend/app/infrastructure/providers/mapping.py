"""Per-item normalization with partial-failure tolerance.

An upstream schema change (a renamed field, a null where a string was promised)
must degrade gracefully: skip the bad record, log it, keep the good ones. A
provider that drops one team is far better than a refresh that fails entirely.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from app.core.logging import get_logger
from app.exceptions.sports import NormalizationError

logger = get_logger(__name__)

T = TypeVar("T")


def normalize_many(
    items: Iterable[Any],
    mapper: Callable[[Any], T],
    *,
    provider: str,
    kind: str,
) -> list[T]:
    """Map each raw item, skipping (and logging) those that fail normalization."""
    results: list[T] = []
    skipped = 0
    for raw in items or []:
        try:
            results.append(mapper(raw))
        except NormalizationError as exc:
            skipped += 1
            # Log the reason, never the payload (may contain PII or secrets).
            logger.warning(
                "sports.normalization.failed", provider=provider, kind=kind, reason=str(exc)
            )
    if skipped:
        logger.warning(
            "sports.normalization.partial",
            provider=provider,
            kind=kind,
            skipped=skipped,
            kept=len(results),
        )
    return results


def as_list(payload: Any, *keys: str) -> list[Any]:
    """Extract a list from a payload, tolerating envelope shape drift.

    Providers wrap collections differently ({"data": [...]}, {"teams": [...]},
    or a bare list). Trying each alias keeps the adapter resilient to renames.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []
