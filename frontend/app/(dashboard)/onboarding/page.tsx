"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, ChevronRight, Loader2 } from "lucide-react";

import { CalendarSelector, ReconnectPrompt } from "@/components/features/calendar-selector";
import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { LOGIN_URL } from "@/lib/auth/api";
import {
  useCalendarStatus,
  useCalendars,
  useSetDefaultCalendar,
} from "@/lib/calendars/use-calendars";
import { useCompetitions, useSports } from "@/lib/sports/use-sports";
import { useBulkSubscribe, useOnboarding } from "@/lib/app/use-app";
import { cn } from "@/lib/utils";
import { toast } from "@/stores/toast-store";

const STEP_LABELS: Record<string, string> = {
  connect_google: "Connect Google",
  grant_calendar: "Grant calendar access",
  select_calendar: "Choose a calendar",
  add_subscription: "Follow competitions",
  first_sync: "First sync",
};
const ORDER = ["connect_google", "grant_calendar", "select_calendar", "add_subscription", "first_sync"];

// Guided onboarding. Progress is server-computed, so refreshing or leaving and
// returning always resumes at the correct step.
export default function OnboardingPage() {
  const router = useRouter();
  const onboarding = useOnboarding();
  const calendarStatus = useCalendarStatus();
  const calendars = useCalendars();
  const setDefault = useSetDefaultCalendar();
  const sports = useSports();
  const bulkSubscribe = useBulkSubscribe();

  const [sportKey, setSportKey] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const competitions = useCompetitions(sportKey);

  const needsReauth = calendarStatus.data?.needs_reauth ?? false;
  const defaultCalendarId = calendars.data?.default_calendar_id ?? null;

  // Redirect out once onboarding is complete.
  useEffect(() => {
    if (onboarding.data?.complete) router.replace("/dashboard");
  }, [onboarding.data?.complete, router]);

  const currentIndex = useMemo(
    () => ORDER.indexOf(onboarding.data?.current_step ?? "connect_google"),
    [onboarding.data?.current_step],
  );

  async function finish() {
    if (picked.size === 0 || !defaultCalendarId || !sportKey) return;
    try {
      await bulkSubscribe.mutateAsync(
        [...picked].map((competitionId) => ({
          calendar_id: defaultCalendarId,
          sport: sportKey,
          scope: "competition" as const,
          competition_id: competitionId,
        })),
      );
      toast.success("You're all set! Syncing your fixtures now.");
      router.replace("/dashboard");
    } catch {
      toast.error("Could not create subscriptions. Please try again.");
    }
  }

  if (onboarding.isLoading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" aria-hidden /> Loading…
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl space-y-8">
      <header className="space-y-2 text-center">
        <h1 className="text-3xl font-bold tracking-tight">Welcome to MatchSync</h1>
        <p className="text-muted-foreground">
          A few quick steps and your fixtures will appear in your calendar automatically.
        </p>
      </header>

      {/* Progress rail */}
      <ol className="flex items-center justify-between" aria-label="Onboarding progress">
        {ORDER.map((key, i) => {
          const done = onboarding.data?.steps.find((s) => s.key === key)?.done ?? false;
          const active = i === currentIndex;
          return (
            <li key={key} className="flex flex-1 flex-col items-center gap-1 text-center">
              <span
                className={cn(
                  "flex h-8 w-8 items-center justify-center rounded-full border text-xs font-medium",
                  done && "border-primary bg-primary text-primary-foreground",
                  active && !done && "border-primary text-primary",
                  !done && !active && "text-muted-foreground",
                )}
                aria-current={active ? "step" : undefined}
              >
                {done ? <Check className="h-4 w-4" aria-hidden /> : i + 1}
              </span>
              <span className="hidden text-xs text-muted-foreground sm:block">
                {STEP_LABELS[key]}
              </span>
            </li>
          );
        })}
      </ol>

      {/* Step 1-3: connect + calendar */}
      {needsReauth || !calendarStatus.data?.has_calendar_scope ? (
        <Card>
          <CardHeader>
            <CardTitle>Connect your Google Calendar</CardTitle>
          </CardHeader>
          <CardContent>
            <ReconnectPrompt loginUrl={LOGIN_URL} />
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Choose your calendar</CardTitle>
          </CardHeader>
          <CardContent>
            <CalendarSelector
              calendars={calendars.data?.calendars ?? []}
              selectedId={defaultCalendarId}
              onSelect={(id) =>
                setDefault.mutate(id, {
                  onSuccess: () => toast.success("Calendar selected."),
                  onError: () => toast.error("That calendar can't be used."),
                })
              }
              pendingId={setDefault.isPending ? setDefault.variables : null}
            />
          </CardContent>
        </Card>
      )}

      {/* Step 4: pick a sport + competitions */}
      {defaultCalendarId && (
        <Card>
          <CardHeader>
            <CardTitle>Follow competitions</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              {sports.data?.map((sport) => (
                <button
                  key={sport.key}
                  type="button"
                  onClick={() => {
                    setSportKey(sport.key);
                    setPicked(new Set());
                  }}
                  className={cn(
                    "rounded-full border px-3 py-1.5 text-sm transition-colors",
                    sportKey === sport.key ? "border-primary bg-accent" : "hover:bg-accent/50",
                  )}
                >
                  {sport.name}
                </button>
              ))}
            </div>

            {sportKey && (
              <div className="space-y-2">
                {competitions.isLoading ? (
                  <p className="text-sm text-muted-foreground">Loading competitions…</p>
                ) : (competitions.data?.length ?? 0) === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    No competitions found. An admin may need to refresh metadata.
                  </p>
                ) : (
                  competitions.data?.map((c) => {
                    const on = picked.has(c.external_id);
                    return (
                      <label
                        key={c.external_id}
                        className={cn(
                          "flex cursor-pointer items-center gap-3 rounded-lg border p-3 text-sm",
                          on && "border-primary bg-accent",
                        )}
                      >
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={() =>
                            setPicked((prev) => {
                              const next = new Set(prev);
                              if (on) {
                                next.delete(c.external_id);
                              } else {
                                next.add(c.external_id);
                              }
                              return next;
                            })
                          }
                          className="h-4 w-4"
                        />
                        <span className="flex-1 font-medium">{c.name}</span>
                        {c.country && <Badge variant="muted">{c.country}</Badge>}
                      </label>
                    );
                  })
                )}
              </div>
            )}

            <div className="flex items-center justify-between pt-2">
              <span className="text-sm text-muted-foreground">
                {picked.size} selected
              </span>
              <Button onClick={finish} disabled={picked.size === 0 || bulkSubscribe.isPending}>
                {bulkSubscribe.isPending ? "Setting up…" : "Finish setup"}
                <ChevronRight className="h-4 w-4" aria-hidden />
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <p className="text-center text-sm">
        <button
          type="button"
          onClick={() => router.push("/dashboard")}
          className="text-muted-foreground underline-offset-4 hover:underline"
        >
          Skip for now
        </button>
      </p>
    </div>
  );
}
