/** Application-layer API: subscriptions, dashboard, onboarding, account. */

import { apiFetch } from "@/lib/api/client";

export interface SubscriptionDto {
  id: string;
  scope: "sport" | "competition" | "team";
  status: "active" | "paused" | "disabled";
  label: string;
  sport_key: string | null;
  sport_name: string | null;
  competition_name: string | null;
  team_name: string | null;
  calendar_id: string;
  calendar_name: string | null;
  sync_frequency_minutes: number;
  event_prefix: string | null;
  last_synced_at: string | null;
  next_sync_at: string | null;
  created_at: string;
}

export interface CreateSubscriptionBody {
  calendar_id: string;
  sport: string;
  scope: "sport" | "competition" | "team";
  competition_id?: string | null;
  team_id?: string | null;
  sync_frequency_minutes?: number;
  event_prefix?: string | null;
}

export interface OnboardingState {
  complete: boolean;
  current_step: string;
  steps: { key: string; done: boolean }[];
}

export interface DashboardSummary {
  calendar: {
    connected: boolean;
    account_email?: string | null;
    needs_reauth?: boolean;
    default_calendar?: string | null;
    calendar_count?: number;
  };
  subscriptions: {
    total: number;
    active: number;
    paused: number;
    items: {
      id: string;
      label: string;
      scope: string;
      sport: string | null;
      status: string;
      calendar: string | null;
      last_synced_at: string | null;
      next_sync_at: string | null;
    }[];
  };
  sync: {
    runs: number;
    created: number;
    updated: number;
    deleted: number;
    by_status: Record<string, number>;
    last_synced_at: string | null;
    next_sync_at: string | null;
    overdue: number;
  };
  orchestration: { healthy: boolean; workers_online: number; scheduler_alive: boolean };
  providers: { key: string; name: string; status: string; last_success_at: string | null }[];
}

/** Delivery channels the backend's NotificationPreferences schema defines.
 * Kept separate from `reminders_minutes`, which is a sibling field on the same
 * object but not a channel — indexing notifications by this narrower key type
 * (instead of `keyof Preferences["notifications"]`) is what lets TypeScript
 * know the result is always a channel, never `number[]`. */
export type NotificationChannelKey = "email" | "push" | "discord" | "slack" | "browser";

export interface NotificationChannelPreference {
  enabled: boolean;
  target: string | null;
}

export type NotificationChannels = Record<NotificationChannelKey, NotificationChannelPreference>;

export interface Preferences {
  notifications: NotificationChannels & { reminders_minutes: number[] };
  display: { theme: "light" | "dark" | "system" };
}

// --- subscriptions ---------------------------------------------------------
export function fetchSubscriptions(): Promise<{ subscriptions: SubscriptionDto[]; total: number }> {
  return apiFetch("/subscriptions");
}

export function createSubscription(body: CreateSubscriptionBody): Promise<SubscriptionDto> {
  return apiFetch("/subscriptions", { method: "POST", body: JSON.stringify(body) });
}

export function bulkSubscribe(items: CreateSubscriptionBody[]): Promise<{ subscriptions: SubscriptionDto[]; total: number }> {
  return apiFetch("/subscriptions/bulk", { method: "POST", body: JSON.stringify({ items }) });
}

export function updateSubscription(
  id: string,
  body: { sync_frequency_minutes?: number; event_prefix?: string | null; clear_event_prefix?: boolean },
): Promise<SubscriptionDto> {
  return apiFetch(`/subscriptions/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function deleteSubscription(id: string): Promise<void> {
  return apiFetch(`/subscriptions/${id}`, { method: "DELETE" });
}

export function pauseSubscription(id: string): Promise<SubscriptionDto> {
  return apiFetch(`/subscriptions/${id}/pause`, { method: "POST" });
}

export function resumeSubscription(id: string): Promise<SubscriptionDto> {
  return apiFetch(`/subscriptions/${id}/resume`, { method: "POST" });
}

// --- dashboard / onboarding / account --------------------------------------
export function fetchDashboard(): Promise<DashboardSummary> {
  return apiFetch("/dashboard");
}

export function fetchOnboarding(): Promise<OnboardingState> {
  return apiFetch("/onboarding/status");
}

export function fetchPreferences(): Promise<{ preferences: Preferences }> {
  return apiFetch("/me/preferences");
}

export function savePreferences(preferences: Preferences): Promise<{ preferences: Preferences }> {
  return apiFetch("/me/preferences", { method: "PUT", body: JSON.stringify(preferences) });
}

export function updateProfile(body: { display_name?: string; timezone?: string }): Promise<unknown> {
  return apiFetch("/me", { method: "PATCH", body: JSON.stringify(body) });
}
