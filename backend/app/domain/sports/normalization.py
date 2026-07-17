"""Normalization pipeline.

Every provider adapter funnels its raw payload through these functions, which is
what guarantees that Football, Valorant, and Basketball emit *byte-identical*
domain models. The future sync engine can therefore compare fixtures from any
provider with the same code path.

Each transformation and its rationale:

**Dates/times** — providers return RFC3339 with ``Z``, offsets, naive strings,
or epoch seconds. ``normalize_datetime`` produces a timezone-aware **UTC**
datetime for all of them. Naive input is *assumed UTC* rather than local time,
because assuming the server's timezone is the classic source of off-by-hours
fixture bugs. Everything downstream (identity keys, calendar events) is UTC.

**Names** — providers disagree on whitespace, unicode width, and dash
characters (a U+00A0 no-break space hiding in "Inter Milan", a zero-width
space splitting "Bayer 04").
``normalize_name`` applies NFKC folding, strips zero-width characters,
canonicalizes dashes, and collapses whitespace. It deliberately does **not**
strip club suffixes ("FC", "CF"): that is lossy and locale-specific, and two
distinct clubs can differ only by suffix.

**Slugs** — ``slugify`` produces a stable, ASCII, lowercase key for lookups and
cache keys. Derived from the normalized name so it is provider-independent.

**Statuses** — every provider has its own vocabulary ("IN_PLAY", "live",
"Ongoing"). ``normalize_status`` maps through a provider-supplied dict into the
shared ``FixtureStatus`` enum, defaulting to ``SCHEDULED`` for unknown values
rather than raising: an unrecognized status must never drop a fixture.

**Ids** — ``normalize_external_id`` coerces ints/ints-as-strings to a trimmed
string. Provider ids are kept *native* (Stage 3's frozen columns store them);
``qualified_id`` in the port adds a provider prefix when global uniqueness is
needed.

**Seasons** — "2025", "2025-2026", "2025/2026", or start/end dates all collapse
to a canonical label: ``"2026"`` for single-year seasons, ``"2025/26"`` for
cross-year ones. Without this, the same season appears under three names.

**Countries** — trimmed, empty-to-None. We do not force ISO codes: providers mix
names and codes, and forcing one direction loses information.

**Venues** — empty strings become ``None`` so "no venue" is unambiguous.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from app.domain.ports.sports_provider import Venue
from app.domain.value_objects.enums import FixtureStatus
from app.exceptions.sports import NormalizationError

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"))
# The "ambiguous" unicode dashes below ARE the data: this is the translation
# table that canonicalizes them. Replacing them with ASCII would delete the
# feature, hence the explicit lint exemption.
_DASHES = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-"})  # noqa: RUF001
_NON_SLUG = re.compile(r"[^a-z0-9]+")
_SEASON_RANGE = re.compile(r"^(\d{4})\s*[-/]\s*(\d{2,4})$")
_SEASON_SINGLE = re.compile(r"^(\d{4})$")


# --------------------------------------------------------------------------
# Dates & times
# --------------------------------------------------------------------------
def normalize_datetime(value: Any, *, field: str = "datetime") -> datetime:
    """Coerce a provider timestamp into a timezone-aware UTC datetime."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):  # epoch seconds
        return datetime.fromtimestamp(float(value), tz=UTC)
    elif isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
            else:
                raise NormalizationError(f"Unparseable {field}: {value!r}") from None
    else:
        raise NormalizationError(f"Missing {field}.")

    # Naive input is assumed UTC — never the server's local timezone.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def normalize_optional_datetime(value: Any) -> datetime | None:
    if value in (None, "", 0):
        return None
    try:
        return normalize_datetime(value)
    except NormalizationError:
        return None


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------
def normalize_name(value: Any, *, field: str = "name") -> str:
    """NFKC-fold, strip zero-width chars, canonicalize dashes, collapse spaces."""
    if not isinstance(value, str) or not value.strip():
        raise NormalizationError(f"Missing {field}.")
    text = unicodedata.normalize("NFKC", value).translate(_ZERO_WIDTH).translate(_DASHES)
    return " ".join(text.split())


def normalize_optional_name(value: Any) -> str | None:
    try:
        return normalize_name(value)
    except NormalizationError:
        return None


def slugify(value: str) -> str:
    """Stable ASCII lowercase key derived from a normalized name."""
    folded = unicodedata.normalize("NFKD", normalize_name(value))
    ascii_only = folded.encode("ascii", "ignore").decode()
    return _NON_SLUG.sub("-", ascii_only.lower()).strip("-")


def normalize_country(value: Any) -> str | None:
    name = normalize_optional_name(value)
    return name or None


# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------
def normalize_external_id(value: Any, *, field: str = "id") -> str:
    """Coerce a provider id (int or str) to a trimmed string."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise NormalizationError(f"Missing {field}.")
    return str(value).strip()


# --------------------------------------------------------------------------
# Statuses
# --------------------------------------------------------------------------
def normalize_status(
    raw: Any, mapping: dict[str, FixtureStatus], *, default: FixtureStatus = FixtureStatus.SCHEDULED
) -> FixtureStatus:
    """Map a provider status string into the shared enum.

    Unknown values fall back to ``default`` — an unrecognized status must never
    cause a fixture to be dropped or a refresh to fail.
    """
    if not isinstance(raw, str):
        return default
    return mapping.get(raw.strip().upper(), mapping.get(raw.strip().lower(), default))


# --------------------------------------------------------------------------
# Seasons
# --------------------------------------------------------------------------
def normalize_season_label(value: Any) -> str | None:
    """Canonicalize a season label: "2025/26" (cross-year) or "2026" (single)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    if match := _SEASON_SINGLE.match(text):
        return match.group(1)

    if match := _SEASON_RANGE.match(text):
        start, end = match.group(1), match.group(2)
        end_short = end[-2:]
        return f"{start}/{end_short}"

    return normalize_optional_name(text)


def season_label_from_dates(start: Any, end: Any) -> str | None:
    """Derive a season label from its start/end dates."""
    start_dt = normalize_optional_datetime(start)
    end_dt = normalize_optional_datetime(end)
    if start_dt is None:
        return None
    if end_dt is None or end_dt.year == start_dt.year:
        return str(start_dt.year)
    return f"{start_dt.year}/{str(end_dt.year)[-2:]}"


# --------------------------------------------------------------------------
# Venues
# --------------------------------------------------------------------------
def normalize_venue(
    name: Any, *, city: Any = None, country: Any = None, external_id: Any = None
) -> Venue | None:
    """Build a Venue, or None when the provider supplies nothing usable."""
    venue_name = normalize_optional_name(name)
    if not venue_name:
        return None
    return Venue(
        name=venue_name,
        city=normalize_optional_name(city),
        country=normalize_country(country),
        external_id=str(external_id).strip() if external_id is not None else None,
    )


# --------------------------------------------------------------------------
# Safe field access (schema-change tolerance)
# --------------------------------------------------------------------------
def require(payload: dict, key: str, *, context: str = "record") -> Any:
    """Fetch a required field, raising NormalizationError if absent.

    Callers catch this per-item so one malformed record never fails a batch.
    """
    if not isinstance(payload, dict) or key not in payload or payload[key] is None:
        raise NormalizationError(f"Missing required field '{key}' in {context}.")
    return payload[key]


def optional(payload: dict, *keys: str) -> Any:
    """Fetch the first present key from a chain of aliases (schema drift)."""
    if not isinstance(payload, dict):
        return None
    for key in keys:
        if payload.get(key) is not None:
            return payload[key]
    return None
