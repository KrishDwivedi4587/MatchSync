"""Seed static reference data.

Populates ONLY static reference data required for the app to function:
sports, a starter set of competitions, provider metadata, and scheduler-job
registry entries. Idempotent — safe to run repeatedly (existing rows by natural
key are skipped).

Run from the ``backend/`` directory:
    python scripts/seed.py
"""

from __future__ import annotations

import asyncio

from app.domain.value_objects.enums import (
    CompetitionType,
    JobStatus,
    ProviderStatus,
    ProviderType,
    SportCategory,
)
from app.persistence.models.catalog import Competition, Sport
from app.persistence.models.system import ProviderMetadata, SchedulerJob
from app.persistence.repositories.catalog import CompetitionRepository, SportRepository
from app.persistence.repositories.system import (
    ProviderMetadataRepository,
    SchedulerJobRepository,
)
from app.persistence.session import transaction

# --- Provider registry (key, name, type) -----------------------------------
PROVIDERS = [
    ("football-api", "Football Data Provider", ProviderType.SPORTS),
    ("valorant", "Valorant Esports Provider", ProviderType.SPORTS),
    ("basketball-api", "Basketball Data Provider", ProviderType.SPORTS),
    ("google-calendar", "Google Calendar", ProviderType.CALENDAR),
    ("google-identity", "Google Identity", ProviderType.IDENTITY),
]

# --- Launch sports (key, name, category, provider_key, order) ---------------
SPORTS = [
    ("football", "Football", SportCategory.TEAM, "football-api", 1),
    ("valorant", "Valorant", SportCategory.ESPORTS, "valorant", 2),
    ("basketball", "Basketball", SportCategory.TEAM, "basketball-api", 3),
]

# --- Starter competitions: sport_key -> [(provider_id, name, type, country)] -
COMPETITIONS: dict[str, list[tuple[str, str, CompetitionType, str | None]]] = {
    "football": [
        ("PL", "Premier League", CompetitionType.LEAGUE, "England"),
        ("PD", "La Liga", CompetitionType.LEAGUE, "Spain"),
        ("CL", "UEFA Champions League", CompetitionType.CUP, None),
    ],
    "valorant": [
        ("vct-champions", "VCT Champions", CompetitionType.TOURNAMENT, None),
        ("vct-masters", "VCT Masters", CompetitionType.TOURNAMENT, None),
    ],
    "basketball": [
        ("NBA", "NBA", CompetitionType.LEAGUE, "USA"),
        ("EL", "EuroLeague", CompetitionType.LEAGUE, None),
    ],
}

# --- Scheduler job registry (key, name, cron) -------------------------------
SCHEDULER_JOBS = [
    ("scan_due_subscriptions", "Scan due subscriptions", "*/15 * * * *"),
    ("refresh_catalog", "Refresh sports catalog", "0 3 * * *"),
    ("provider_health_check", "Provider health check", "*/30 * * * *"),
]


async def seed() -> None:
    async with transaction() as session:
        providers = ProviderMetadataRepository(session)
        sports_repo = SportRepository(session)
        competitions = CompetitionRepository(session)
        jobs = SchedulerJobRepository(session)

        # Providers
        for key, name, ptype in PROVIDERS:
            if await providers.get_by_key(key) is None:
                await providers.add(
                    ProviderMetadata(
                        key=key,
                        name=name,
                        provider_type=ptype,
                        status=ProviderStatus.HEALTHY,
                    )
                )

        # Sports + their starter competitions
        for key, name, category, provider_key, order in SPORTS:
            sport = await sports_repo.get_by_key(key)
            if sport is None:
                sport = await sports_repo.add(
                    Sport(
                        key=key,
                        name=name,
                        category=category,
                        provider_key=provider_key,
                        display_order=order,
                    )
                )
            for prov_id, comp_name, comp_type, country in COMPETITIONS.get(key, []):
                existing = await competitions.get_by_provider_id(sport.id, prov_id)
                if existing is None:
                    await competitions.add(
                        Competition(
                            sport_id=sport.id,
                            provider_competition_id=prov_id,
                            name=comp_name,
                            type=comp_type,
                            country=country,
                        )
                    )

        # Scheduler job registry
        for key, name, cron in SCHEDULER_JOBS:
            if await jobs.get_by_key(key) is None:
                await jobs.add(
                    SchedulerJob(key=key, name=name, schedule=cron, status=JobStatus.ENABLED)
                )

    print("Seed complete: providers, sports, competitions, scheduler jobs.")


if __name__ == "__main__":
    asyncio.run(seed())
