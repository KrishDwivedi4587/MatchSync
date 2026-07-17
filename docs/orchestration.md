# Orchestration Platform (Stage 9)

The scheduler decides **when** work runs. Workers execute it. The Stage 8
synchronization engine — the only component that knows *how* synchronization
works — is invoked unchanged.

```mermaid
flowchart LR
    B[Celery Beat<br/>scan_due_subscriptions] -->|enqueue Job| Q{{Queues}}
    Q --> W[Worker]
    W -->|acquire Redis lock| L[(Lock)]
    W --> SE[Synchronization Engine<br/>Stage 8, unchanged]
    SE --> CP[Calendar Platform]
    CP --> G[Google Calendar]
    SE --> H[(sync_history)]
    W --> J[(Job store · Redis)]
    SPORTS[Sports APIs] -.x.- W
    G -.x.- W
```

Workers know nothing about Google and nothing about sports APIs. They load a
job, take a lock, call **one** engine method, record the outcome, release.

## Formal guarantees

| # | Guarantee | How it is enforced |
|---|---|---|
| **G1** | **At-most-one active synchronization per subscription** | A Redis lock keyed `sync:subscription:{id}`, taken with `SET NX PX`. `SYNC_SUBSCRIPTION` and `RECONCILE` deliberately share the key. A contending delivery becomes `SKIPPED`, not an error. |
| **G2** | **Lost workers do not permanently lock subscriptions** | The lock has a TTL and is renewed at `ttl/3`. A crashed worker stops renewing; the lease expires and the lock is reclaimed automatically. |
| **G3** | **A worker cannot release or extend a lock it no longer owns** | The lock value is a random ownership token; release and renewal are compare-and-swap on it (`WATCH`/`MULTI`). |
| **G4** | **Retrying a job cannot produce duplicate calendar events** | Not because retries are careful — because the engine is **idempotent** (Stage 8, I5/I6) and duplicate prevention is *structural*: `UNIQUE(subscription_id, fixture_id)` plus a deterministic remote event id. A replay re-plans and yields `NO_CHANGE`. |
| **G5** | **Failed jobs never corrupt synchronization state** | The engine writes `synced_content_hash` only *after* the calendar call succeeds. An aborted job leaves the database describing exactly what is true. |
| **G6** | **Every scheduled synchronization is eventually executed unless permanently failed** | `task_acks_late` + `task_reject_on_worker_lost` redeliver a lost message. Beat re-enqueues from `subscriptions.next_sync_at`, which only advances on a non-fatal run. |
| **G7** | **A poison message can never loop forever** | Bounded attempts, then the dead-letter queue. Permanent failures (revoked access, validation) dead-letter on the *first* attempt. |
| **G8** | **A duplicate delivery of a finished job is a no-op** | The worker re-reads job state and returns early for terminal states. |
| **G9** | **A cancelled job never touches the calendar** | Cancellation both revokes the broker message *and* marks the job `CANCELLED`; the worker re-reads state before executing. |
| **G10** | **The scheduler never synchronizes** | Beat's only sync-related task is `scan_due_subscriptions`, which reads `next_sync_at` and enqueues ids. It calls no engine. |

> **G4 is worth restating.** The lock is an optimization for *quota and latency*,
> not the correctness mechanism. If Redis vanished entirely and two workers ran
> the same subscription concurrently, the result would still be one calendar event
> per fixture. Correctness lives in Stage 8; orchestration only makes it efficient.

## Job model

| State | Meaning |
|---|---|
| `PENDING` → `QUEUED` | created, handed to the broker |
| `RUNNING` | a worker holds it (increments `attempts`) |
| `SUCCEEDED` | done |
| `SKIPPED` | a concurrent worker held the lock — a *correct* no-op, never retried |
| `RETRYING` → `QUEUED` | transient failure, backoff elapsed |
| `FAILED` | ran and failed, or died before starting (no handler) |
| `DEAD_LETTER` | budget spent or permanently failed; manual retry only |
| `CANCELLED` | revoked before execution |

Illegal transitions raise. `SKIPPED`, `SUCCEEDED`, `CANCELLED` are terminal.

**Job types:** `sync_subscription`, `sync_user`, `reconcile`, `fixture_import`,
`metadata_refresh`, `cleanup`, `health_check`.

## Queues

| Queue | Carries | Why separate |
|---|---|---|
| `sync.high` | manual "sync now" | a user's click never waits behind a nightly backlog |
| `sync.default` | scheduled syncs | the bulk of the work |
| `ingest` | fixture imports | a slow provider cannot starve syncs |
| `maintenance` | metadata, cleanup, health, the Beat scan | low priority, low volume |
| `dead_letter` | poison jobs (indexed, not consumed) | inspection + manual retry |

Deploy separate worker pools per queue set (see `docker-compose.yml`).

## Scheduling

