# Database Documentation

## Conceptual model

The full entity model is specified in
[ARCHITECTURE.md, Section 5](ARCHITECTURE.md#5-database-design-conceptual) and is
now implemented (Stage 3). The schema has **16 tables**:

| Table | Purpose |
|---|---|
| `users` | MatchSync account (identity within our system). |
| `google_accounts` | Linked identity/calendar account (provider-discriminated). |
| `oauth_tokens` | Encrypted OAuth tokens (1-1 with an account). |
| `calendars` | External calendars belonging to an account. |
| `sports` | Sports catalog (data, not an enum — extensible). |
| `competitions` | Leagues/tournaments per sport. |
| `teams` | Clubs/esports teams/individual competitors. |
| `team_competition` | M2M association of teams to competitions. |
| `fixtures` | Normalized cached matches (identity_key + content_hash). |
| `subscriptions` | User intent: sync a scope into a calendar. |
| `calendar_events` | (subscription, fixture) → event mapping — dedup linchpin. |
| `sync_history` | One sync run per subscription execution. |
| `sync_operations` | Per-fixture outcome within a run. |
| `application_logs` | Durable audit/application events. |
| `scheduler_jobs` | Scheduler job registry (metadata; Beat executes). |
| `provider_metadata` | External provider config + health (no secrets). |

Models live in [`backend/app/persistence/models/`](../backend/app/persistence/models/);
enums in [`backend/app/domain/value_objects/enums.py`](../backend/app/domain/value_objects/enums.py);
repositories in [`backend/app/persistence/repositories/`](../backend/app/persistence/repositories/).

### Seed data

Static reference data (sports, starter competitions, providers, scheduler jobs)
is loaded idempotently:

```bash
cd backend && python scripts/seed.py
```

## Engine & drivers

- **Async (app runtime):** `postgresql+asyncpg` via SQLAlchemy 2.0 async engine.
- **Sync (migrations):** `postgresql+psycopg` used by Alembic.
- Both URLs are derived from the same settings (`app/core/config.py`), so
  credentials live in exactly one place.

## Migrations (Alembic)

- Config: `backend/alembic.ini` (URL injected at runtime from settings — no
  credentials in the file).
- Environment: `backend/alembic/env.py` imports `Base.metadata` and the models
  package so `--autogenerate` detects new tables.

```bash
cd backend
alembic revision --autogenerate -m "message"
alembic upgrade head
alembic downgrade -1
alembic history
```

## Conventions

- Surrogate `UUID` primary keys (avoids hotspotting; enables future sharding).
- All timestamps stored in **UTC**.
- Uniqueness constraints enforce correctness invariants at the DB level (e.g.
  the duplicate-prevention constraint on `CalendarEventMapping`).
