"""Unit tests for the normalization pipeline and model codecs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.ports.sports_provider import Competition, Season, Sport, Team
from app.domain.sports.codec import (
    competition_from_dict,
    competition_to_dict,
    sport_from_dict,
    sport_to_dict,
    team_from_dict,
    team_to_dict,
)
from app.domain.sports.normalization import (
    normalize_country,
    normalize_datetime,
    normalize_external_id,
    normalize_name,
    normalize_optional_datetime,
    normalize_season_label,
    normalize_status,
    normalize_venue,
    optional,
    require,
    season_label_from_dates,
    slugify,
)
from app.domain.value_objects.enums import CompetitionType, FixtureStatus, SportCategory
from app.domain.value_objects.time_window import TimeWindow
from app.exceptions.sports import NormalizationError


# --- datetimes -------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        "2026-08-01T15:00:00Z",
        "2026-08-01T15:00:00+00:00",
        "2026-08-01T17:00:00+02:00",
        datetime(2026, 8, 1, 15, 0, tzinfo=UTC),
    ],
)
def test_datetimes_normalize_to_the_same_utc_instant(raw) -> None:
    assert normalize_datetime(raw) == datetime(2026, 8, 1, 15, 0, tzinfo=UTC)


def test_naive_datetime_is_assumed_utc_not_local() -> None:
    result = normalize_datetime("2026-08-01 15:00:00")
    assert result == datetime(2026, 8, 1, 15, 0, tzinfo=UTC)
    assert result.tzinfo is UTC


def test_epoch_seconds_are_supported() -> None:
    assert normalize_datetime(0) == datetime(1970, 1, 1, tzinfo=UTC)


def test_unparseable_datetime_raises_normalization_error() -> None:
    with pytest.raises(NormalizationError):
        normalize_datetime("not a date")
    with pytest.raises(NormalizationError):
        normalize_datetime(None)


def test_optional_datetime_swallows_bad_values() -> None:
    assert normalize_optional_datetime(None) is None
    assert normalize_optional_datetime("garbage") is None
    assert normalize_optional_datetime("2026-01-01") is not None


# --- names -----------------------------------------------------------------
def test_names_are_folded_and_whitespace_collapsed() -> None:
    assert normalize_name("  Inter   Milan \n") == "Inter Milan"
    # Zero-width joiner and full-width chars normalize away.
    assert normalize_name("Bayer​ 04") == "Bayer 04"
    fullwidth = "Ｒｅａｌ"  # noqa: RUF001 - the width IS the test input
    assert normalize_name(fullwidth) == "Real"


def test_dashes_are_canonicalized() -> None:
    dashed = "Saint–Etienne"  # noqa: RUF001 - the EN DASH IS the test input
    assert normalize_name(dashed) == "Saint-Etienne"


def test_club_suffixes_are_preserved() -> None:
    # Stripping "FC" would collide distinct clubs; we deliberately keep it.
    assert normalize_name("Arsenal FC") == "Arsenal FC"


def test_missing_name_raises() -> None:
    with pytest.raises(NormalizationError):
        normalize_name("   ")


def test_slugify_is_ascii_and_stable() -> None:
    assert slugify("Bayern München") == "bayern-munchen"
    assert slugify("  FC   Köln ") == "fc-koln"


def test_normalize_country_empties_to_none() -> None:
    assert normalize_country("  ") is None
    assert normalize_country("England") == "England"


# --- ids -------------------------------------------------------------------
def test_external_ids_coerce_to_trimmed_strings() -> None:
    assert normalize_external_id(2021) == "2021"
    assert normalize_external_id("  PL ") == "PL"
    with pytest.raises(NormalizationError):
        normalize_external_id(None)


# --- statuses --------------------------------------------------------------
def test_status_mapping_is_case_insensitive() -> None:
    mapping = {"IN_PLAY": FixtureStatus.LIVE}
    assert normalize_status("in_play", mapping) is FixtureStatus.LIVE
    assert normalize_status("IN_PLAY", mapping) is FixtureStatus.LIVE


def test_unknown_status_falls_back_rather_than_dropping_the_fixture() -> None:
    assert normalize_status("BRAND_NEW_STATUS", {}) is FixtureStatus.SCHEDULED
    assert normalize_status(None, {}) is FixtureStatus.SCHEDULED


# --- seasons ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2026", "2026"), ("2025-2026", "2025/26"), ("2025/2026", "2025/26"), ("2025/26", "2025/26")],
)
def test_season_labels_canonicalize(raw, expected) -> None:
    assert normalize_season_label(raw) == expected


def test_season_label_from_dates() -> None:
    assert season_label_from_dates("2025-08-01", "2026-05-30") == "2025/26"
    assert season_label_from_dates("2026-03-01", "2026-11-30") == "2026"
    assert season_label_from_dates(None, None) is None


# --- venues ----------------------------------------------------------------
def test_venue_is_none_when_provider_gives_nothing() -> None:
    assert normalize_venue(None) is None
    assert normalize_venue("") is None
    venue = normalize_venue("Emirates Stadium", city="London", country="England")
    assert venue is not None and venue.name == "Emirates Stadium"


# --- safe field access -----------------------------------------------------
def test_require_and_optional_handle_schema_drift() -> None:
    payload = {"crestUrl": "http://x/y.png"}
    assert optional(payload, "crest", "crestUrl") == "http://x/y.png"
    assert optional(payload, "missing") is None
    with pytest.raises(NormalizationError):
        require(payload, "id")
    with pytest.raises(NormalizationError):
        require({"id": None}, "id")


# --- TimeWindow ------------------------------------------------------------
def test_time_window_rejects_inverted_range() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError):
        TimeWindow(start=now, end=now)


def test_time_window_next_days_contains() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    window = TimeWindow.next_days(7, now=now)
    assert window.contains(now + timedelta(days=3))
    assert not window.contains(now + timedelta(days=8))


# --- codecs ----------------------------------------------------------------
def test_sport_codec_roundtrip() -> None:
    sport = Sport(key="football", name="Football", category=SportCategory.TEAM, provider_key="f")
    assert sport_from_dict(sport_to_dict(sport)) == sport


def test_competition_codec_roundtrip_with_season() -> None:
    comp = Competition(
        external_id="PL",
        name="Premier League",
        sport_key="football",
        type=CompetitionType.LEAGUE,
        country="England",
        season=Season(label="2025/26", start=datetime(2025, 8, 1, tzinfo=UTC), is_current=True),
        logo_url="http://x",
    )
    assert competition_from_dict(competition_to_dict(comp)) == comp


def test_team_codec_roundtrip() -> None:
    team = Team(external_id="57", name="Arsenal FC", sport_key="football", short_name="ARS")
    assert team_from_dict(team_to_dict(team)) == team