| Policy | Mechanism |
|---|---|
| **Hourly / user-configurable interval** | `subscriptions.sync_frequency_minutes` → `next_sync_at`, maintained by Stage 8 |
| **Recurring** | Beat's `scan_due_subscriptions` (every 5 min) enqueues every subscription whose `next_sync_at` has passed |
| **Missed-schedule recovery** | The scan is *state-based*, not tick-based. A scheduler outage means the next scan simply finds a larger due set. Nothing is lost. |
| **Immediate / manual** | `POST /jobs/sync` → `HIGH` priority → `sync.high` |
| **Delayed** | `delay_seconds` on the request → Celery `countdown` |
| **Retry schedule** | exponential backoff + full jitter (below) |
| **Timezone-aware** | Everything is UTC end-to-end. Per-user local-time windows are a `user.timezone` filter in the scan, deliberately deferred. |
| **Future cron expressions** | `scheduler_jobs.schedule` already stores a cron string; a dynamic Beat scheduler can read it without a migration. |

> **One Beat entry serves 100k subscriptions.** Per-subscription Beat entries
> would not scale (Stage 1, §10).

## Distributed locking

```
acquire:  SET lock:{name} <token> NX PX ttl
renew:    WATCH → GET == token → MULTI → PEXPIRE      (every ttl/3)
release:  WATCH → GET == token → MULTI → DEL
```

- **`NX`** gives mutual exclusion. **`PX`** gives automatic deadlock recovery.
- **Ownership token** prevents a lagging worker from releasing someone else's lock.
- **`WATCH`/`MULTI` rather than Lua** — same atomicity, works on Redis-compatible
  backends without scripting.
- **A lost lease is safe** (see G4). We log `lock.lost` and continue.

> A bug found while building this: the renew interval had a `max(ttl/3, 1.0)`
> floor, so any lease with `ttl ≤ 3s` renewed *at or after* expiry. Harmless at
> the production TTL (120s → 40s), fatal for short leases. The floor is now an
> epsilon.

## Retry orchestration

```
delay = uniform(0, min(cap, base · 2^(attempt-1)))     # full jitter
```

**Full jitter**, not equal or none: when a Google outage fails a whole fleet at
once, the retries must not re-converge into a thundering herd.

| Failure | Kind | Behaviour |
|---|---|---|
| `RateLimitError`, `QuotaExceededError` | `RATE_LIMITED` | retry, but never before the throttle floor (60 s) |
| `ProviderUnavailableError`, `RetryableError`, unknown non-`AppError` | `TRANSIENT` | retry with backoff |
| `CalendarReauthRequiredError`, `AuthorizationError`, `ValidationAppError`, `NotFoundError`, `PermanentError` | `PERMANENT` | **dead-letter on the first attempt** — retrying cannot fix a revoked token, and Stage 8 already paused the subscription |

Exhausted budget → dead-letter. `POST /jobs/{id}/retry` restores the full budget.

## Failure recovery

| Failure | Recovery |
|---|---|
| **Worker crash (SIGKILL)** | `task_reject_on_worker_lost` redelivers the message. `detect_stuck_jobs` reaps the `RUNNING` record after 30 min. The lock expires on its own. |
| **Graceful shutdown** | Celery warm-shutdown finishes in-flight tasks (`stop_grace_period: 60s`); the heartbeat key is deleted immediately. |
| **Redis restart** | Jobs in flight are re-delivered by the broker; the *job log* may be lost — by design, it is ephemeral. Durable truth (`sync_history`) is in Postgres. Locks are gone, which is safe (G4). |
| **Postgres / network outage** | The engine raises a transient error → job retries with backoff. |
| **Google outage / quota** | `RATE_LIMITED` → long backoff. Stage 8 aborts the run, marking nothing synced. |
| **Provider outage** | Only `fixture_import` is affected; syncs continue from persisted fixtures. |
| **Lost lock** | Run continues; idempotency + structural duplicate prevention keep it correct. |
| **Duplicate job** | Terminal-state check → no-op. Concurrent duplicate → `SKIPPED`. |
| **Beat outage** | The next scan finds a bigger due set. State-based scheduling has no "missed tick". |

## Observability

Logged: `job.created`, `job.retried`, `job.cancelled`, `worker.lock_acquired`,
`worker.skipped_locked`, `worker.job_succeeded` (with `duration_seconds`,
`queue_latency_seconds`, `attempts`), `worker.job_retrying` (kind, delay),
`worker.job_dead_lettered`, `lock.contended`, `lock.lost`,
`scheduler.scan_due_subscriptions`, `scheduler.stuck_jobs_reaped`.

**Never logged:** OAuth tokens, calendar contents, event bodies, provider payloads.

### Metrics (`GET /orchestration/metrics`)

Queue depth per queue · worker count and utilization · success/failure rate ·
retry count · dead-letter depth · **lock contention** (the `SKIPPED` counter) ·
**scheduling delay** (age of the oldest due subscription) · backlog size.

