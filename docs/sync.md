# Synchronization Engine (Stage 8)

The single source of business logic for calendar synchronization. It begins at a
**persisted fixture** and ends at **CalendarService**. It never calls a sports
API and never speaks Google.

```mermaid
flowchart LR
    DB[(fixtures · calendar_events)] --> L[Load]
    L --> P[Planner + Diff]
    P --> PL[SyncPlan · deterministic]
    PL --> E[Executor]
    E --> CS[CalendarService]
    CS --> G[Google Calendar]
    E --> R[(sync_history · sync_operations)]
    SPORTS[Sports APIs] -.x.- P
```

## Formal model

**States of a sync unit** `(subscription, fixture)`: `UNMAPPED`, `PENDING`
(create never confirmed), `SYNCED`, `STALE`, `CANCELLED`, `DELETED`, `ORPHANED`,
`CONFLICTED`.

**Invariants** — each enforced, each tested:

| # | Invariant | Enforced by |
|---|---|---|
| **I1** | Every `(subscription, fixture)` has at most one non-deleted mapping | frozen `UNIQUE(subscription_id, fixture_id)` |
| **I2** | Every active mapping has at most one remote event | deterministic event id `derive_event_id(identity_key)`; a duplicate insert is rejected (409) |
| **I3** | Every owned remote event maps to exactly one fixture, or is orphaned and deleted | reconcile pass |
| **I4** | `plan(F, M, R)` is pure; identical inputs → identical, identically-ordered plan | pure planner; total sort key |
| **I5** | An empty plan performs **zero** calendar calls and **zero** writes | executor short-circuit + `sync_skip_empty_runs` |
| **I6** | After a successful action, `synced_content_hash == fixture.content_hash` | the hash is written **only after** the calendar call succeeds |
| **I7** | Every calendar mutation writes exactly one `sync_operations` row | executor result → operation row |

No-ops write **no** operation rows — at millions of fixtures that would dominate cost.

## Algorithm

```
plan(S):
  F      ← fixtures in scope(S) ∩ window      (changed-only in INCREMENTAL)
  Scope  ← ids of fixtures still in scope(S)  (authoritative, id-only query)
  M      ← mappings(S)
  idx    ← index M by fixture_id
  A      ← [ classify(f, idx[f.id]) for f in F ]        # pass 1
  A     += [ DELETE(m) for m in M if m.fixture_id ∉ Scope ]   # pass 2
  A     += reconcile(R, idx, F) if mode == RECONCILE           # pass 3
  return sort(dedupe(A), key=(rank, identity_key, type))
```

> **`Scope` is separate from `F` on purpose.** In incremental mode only *changed*
> fixtures are loaded. Treating "absent from `F`" as "no longer in scope" would
> delete the calendar event of every unchanged fixture. This is the sharpest edge
> in the engine and is pinned by a regression test.

**Complexity.** Time `O(|F| + |M| + |R| + |P| log |P|)` = `O(N log N)`; space `O(N)`.
The `log` factor is only the determinism sort. Execution issues `⌈|P|/50⌉` batched
calendar calls, not `|P|`.

**Worst case.** Every fixture changed → `|P| = |F|`. Incremental mode bounds `|F|`
to *fixtures changed since the watermark*, so the steady state is `|F| ≈ 0` and a
run costs two indexed `SELECT`s.

**Why it is correct.** `classify` is a pure function of `(fixture, mapping)`, so
I4 holds. I6 plus the hash comparison make `NO_CHANGE` a **fixed point**: once
synced, replanning yields `∅`, which by I5 gives idempotency. I1 and I2 make
duplicate prevention *structural* — it cannot fail even if the application logic
is wrong.

## Identity resolution

| Level | Signal | Source |
|---|---|---|
| 1 | `calendar_events.fixture_id` | the mapping row (authoritative) |
| 2 | `ms_id` extended property == `fixtures.identity_key` | Stage 5 metadata + Stage 7 identity |
| 3 | deterministic event id `derive_event_id(identity_key)` | rejects duplicate inserts |
| 4 | ownership marker `ms_app` | separates our events from the user's |
| 5 | `ms_hash` vs `synced_content_hash` | detects manual edits |

