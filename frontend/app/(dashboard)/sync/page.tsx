"use client";

import { useState } from "react";
import { AlertTriangle, CheckCircle2, Eye, RefreshCw, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { SyncMode } from "@/lib/sync/api";
import {
  useRunSync,
  useSyncHistory,
  useSyncMetrics,
  useSyncPlan,
  useSyncReport,
  useSyncStatus,
} from "@/lib/sync/use-sync";
import { cn } from "@/lib/utils";

const MODES: SyncMode[] = ["incremental", "full", "reconcile"];

const STATUS_STYLE: Record<string, string> = {
  success: "text-primary",
  partial: "text-amber-600 dark:text-amber-500",
  failed: "text-destructive",
  running: "text-muted-foreground",
};

function StatusIcon({ status }: { status: string }) {
  if (status === "success") return <CheckCircle2 className="h-4 w-4" aria-hidden />;
  if (status === "failed") return <XCircle className="h-4 w-4" aria-hidden />;
  return <AlertTriangle className="h-4 w-4" aria-hidden />;
}

function fmt(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";
}

// Synchronization page: status, plan preview, manual run, history, metrics,
// failure + conflict viewer. Nothing here decides *when* to sync — that is
// Stage 9's background workers.
export default function SyncPage() {
  const [mode, setMode] = useState<SyncMode>("incremental");
  const [subscriptionId, setSubscriptionId] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [openRunId, setOpenRunId] = useState<string | null>(null);

  const status = useSyncStatus();
  const history = useSyncHistory();
  const metrics = useSyncMetrics();
  const plan = useSyncPlan(previewing ? subscriptionId : null, mode);
  const report = useSyncReport(openRunId);
  const run = useRunSync();

  const subscriptions = status.data?.subscriptions ?? [];
  const activeId = subscriptionId ?? subscriptions[0]?.subscription_id ?? null;

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight">Synchronization</h1>
          <p className="text-sm text-muted-foreground">
            Compare persisted fixtures with your calendar and apply the difference.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as SyncMode)}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm capitalize"
          >
            {MODES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setSubscriptionId(activeId);
              setPreviewing(true);
            }}
            disabled={!activeId}
          >
            <Eye className="h-4 w-4" aria-hidden />
            Preview plan
          </Button>
          <Button
            size="sm"
            onClick={() => run.mutate({ subscriptionId: activeId, mode })}
            disabled={run.isPending}
          >
            <RefreshCw className={cn("h-4 w-4", run.isPending && "animate-spin")} aria-hidden />
            {run.isPending ? "Syncing…" : "Sync now"}
          </Button>
        </div>
      </header>

      {run.isError && <p className="text-sm text-destructive">Synchronization failed.</p>}

      {/* Live report */}
      {run.data?.reports.map((r) => (
        <section key={r.run_id ?? r.subscription_id} className="rounded-lg border p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className={cn("flex items-center gap-2 font-medium", STATUS_STYLE[r.status])}>
              <StatusIcon status={r.status} />
              Sync {r.status}
            </span>
            <span className="text-xs text-muted-foreground">
              {r.api_calls} API call{r.api_calls === 1 ? "" : "s"} · plan {r.plan_ms} ms · total{" "}
              {r.total_ms} ms
            </span>
          </div>
          <dl className="mt-3 grid grid-cols-3 gap-3 sm:grid-cols-6">
            {(
              [
                ["Created", r.created], ["Updated", r.updated], ["Deleted", r.deleted],
                ["Skipped", r.skipped], ["Failed", r.failed],
                ["Dupes prevented", r.duplicates_prevented],
              ] as [string, number][]
            ).map(([label, value]) => (
              <div key={label} className="rounded-md bg-muted/50 p-2 text-center">
                <dt className="text-xs text-muted-foreground">{label}</dt>
                <dd className="text-lg font-semibold tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
          {r.error_summary && (
            <p className="mt-3 text-sm text-destructive">{r.error_summary}</p>
          )}
          {r.plan.mutations === 0 && (
            <p className="mt-3 text-sm text-muted-foreground">
              Nothing to do — your calendar already matches every fixture.
            </p>
          )}
        </section>
      ))}

      {/* Plan preview */}
      {previewing && plan.data && (
        <section className="space-y-3 rounded-lg border p-4">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">Planned actions ({plan.data.mode})</h2>
            <Button variant="ghost" size="sm" onClick={() => setPreviewing(false)}>
              Close
            </Button>
          </div>
          {plan.data.is_empty ? (
            <p className="text-sm text-muted-foreground">
              No changes required. {plan.data.stats.no_op} fixture(s) already in sync.
            </p>
          ) : (
            <ul className="divide-y text-sm">
              {plan.data.actions.slice(0, 50).map((action, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2 py-2">
                  <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-medium capitalize">
                    {action.type}
                  </span>
                  <code className="truncate font-mono text-xs">{action.identity_key.slice(0, 16)}…</code>
                  <span className="text-xs text-muted-foreground">{action.reason}</span>
                  {action.changed_fields.length > 0 && (
                    <span className="text-xs text-muted-foreground">
                      ({action.changed_fields.join(", ")})
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* Subscription status */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Subscriptions</h2>
        {status.isLoading ? (
          <div className="h-20 animate-pulse rounded-md bg-muted" aria-busy />
        ) : subscriptions.length === 0 ? (
          <p className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
            No active subscriptions yet.
          </p>
        ) : (
          <ul className="divide-y rounded-lg border">
            {subscriptions.map((s) => (
              <li key={s.subscription_id} className="flex flex-wrap items-center justify-between gap-3 p-4">
                <div className="min-w-0 text-sm">
                  <p className="font-medium capitalize">{s.status}</p>
                  <p className="text-xs text-muted-foreground">
                    Last synced {fmt(s.last_synced_at)} · next {fmt(s.next_sync_at)}
                  </p>
                </div>
                {s.last_run && (
                  <span className={cn("text-xs font-medium", STATUS_STYLE[s.last_run.status])}>
                    {s.last_run.status}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Metrics */}
      {metrics.data && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">Metrics</h2>
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {(
              [
                ["Runs", metrics.data.metrics.runs],
                ["Calendar writes", metrics.data.metrics.calendar_writes],
                ["No-op %", metrics.data.metrics.no_op_percentage],
                ["Failure rate", metrics.data.metrics.failure_rate],
              ] as [string, number][]
            ).map(([label, value]) => (
              <div key={label} className="rounded-lg border p-3 text-center">
                <dt className="text-xs text-muted-foreground">{label}</dt>
                <dd className="text-xl font-semibold tabular-nums">{String(value ?? 0)}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      {/* History + failure viewer */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">History</h2>
        {history.data?.runs.length === 0 ? (
          <p className="text-sm text-muted-foreground">No runs yet.</p>
        ) : (
          <ul className="divide-y rounded-lg border">
            {history.data?.runs.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => setOpenRunId(openRunId === r.id ? null : r.id)}
                  className="flex w-full flex-wrap items-center justify-between gap-3 p-4 text-left hover:bg-accent/50"
                >
                  <span className={cn("flex items-center gap-2 text-sm font-medium", STATUS_STYLE[r.status])}>
                    <StatusIcon status={r.status} />
                    {r.status}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    +{r.created_count} ~{r.updated_count} -{r.deleted_count} · {fmt(r.started_at)}
                  </span>
                </button>

                {openRunId === r.id && report.data && (
                  <div className="border-t bg-muted/30 p-4 text-xs">
                    {r.error_summary && (
                      <p className="mb-2 text-destructive">{r.error_summary}</p>
                    )}
                    <ul className="space-y-1">
                      {report.data.operations.map((op, i) => (
                        <li key={i} className="flex flex-wrap gap-2">
                          <span className="font-medium capitalize">{op.operation_type}</span>
                          <span
                            className={op.status === "failed" ? "text-destructive" : "text-primary"}
                          >
                            {op.status}
                          </span>
                          {op.message && <span className="text-muted-foreground">{op.message}</span>}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