### Health (`GET /orchestration/health`)

Redis reachable · workers online · scheduler heartbeat · stuck-job count.
Liveness is a TTL'd Redis key, so a SIGKILLed worker disappears on its own.

## API

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/jobs/sync` | Enqueue a manual (HIGH) sync |
| GET | `/api/v1/jobs` | List the caller's jobs (filter by state/type) |
| GET | `/api/v1/jobs/dead-letter` | The poison queue |
| GET | `/api/v1/jobs/{id}` | One job |
| POST | `/api/v1/jobs/{id}/retry` | Re-queue; restores the attempt budget |
| POST | `/api/v1/jobs/{id}/cancel` | Revoke + mark cancelled |
| GET | `/api/v1/scheduler/status` | Beat liveness + schedule definitions |
| GET | `/api/v1/workers` | Live workers |
| GET | `/api/v1/queue` | Queue depths |
| GET | `/api/v1/orchestration/backlog` | Due subscriptions + scheduling delay |
| GET | `/api/v1/orchestration/metrics` | Aggregate metrics |
| GET | `/api/v1/orchestration/health` | Platform health |

Paths use `/jobs/{id}/retry` rather than `POST /jobs/retry` with a body: the job
is the resource being acted on (Stage 1, §11).

## Schema

**No migration.** Job *execution* records live in Redis, per Stage 1: *"Redis
holds only ephemeral/derived state; losing Redis loses in-flight jobs and caches,
never durable truth."* A job's durable business outcome already lives in
`sync_history` (Stage 8) and `import_runs` (Stage 7). Recurring *schedule
definitions* reuse the frozen `scheduler_jobs` table.

## Operating at scale

| Users | Shape |
|---|---|
| **10** | One worker, one Beat, one Redis. The scan finds a handful of due subs. |
| **100** | Same. Queue depth stays at zero. |
| **1,000** | 2–4 sync workers. Split `ingest`/`maintenance` onto their own pool so a nightly import never delays a sync. |
| **10,000** | Scale sync workers horizontally (they are stateless; the lock makes replica count irrelevant to correctness). Raise `scheduler_scan_batch_size`. Watch `lock_contention` and `max_scheduling_delay_seconds`. |
| **100,000** | Partition queues by shard (`sync.default.0..N`) with subscriptions hashed to a shard, so a hot tenant cannot monopolize one queue. Redis Cluster or a separate Redis per role (broker / locks / cache). Autoscale workers on `queue_depth_total` and `max_scheduling_delay_seconds`, not CPU. |

- **Google quotas** — `sync_task_rate_limit` throttles per worker; the Calendar
  Platform's own token bucket and batching (Stage 5) bound request volume. The
  content-hash no-op means a steady-state sync costs **zero** API calls.
- **Provider quotas** — only `fixture_import` touches providers; it is scheduled
  four times a day on its own queue with its own circuit breaker (Stage 6).
- **Subscription sharding** — the lock key is already per-subscription, so
  sharding is a queue-routing change, not a correctness change.
- **Recovery after an outage** — state-based scheduling means the backlog drains
  naturally; the only tuning knob is worker count.

## Operations guide

```bash
# API
uvicorn app.main:app

# Sync workers (scale this)
celery -A app.worker.celery_app worker -Q sync.high,sync.default -c 4

# Maintenance/ingest worker
celery -A app.worker.celery_app worker -Q ingest,maintenance -c 2

# Exactly ONE Beat process per deployment
celery -A app.worker.celery_app beat
```

- **Never run two Beat processes** — they would double-enqueue. (Duplicates would
  be `SKIPPED` by the lock, but it wastes broker traffic.)
- **Draining a worker:** send `SIGTERM`. Celery finishes in-flight tasks; the
  heartbeat key is removed immediately.
- **Redis visibility timeout** is set above the hard task time limit, so a running
  task is never redelivered underneath itself.
- **Fork safety:** `worker_process_init` disposes the async DB pool so no child
  inherits a parent's asyncpg sockets.

```bash
cd backend && pytest tests/ -k orchestration
```

Browse at `http://localhost:3000/jobs`.

## Troubleshooting

- **Jobs sit `queued`** — no worker is consuming that queue. Check `GET /workers`
  and the `-Q` flags.
- **Everything is `skipped`** — a stale lock, or a duplicate Beat. Locks expire in
  `LOCK_TTL_SECONDS`; check for two `beat` processes.
- **A job is stuck `running`** — its worker died. `detect_stuck_jobs` reaps it
  after `STUCK_JOB_THRESHOLD_SECONDS`; the broker already redelivered the work.
- **Dead-letter fills with `calendar_reauth_required`** — the user must reconnect
  Google Calendar. Retrying will not help (by design).
- **`max_scheduling_delay_seconds` climbing** — workers are saturated. Scale out.