## Diff rules (first match wins)

1. **Fixture gone** → `DELETE` if an event exists, else `NO_CHANGE`. Never create for a dead fixture.
2. **No mapping** → `CREATE`.
3. **Mapping unconfirmed** (`external_event_id IS NULL`) → `RECREATE`.
4. **Remote event missing** (reconcile only) → `RECREATE`.
5. **User edited the event** → `CONFLICT` under `USER_WINS`; under `FIXTURE_WINS` fall through.
6. **Content hash equal** → `NO_CHANGE`. *The fixed point. Zero API calls.*
7. **Fixture cancelled** → `CANCEL` (annotate) or `DELETE`, per policy.
8. **Otherwise** → `MINOR_UPDATE` (venue/round/stage) or `MAJOR_UPDATE` (time/status/participants).

**Naming the changed fields without a new column:** the mapping stores only the
hash we last pushed; Stage 7's `fixture_versions` stores a snapshot per hash, so
we look up the snapshot whose hash equals `synced_content_hash` and diff it. If
that version was pruned we fall back to `MAJOR_UPDATE` — the safe
over-approximation (a full PATCH).

## Conflict resolution

| Situation | Policy |
|---|---|
| **User edited the event** | `FIXTURE_WINS` (default): only overwritten when the *fixture* actually changed. An unchanged fixture **never** clobbers a manual edit. `USER_WINS`: recorded as a conflict, never overwritten. |
| **Event deleted in the calendar** | `RECREATE` (in-line on a 404 during PATCH, or via reconcile). |
| **Duplicate owned events** | Keep the mapped one (else the lowest event id); delete the rest. |
| **Orphaned owned event** | No fixture claims it → delete (invariant I3). |
| **Corrupted metadata** | Owned but unidentifiable → `RECONCILE` action, never blind-deleted. |
| **Missing metadata / not ours** | Ignored entirely. We only touch events carrying `ms_app`. |
| **Retry budget exhausted** | Dead-lettered as `CONFLICT` after `sync_max_item_retries` failures (derived from `sync_operations` history — no new column). |

## Incremental synchronization

Three modes:

- **`incremental`** (default) — fixtures with `updated_at ≥ last_synced_at`, plus
  *repair* units (`external_event_id IS NULL` or hash drift). No remote read.
- **`full`** — every fixture in scope + window. No remote read.
- **`reconcile`** — `full` + one `list_events` call to repair remote drift.

The watermark advances on `SUCCESS` and `PARTIAL`, never on `FAILED`. Advancing on
`PARTIAL` is safe because the repair query re-picks unfinished mappings regardless
of fixture freshness.

## Idempotency — how it is enforced

1. `synced_content_hash` is written **only after** the calendar call succeeds (I6).
2. Rule 6 makes an equal hash a `NO_CHANGE` verdict — the fixed point.
3. An all-`NO_CHANGE` plan has `mutations == 0` → `plan.is_empty`.
4. The executor short-circuits on an empty plan: **zero API calls**.
5. With `sync_skip_empty_runs=True` (default) no `sync_history` row, no
   `sync_operations`, no `calendar_events` write is made either.

> The one remaining write on an empty run is the scheduler watermark
> (`last_synced_at` / `next_sync_at`). That is scheduling bookkeeping, not
> synchronization state, and it is skipped entirely with `advance_schedule=False`
> and in `GET /sync/plan`, which perform **literally zero writes**.

## Failure and recovery

**There is no rollback.** Calendar mutations are external side-effects and cannot
be undone safely. Recovery is *forward*:

