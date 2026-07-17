"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  AlertTriangle,
  CalendarCheck,
  CalendarClock,
  PlayCircle,
  RefreshCw,
} from "lucide-react";

import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { LOGIN_URL } from "@/lib/auth/api";
import { useDashboard, useOnboarding } from "@/lib/app/use-app";
import { useEnqueueSync } from "@/lib/jobs/use-jobs";
import { toast } from "@/stores/toast-store";

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.round(Math.abs(diff) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ${diff < 0 ? "from now" : "ago"}`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ${diff < 0 ? "from now" : "ago"}`;
  return new Date(iso).toLocaleDateString();
}

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </CardContent>
    </Card>
  );
}

// The home screen. Composes calendar, subscriptions, sync, and orchestration
// health — one fan-out to the /dashboard aggregation endpoint.
export default function DashboardPage() {
  const router = useRouter();
  const onboarding = useOnboarding();
  const dashboard = useDashboard();
  const syncNow = useEnqueueSync();

  // New users are guided into onboarding.
  useEffect(() => {
    if (onboarding.data && !onboarding.data.complete) router.replace("/onboarding");
  }, [onboarding.data, router]);

  if (dashboard.isLoading) {
    return (
      <div className="grid gap-4 sm:grid-cols-4" aria-busy>
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-24 animate-pulse rounded-xl bg-muted" />
        ))}
      </div>
    );
  }

  if (dashboard.isError || !dashboard.data) {
    return (
      <Card>
        <CardContent className="p-6 text-center">
          <p className="text-sm text-destructive">Could not load your dashboard.</p>
          <Button variant="outline" size="sm" className="mt-3" onClick={() => dashboard.refetch()}>
            Try again
          </Button>
        </CardContent>
      </Card>
    );
  }

  const d = dashboard.data;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            {d.calendar.account_email ?? "Your account"} · {d.calendar.default_calendar ?? "no calendar"}
          </p>
        </div>
        <Button
          onClick={() =>
            syncNow.mutate(null, {
              onSuccess: () => toast.success("Sync queued."),
              onError: () => toast.error("Could not queue sync."),
            })
          }
          disabled={syncNow.isPending}
        >
          <PlayCircle className="h-4 w-4" aria-hidden />
          {syncNow.isPending ? "Queueing…" : "Sync now"}
        </Button>
      </header>

      {d.calendar.needs_reauth && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4">
            <span className="flex items-center gap-2 text-sm">
              <AlertTriangle className="h-4 w-4 text-destructive" aria-hidden />
              Calendar access needs to be reconnected.
            </span>
            <Button asChild size="sm">
              <a href={LOGIN_URL}>Reconnect Google</a>
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Stat row */}
      <section className="grid gap-4 sm:grid-cols-4">
        <Stat label="Subscriptions" value={d.subscriptions.active} hint={`${d.subscriptions.paused} paused`} />
        <Stat label="Events synced" value={d.sync.created + d.sync.updated} hint={`${d.sync.runs} runs`} />
        <Stat label="Last sync" value={timeAgo(d.sync.last_synced_at)} />
        <Stat label="Next sync" value={timeAgo(d.sync.next_sync_at)} hint={d.sync.overdue ? `${d.sync.overdue} due` : undefined} />
      </section>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Subscriptions */}
        <Card className="lg:col-span-2">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Your subscriptions</CardTitle>
            <Button variant="outline" size="sm" asChild>
              <Link href="/subscriptions">Manage</Link>
            </Button>
          </CardHeader>
          <CardContent>
            {d.subscriptions.items.length === 0 ? (
              <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
                No subscriptions yet.{" "}
                <Link href="/onboarding" className="text-primary underline-offset-4 hover:underline">
                  Add one
                </Link>
                .
              </div>
            ) : (
              <ul className="divide-y">
                {d.subscriptions.items.slice(0, 6).map((s) => (
                  <li key={s.id} className="flex items-center justify-between gap-3 py-3 text-sm">
                    <div className="min-w-0">
                      <p className="truncate font-medium">{s.label}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        {s.sport} · {s.calendar}
                      </p>
                    </div>
                    <Badge variant={s.status === "active" ? "success" : "muted"}>{s.status}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* System status */}
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">System</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-2 text-muted-foreground">
                  <CalendarCheck className="h-4 w-4" aria-hidden /> Calendar
                </span>
                <Badge variant={d.calendar.connected ? "success" : "warning"}>
                  {d.calendar.connected ? "connected" : "disconnected"}
                </Badge>
              </div>
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-2 text-muted-foreground">
                  <RefreshCw className="h-4 w-4" aria-hidden /> Workers
                </span>
                <Badge variant={d.orchestration.workers_online > 0 ? "success" : "warning"}>
                  {d.orchestration.workers_online} online
                </Badge>
              </div>
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-2 text-muted-foreground">
                  <CalendarClock className="h-4 w-4" aria-hidden /> Scheduler
                </span>
                <Badge variant={d.orchestration.scheduler_alive ? "success" : "warning"}>
                  {d.orchestration.scheduler_alive ? "alive" : "down"}
                </Badge>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Providers</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              {d.providers.map((p) => (
                <div key={p.key} className="flex items-center justify-between">
                  <span className="truncate text-muted-foreground">{p.name}</span>
                  <Badge variant={p.status === "healthy" ? "success" : "warning"}>{p.status}</Badge>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
