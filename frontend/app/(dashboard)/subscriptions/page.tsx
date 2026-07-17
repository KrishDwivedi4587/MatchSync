"use client";

import { useState } from "react";
import Link from "next/link";
import { Pause, Play, Plus, Trash2 } from "lucide-react";

import { Badge, Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { type SubscriptionDto } from "@/lib/app/api";
import {
  useDeleteSubscription,
  usePauseSubscription,
  useResumeSubscription,
  useSubscriptions,
  useUpdateSubscription,
} from "@/lib/app/use-app";
import { toast } from "@/stores/toast-store";

const FREQUENCIES = [
  { label: "Every hour", value: 60 },
  { label: "Every 6 hours", value: 360 },
  { label: "Every 12 hours", value: 720 },
  { label: "Daily", value: 1440 },
];

function SubscriptionRow({ sub }: { sub: SubscriptionDto }) {
  const pause = usePauseSubscription();
  const resume = useResumeSubscription();
  const remove = useDeleteSubscription();
  const update = useUpdateSubscription();
  const [confirming, setConfirming] = useState(false);

  const paused = sub.status !== "active";

  return (
    <Card>
      <CardContent className="flex flex-wrap items-center justify-between gap-4 p-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p className="truncate font-medium">{sub.label}</p>
            <Badge variant={paused ? "muted" : "success"}>{sub.status}</Badge>
            <Badge variant="muted">{sub.scope}</Badge>
          </div>
          <p className="truncate text-xs text-muted-foreground">
            {sub.sport_name} · {sub.calendar_name}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="sr-only" htmlFor={`freq-${sub.id}`}>
            Sync frequency for {sub.label}
          </label>
          <select
            id={`freq-${sub.id}`}
            value={sub.sync_frequency_minutes}
            onChange={(e) =>
              update.mutate(
                { id: sub.id, body: { sync_frequency_minutes: Number(e.target.value) } },
                { onSuccess: () => toast.success("Frequency updated.") },
              )
            }
            className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          >
            {FREQUENCIES.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
            {!FREQUENCIES.some((f) => f.value === sub.sync_frequency_minutes) && (
              <option value={sub.sync_frequency_minutes}>
                Every {sub.sync_frequency_minutes}m
              </option>
            )}
          </select>

          {paused ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                resume.mutate(sub.id, { onSuccess: () => toast.success("Resumed.") })
              }
              disabled={resume.isPending}
            >
              <Play className="h-3.5 w-3.5" aria-hidden /> Resume
            </Button>
          ) : (
            <Button
              variant="outline"
              size="sm"
              onClick={() => pause.mutate(sub.id, { onSuccess: () => toast.info("Paused.") })}
              disabled={pause.isPending}
            >
              <Pause className="h-3.5 w-3.5" aria-hidden /> Pause
            </Button>
          )}

          {confirming ? (
            <span className="flex items-center gap-1 text-xs">
              <Button
                variant="destructive"
                size="sm"
                onClick={() =>
                  remove.mutate(sub.id, {
                    onSuccess: () => toast.success("Subscription removed."),
                  })
                }
                disabled={remove.isPending}
              >
                Confirm
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </span>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setConfirming(true)}
              aria-label={`Delete ${sub.label}`}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// Manage subscriptions. Removing one drops its events on the next sync (the
// engine prunes events for fixtures no longer covered).
export default function SubscriptionsPage() {
  const subscriptions = useSubscriptions();

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Subscriptions</h1>
          <p className="text-sm text-muted-foreground">
            Competitions and teams synced into your calendar.
          </p>
        </div>
        <Button asChild>
          <Link href="/onboarding">
            <Plus className="h-4 w-4" aria-hidden /> Add subscription
          </Link>
        </Button>
      </header>

      {subscriptions.isLoading ? (
        <div className="space-y-3" aria-busy>
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-20 animate-pulse rounded-xl bg-muted" />
          ))}
        </div>
      ) : subscriptions.isError ? (
        <p className="text-sm text-destructive">Could not load subscriptions.</p>
      ) : (subscriptions.data?.total ?? 0) === 0 ? (
        <Card>
          <CardContent className="p-10 text-center">
            <p className="text-muted-foreground">You have no subscriptions yet.</p>
            <Button asChild className="mt-4">
              <Link href="/onboarding">Get started</Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {subscriptions.data?.subscriptions.map((sub) => (
            <SubscriptionRow key={sub.id} sub={sub} />
          ))}
        </div>
      )}
    </div>
  );
}
