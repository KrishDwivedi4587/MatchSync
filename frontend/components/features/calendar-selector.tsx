"use client";

import { Check, Lock } from "lucide-react";

import { Button } from "@/components/ui/button";
import { type CalendarDto, isWritable } from "@/lib/calendars/api";
import { cn } from "@/lib/utils";

interface CalendarSelectorProps {
  calendars: CalendarDto[];
  selectedId: string | null;
  onSelect: (calendarId: string) => void;
  pendingId?: string | null;
  disabled?: boolean;
}

/**
 * Calendar picker. Read-only calendars are rendered but not selectable — the
 * backend rejects them, so the UI must not offer them.
 */
export function CalendarSelector({
  calendars,
  selectedId,
  onSelect,
  pendingId,
  disabled,
}: CalendarSelectorProps) {
  if (calendars.length === 0) {
    return (
      <p className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
        No calendars found. Try refreshing.
      </p>
    );
  }

  return (
    <ul className="space-y-2" role="radiogroup" aria-label="Sync target calendar">
      {calendars.map((calendar) => {
        const writable = isWritable(calendar);
        const selected = calendar.id === selectedId;
        const busy = pendingId === calendar.id;

        return (
          <li key={calendar.id}>
            <button
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={!writable || disabled || busy}
              onClick={() => onSelect(calendar.id)}
              className={cn(
                "flex w-full items-center justify-between gap-3 rounded-lg border p-4 text-left transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                selected && "border-primary bg-accent",
                !writable && "cursor-not-allowed opacity-60",
                writable && !selected && "hover:bg-accent/50",
              )}
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium">{calendar.summary}</span>
                  {calendar.is_primary && (
                    <span className="shrink-0 rounded bg-secondary px-1.5 py-0.5 text-xs text-secondary-foreground">
                      Primary
                    </span>
                  )}
                </div>
                <p className="mt-0.5 truncate text-xs text-muted-foreground">
                  {calendar.time_zone ?? "Unknown timezone"}
                </p>
              </div>

              <span className="shrink-0 text-muted-foreground">
                {busy ? (
                  <span className="text-xs">Saving…</span>
                ) : !writable ? (
                  <span className="flex items-center gap-1 text-xs">
                    <Lock className="h-3.5 w-3.5" aria-hidden />
                    Read-only
                  </span>
                ) : selected ? (
                  <Check className="h-5 w-5 text-primary" aria-label="Selected" />
                ) : null}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

interface ReconnectPromptProps {
  loginUrl: string;
}

/** Shown when the stored token lacks the calendar scopes (pre-Stage-5 logins). */
export function ReconnectPrompt({ loginUrl }: ReconnectPromptProps) {
  return (
    <div className="space-y-3 rounded-lg border border-destructive/40 bg-destructive/5 p-4">
      <div>
        <p className="font-medium">Calendar access needs to be reconnected</p>
        <p className="text-sm text-muted-foreground">
          Grant MatchSync permission to read your calendar list and manage events.
        </p>
      </div>
      <Button asChild size="sm">
        <a href={loginUrl}>Reconnect Google Calendar</a>
      </Button>
    </div>
  );
}