| Failure | Behaviour |
|---|---|
| Quota exceeded / rate limited / provider down | Abort the remaining actions; run is `PARTIAL` or `FAILED`; nothing marked synced; next run replans the remainder |
| Access revoked / permission denied | Abort **and pause the subscription** — stop burning quota on a calendar we cannot write to |
| Item error inside a batch | Isolated: recorded as a failed `sync_operation`; other items proceed |
| Event missing on PATCH (404) | Recreated in-line |
| Duplicate rejected on INSERT (409) | `duplicates_prevented++`, repaired by patching the existing event |
| Retry exhaustion | Dead-lettered as a conflict, not retried forever |

## Performance

- Batched calendar calls (`sync_batch_size`, default 50 — Google's cap).
- Bulk `INSERT`/`UPDATE` of mappings and operations, one round-trip each.
- O(1) matching via a hash index built once per subscription.
- Content-hash short-circuit: an unchanged fixture costs **zero** API calls.
- `sync_max_fixtures` / `sync_max_actions` bound memory and work per run; because
  ordering is deterministic, bounded runs make monotone progress (no starvation).
- Reconcile's remote read is **one** paginated `list_events` call, filtered to
  MatchSync-owned events.

## API

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/sync` | Sync one subscription (or all of the user's) |
| POST | `/api/v1/sync/user` | Sync every active subscription |
| POST | `/api/v1/sync/calendar` | Sync every subscription on one calendar |
| GET | `/api/v1/sync/plan` | **Preview** — zero writes, zero API calls |
| GET | `/api/v1/sync/status` | Per-subscription schedule + last run |
| GET | `/api/v1/sync/history` | Recent runs |
| GET | `/api/v1/sync/report/{id}` | One run + its operations |
| GET | `/api/v1/sync/metrics` | Aggregate metrics |

## Observability

Logged: `sync.planned` (full plan stats + `plan_ms`), `sync.finished`
(created/updated/deleted/skipped/failed, `api_calls`, `duplicates_prevented`,
`no_op_ratio`, `total_ms`), `sync.noop`, `sync.execute.aborted`,
`sync.duplicate_prevented`. No fixture or event contents are logged.

Metrics (`GET /sync/metrics`): runs by status, calendar writes, skipped
operations, no-op percentage, failure rate.

> `api_calls`, `plan_ms` and `execute_ms` are returned live and logged, but are
> **not** persisted on `sync_history` (no column exists and none was added). Per-run
> counts and every operation *are* persisted, so all calendar mutations remain
> traceable.

## Schema

**No migration.** Stage 3 anticipated this stage exactly: `calendar_events`
already carries `fixture_identity_key`, `synced_content_hash`, `state`,
`last_pushed_at`; `sync_history` / `sync_operations` already model runs and
operations; `subscriptions` already carries `last_synced_at` / `next_sync_at`.

The retry budget is *derived* from `sync_operations` rather than stored, and the
changed-field diff reuses Stage 7's `fixture_versions` — both deliberately, to
avoid new columns.

## Developer guide

```bash
cd backend && pytest tests/ -k sync        # the engine test suite
curl "localhost:8000/api/v1/sync/plan?subscription_id=<id>&mode=full"   # safe preview
curl -X POST localhost:8000/api/v1/sync -d '{"subscription_id":"<id>"}'
```

Browse at `http://localhost:3000/sync`.

## Troubleshooting

- **"Nothing to do" on every run** — that is correct. The plan is empty because
  every fixture's `content_hash` matches its mapping's `synced_content_hash`.
- **Subscription became `paused`** — calendar access was revoked. Reconnect on
  `/settings/calendar`, then re-activate.
- **A user's manual edit was overwritten** — the fixture changed, and the policy
  is `FIXTURE_WINS`. Set `SYNC_CONFLICT_POLICY=user_wins` to never overwrite.
- **Duplicate events in the calendar** — run `mode=reconcile`; the planner deletes
  duplicates and orphans.
- **A fixture never syncs** — check `sync_operations` for ≥ `sync_max_item_retries`
  failures; it is dead-lettered as a conflict.
