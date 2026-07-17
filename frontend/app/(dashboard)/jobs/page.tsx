"use client";

import { useState } from "react";
import { Activity, Ban, Play, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { JOB_STATES, type JobDto } from "@/lib/jobs/api";
import {
  useCancelJob,
  useDeadLetter,
  useEnqueueSync,
  useJobs,
  useOrchestrationHealth,
  useQueueDepth,
  useRetryJob,
  useSchedulerStatus,
  useWorkers,
} from "@/lib/jobs/use-jobs";
import { cn } from "@/lib/utils";

const STATE_STYLE: Record<string, string> = {
  succeeded: "text-primary",
  skipped: "text-muted-foreground",
  running: "text-blue-600 dark:text-blue-400",
  retrying: "text-amber-600 dark:text-amber-500",
  queued: "text-muted-foreground",
  failed: "text-destructive",
  dead_letter: "text-destructive",
  cancelled: "text-muted-foreground",
};

const TERMINAL = new Set(["succeeded", "skipped", "cancelled"]);

function fmt(iso: string | null): string {
  return iso ? new Date(iso).toLocaleTimeString() : "—";
}

function Stat({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="rounded-lg border p-3 text-center">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn("text-xl font-semibold tabular-nums", tone)}>{value}</p>
    </div>
  );
}

// Orchestration dashboard: jobs, queues, workers, scheduler, backlog, controls.
// Nothing here performs synchronization — it only schedules and inspects.
export default function JobsPage() {
  const [state, setState] = useState("");

  const jobs = useJobs(state);
  const dead = useDeadLetter();
  const workers = useWorkers();
  const queue = useQueueDepth();
  const scheduler = useSchedulerStatus();
  const health = useOrchestrationHealth();
  const enqueue = useEnqueueSync();
  const retry = useRetryJob();
  const cancel = useCancelJob();

  const renderRow = (job: JobDto) => (
    <li key={job.id} className="flex flex-wrap items-center justify-between gap-3 p-3 text-sm">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className={cn("font-medium capitalize", STATE_STYLE[job.state])}>
            {job.state.replace("_", " ")}
          </span>
          <span className="text-muted-foreground">{job.type}</span>
          <span className="rounded bg-muted px-1.5 py-0.5 text-xs">{job.queue}</span>
        </div>
        <p className="truncate text-xs text-muted-foreground">
          attempt {job.attempts}/{job.max_attempts} · queued {fmt(job.queued_at)}
          {job.duration_seconds !== null && ` · ran ${job.duration_seconds.toFixed(2)}s`}
          {job.error_code && ` · ${job.error_code}`}
        </p>
      </div>
      <div className="flex shrink-0 gap-2">
        {["failed", "dead_letter", "cancelled"].includes(job.state) && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => retry.mutate(job.id)}
            disabled={retry.isPending}
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden />
            Retry
          </Button>
        )}
        {!TERMINAL.has(job.state) && job.state !== "dead_letter" && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => cancel.mutate(job.id)}
            disabled={cancel.isPending}
          >
            <Ban className="h-3.5 w-3.5" aria-hidden />
            Cancel
          </Button>
        )}
      </div>
    </li>
  );

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold tracking-tight">Orchestration</h1>
          <p className="text-sm text-muted-foreground">
            Scheduler, queues, workers, and job control. Updates live.
          </p>
        </div>
        <Button size="sm" onClick={() => enqueue.mutate(null)} disabled={enqueue.isPending}>
          <Play className="h-4 w-4" aria-hidden />
          {enqueue.isPending ? "Queueing…" : "Sync now"}
        </Button>
      </header>

      {/* Platform health */}
      <section className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Stat
          label="Health"
          value={health.data?.healthy ? "OK" : "Degraded"}
          tone={health.data?.healthy ? "text-primary" : "text-destructive"}
        />
        <Stat label="Workers" value={workers.data?.online ?? 0} />
        <Stat
          label="Scheduler"
          value={scheduler.data?.alive ? "Alive" : "Down"}
          tone={scheduler.data?.alive ? "text-primary" : "text-destructive"}
        />
        <Stat label="Queued" value={queue.data?.total ?? 0} />
        <Stat
          label="Stuck"
          value={health.data?.stuck_jobs ?? 0}
          tone={(health.data?.stuck_jobs ?? 0) > 0 ? "text-destructive" : undefined}
        />
      </section>

      {/* Queue depths */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Queues</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Object.entries(queue.data?.depths ?? {}).map(([name, depth]) => (
            <Stat key={name} label={name} value={depth} />
          ))}
        </div>
      </section>

      {/* Workers */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Workers</h2>
        {workers.data?.online === 0 ? (
          <p className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
            No workers online. Scheduled synchronization will not run.
          </p>
        ) : (
          <ul className="divide-y rounded-lg border text-sm">
            {workers.data?.workers.map((worker) => (
              <li key={worker.name} className="flex items-center justify-between p-3">
                <span className="flex items-center gap-2 font-medium">
                  <Activity className="h-4 w-4 text-primary" aria-hidden />
                  {worker.name}
                </span>
                <span className="text-xs text-muted-foreground">
                  seen {new Date(worker.seen_at).toLocaleTimeString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Scheduler definitions */}
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Schedule</h2>
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th className="p-3">Job</th>
                <th className="p-3">Cron</th>
                <th className="p-3">Status</th>
                <th className="p-3">Last run</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {scheduler.data?.jobs.map((job) => (
                <tr key={job.key}>
                  <td className="p-3 font-medium">{job.name}</td>
                  <td className="p-3 font-mono text-xs">{job.schedule}</td>
                  <td className="p-3 capitalize">{job.status}</td>
                  <td className="p-3 text-muted-foreground">{fmt(job.last_run_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Dead letter */}
      {(dead.data?.total ?? 0) > 0 && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold text-destructive">
            Dead letter ({dead.data?.total})
          </h2>
          <ul className="divide-y rounded-lg border border-destructive/40">
            {dead.data?.jobs.map(renderRow)}
          </ul>
        </section>
      )}

      {/* Jobs */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Jobs</h2>
          <select
            value={state}
            onChange={(e) => setState(e.target.value)}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm capitalize"
          >
            {JOB_STATES.map((s) => (
              <option key={s} value={s}>
                {s === "" ? "All states" : s.replace("_", " ")}
              </option>
            ))}
          </select>
        </div>

        {jobs.isLoading ? (
          <div className="h-24 animate-pulse rounded-md bg-muted" aria-busy />
        ) : (jobs.data?.total ?? 0) === 0 ? (
          <p className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
            No jobs yet.
          </p>
        ) : (
          <ul className="divide-y rounded-lg border">{jobs.data?.jobs.map(renderRow)}</ul>
        )}
      </section>
    </div>
  );
}
