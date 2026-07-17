# MatchSync Documentation

| Document | Purpose |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | **Source of truth.** The full Software Architecture Document from Stage 1. |
| [developer-guide.md](developer-guide.md) | Local setup, workflow, first-run checklist, tooling. |
| [authentication.md](authentication.md) | Google OAuth setup, cookies/JWT/sessions, troubleshooting. |
| [calendar.md](calendar.md) | Calendar platform: provider abstraction, scopes, metadata, limits. |
| [sports.md](sports.md) | Sports platform: provider abstraction, normalization, capabilities. |
| [fixtures.md](fixtures.md) | Fixture ingestion: identity, versioning, dedup, import reports. |
| [sync.md](sync.md) | Synchronization engine: planner, diff, invariants, idempotency. |
| [orchestration.md](orchestration.md) | Scheduler, workers, queues, locks, retries, operations. |
| [application.md](application.md) | Application layer: onboarding, subscriptions, dashboard, settings. |
| [deployment.md](deployment.md) | Building and running images in production. |
| [api.md](api.md) | API conventions; live OpenAPI docs at `/docs`. |
| [database.md](database.md) | Migrations workflow; conceptual model lives in the SAD. |

Future decision records (ADRs) resolving the SAD's `[Deferred]` items go in
`docs/adr/` as they are made.
