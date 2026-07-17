# MatchSync — Frontend

Next.js 15 (App Router) + TypeScript + TailwindCSS + shadcn/ui + TanStack Query
+ Zustand. See [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) for the design.

## Layout

```
app/          # App Router: routes, layouts, global providers
components/
├── ui/       # shadcn/ui primitives
└── features/ # composed, domain-aware components
features/     # feature-based modules (self-contained per feature)
hooks/        # reusable cross-feature hooks
lib/
├── api/      # typed backend client
├── query/    # TanStack Query provider + keys
└── utils.ts  # cn() and shared helpers
services/     # API service functions built on lib/api
stores/       # Zustand (ephemeral client state ONLY)
types/        # shared TS types (OpenAPI-generated later)
utils/        # pure helpers
```

## State rule (from Stage 1)

- **Server state → TanStack Query** (`lib/query`, `services`).
- **Client/ephemeral state → Zustand** (`stores`).

Never mirror server data in Zustand.

## Quick start

```bash
npm install
cp .env.example .env.local
npm run dev            # http://localhost:3000
```

## Commands

```bash
npm run dev            # dev server
npm run build          # production build (standalone)
npm run lint           # ESLint
npm run typecheck      # tsc --noEmit
npm run format         # Prettier
npm run test           # Vitest (unit/component)
npm run test:e2e       # Playwright (e2e)
```
