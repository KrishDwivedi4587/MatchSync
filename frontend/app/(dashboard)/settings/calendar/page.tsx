"use client";

import { RefreshCw } from "lucide-react";

import {
  CalendarSelector,
  ReconnectPrompt,
} from "@/components/features/calendar-selector";
import { Button } from "@/components/ui/button";
import { LOGIN_URL } from "@/lib/auth/api";
import {
  useCalendarStatus,
  useCalendars,
  useRefreshCalendars,
  useSetDefaultCalendar,
} from "@/lib/calendars/use-calendars";

// Calendar settings: connection status, calendar selection, manual refresh.
// No synchronization controls — those belong to a later stage.
export default function CalendarSettingsPage() {
  const calendars = useCalendars();
  const status = useCalendarStatus();
  const refresh = useRefreshCalendars();
  const setDefault = useSetDefaultCalendar();

  const isLoading = calendars.isLoading || status.isLoading;
  const needsReauth = status.data?.needs_reauth ?? false;

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight">Calendar settings</h1>
        <p className="text-sm text-muted-foreground">
          Choose which calendar MatchSync writes fixtures into.
        </p>
      </header>

      {/* Connection status */}
      <section className="rounded-lg border p-4">
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading calendar status…</p>
        ) : status.isError ? (
          <p className="text-sm text-destructive">Could not load calendar status.</p>
        ) : (
          <dl className="grid gap-2 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-muted-foreground">Google account</dt>
              <dd className="truncate font-medium">{status.data?.account_email ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Selected calendar</dt>
              <dd className="truncate font-medium">
                {status.data?.default_calendar_summary ?? "None selected"}
              </dd>
            </div>
          </dl>
        )}
      </section>

      {needsReauth && <ReconnectPrompt loginUrl={LOGIN_URL} />}

      {/* Calendar list */}
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Your calendars</h2>
          <Button
            variant="outline"
            size="sm"
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending || needsReauth}
          >
            <RefreshCw
              className={`h-4 w-4 ${refresh.isPending ? "animate-spin" : ""}`}
              aria-hidden
            />
            {refresh.isPending ? "Refreshing…" : "Refresh"}
          </Button>
        </div>

        {isLoading ? (
          <div className="space-y-2" aria-busy>
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-[74px] animate-pulse rounded-lg bg-muted" />
            ))}
          </div>
        ) : calendars.isError ? (
          <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm">
            <p className="text-destructive">Could not load your calendars.</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={() => void calendars.refetch()}
            >
              Try again
            </Button>
          </div>
        ) : (
          <CalendarSelector
            calendars={calendars.data?.calendars ?? []}
            selectedId={calendars.data?.default_calendar_id ?? null}
            onSelect={(id) => setDefault.mutate(id)}
            pendingId={setDefault.isPending ? setDefault.variables : null}
            disabled={needsReauth}
          />
        )}

        {setDefault.isError && (
          <p className="text-sm text-destructive">
            Could not select that calendar. It may be read-only or no longer available.
          </p>
        )}
      </section>
    </div>
  );
}
