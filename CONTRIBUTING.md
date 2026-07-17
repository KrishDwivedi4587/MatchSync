# Contributing to MatchSync

## Golden rule

The [Architecture Document](docs/ARCHITECTURE.md) is the single source of truth.
Do not introduce architectural changes without an accompanying ADR in
`docs/adr/` and reviewer sign-off.

## Git strategy

**Branching — trunk-based with short-lived branches.**

- `main` — always deployable, protected. No direct pushes.
- `develop` — optional integration branch (used once staging exists).
- Work branches: `feat/<slug>`, `fix/<slug>`, `chore/<slug>`, `docs/<slug>`.

Keep branches short-lived and rebased on `main`. Small PRs over large ones.

**Commits — Conventional Commits.**

```
<type>(<scope>): <subject>

feat(auth): add google oauth callback handler
fix(sync): prevent duplicate event on reschedule
chore(ci): cache pip in backend workflow
```

Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `ci`.
This history feeds automated changelogs and **SemVer** versioning
(`MAJOR.MINOR.PATCH`).

**Pull requests & reviews.**

- Fill in the PR template; link the issue.
- CI must be green (lint, format, type-check, test, docker build).
- At least one approving review before merge.
- Squash-merge to keep `main` history linear and readable.

**Labels.** Apply `type:`, `area:`, and `priority:` labels (see
`.github/labels.yml`).

## Local quality gates

Run before pushing (or let `pre-commit` do most of it):

```bash
# Backend
cd backend && ruff check . && black --check . && mypy app && pytest
# Frontend
cd frontend && npm run lint && npm run format:check && npm run typecheck && npm run test
```

Install hooks once: `pip install pre-commit && pre-commit install`.

## Code style

- **Python:** Ruff (lint + import order), Black (format), mypy `--strict`.
- **TypeScript:** ESLint, Prettier, `tsc` strict mode.
- Match the surrounding code; keep functions small; comment the *why*.
